#!/usr/bin/env node
// Place 2-D points so that no triple is visibly colinear.
//
// Problem: given a set of fixed point ids + a starting position guess,
// find x/y for each id such that for every triple (a, b, c), the
// perpendicular distance from c to the line ab (when c projects between
// a and b along that line) is at least TARGET pixels — and no two
// points are closer than MIN_PAIR pixels (so the per-node spokes the
// netgen pages draw at radius + spoke_len don't overlap a neighbour).
//
// Strategy: simulated annealing. Multiple restarts seed from random
// perturbations of the supplied initial config, then a local nudge
// chain anneals against a penalty that grows cubically as a triple's
// perpendicular distance falls below TARGET. The best seen config
// (highest worst-case distance, then lowest total penalty) wins.
//
// Usage:
//   node optim_positions.mjs <input.json> [options]
//
//   <input.json> shape:
//     {
//       "positions": { "1": {"x": -126, "y": -180}, ... },
//       "target": 22,        // px; threshold below which a triple is "colinear"
//       "minPair": 58,       // px; minimum allowed pairwise distance
//       "maxDisplace": 70,   // px; cap on |position - originalPosition|
//       "restarts": 60,
//       "iters": 200000
//     }
//
// Output: writes the best positions to stdout as JSON. Non-zero exit
// code if no feasible config was found.

import { readFileSync } from "fs";

function loadInput(path) {
  const raw = JSON.parse(readFileSync(path, "utf8"));
  if (!raw.positions) throw new Error("input must have .positions");
  return {
    orig: raw.positions,
    target:      raw.target      != null ? raw.target      : 22,
    minPair:     raw.minPair     != null ? raw.minPair     : 58,
    maxDisplace: raw.maxDisplace != null ? raw.maxDisplace : 70,
    restarts:    raw.restarts    != null ? raw.restarts    : 60,
    iters:       raw.iters       != null ? raw.iters       : 200000,
    nudgePx:     raw.nudgePx     != null ? raw.nudgePx     : 8,
    seedPerturb: raw.seedPerturb != null ? raw.seedPerturb : 40,
    initialTemp: raw.initialTemp != null ? raw.initialTemp : 200,
    coolRate:    raw.coolRate    != null ? raw.coolRate    : 0.99998,
    tBetween:    raw.tBetween    != null ? raw.tBetween    : [0.05, 0.95],
  };
}

function toArr(positions) {
  // Internal representation: { id: [x, y] }
  const o = {};
  for (const k of Object.keys(positions)) {
    o[k] = [positions[k].x, positions[k].y];
  }
  return o;
}
function fromArr(P) {
  const o = {};
  for (const k of Object.keys(P)) o[k] = { x: P[k][0], y: P[k][1] };
  return o;
}
function copy(P) {
  const o = {};
  for (const k of Object.keys(P)) o[k] = P[k].slice();
  return o;
}

// Score a config. Returns the worst-case perpendicular distance, the
// number of triples below TARGET, the cubic-deficit penalty driving
// the SA, and the minimum pairwise distance.
function makeScorer(target, tLo, tHi) {
  return function score(P) {
    const ids = Object.keys(P);
    let worst = Infinity, count = 0, penalty = 0, minPair = Infinity;
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
      const a = P[ids[i]], b = P[ids[j]];
      const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (d < minPair) minPair = d;
    }
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) for (let k = 0; k < ids.length; k++) {
      if (k === i || k === j) continue;
      const a = P[ids[i]], b = P[ids[j]], c = P[ids[k]];
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const L = Math.hypot(dx, dy);
      if (L < 1) continue;
      const cross = Math.abs(dx * (a[1] - c[1]) - dy * (a[0] - c[0])) / L;
      const t = ((c[0] - a[0]) * dx + (c[1] - a[1]) * dy) / (L * L);
      // Skip triples where C is not between A and B along the line.
      // Off-segment c never reads as "edge through node c" in the viz.
      if (t > tLo && t < tHi) {
        if (cross < worst) worst = cross;
        if (cross < target) {
          count++;
          const d = target - cross;
          penalty += d * d * d;
        }
      }
    }
    return { worst, count, penalty, minPair };
  };
}

function within(P, ORIG, max) {
  for (const k of Object.keys(P)) {
    const dx = P[k][0] - ORIG[k][0], dy = P[k][1] - ORIG[k][1];
    if (Math.hypot(dx, dy) > max) return false;
  }
  return true;
}

function feasibleSeed(ORIG, score, minPair, perturb) {
  for (let attempt = 0; attempt < 400; attempt++) {
    const P = {};
    for (const k of Object.keys(ORIG)) {
      P[k] = [
        Math.round(ORIG[k][0] + (Math.random() * 2 - 1) * perturb),
        Math.round(ORIG[k][1] + (Math.random() * 2 - 1) * perturb),
      ];
    }
    if (score(P).minPair >= minPair) return P;
  }
  return null;
}

function optimize(opts) {
  const ORIG = toArr(opts.orig);
  const score = makeScorer(opts.target, opts.tBetween[0], opts.tBetween[1]);
  let best = null, bestScore = { penalty: Infinity, worst: 0 };
  for (let restart = 0; restart < opts.restarts; restart++) {
    let cur;
    if (restart === 0) {
      cur = copy(ORIG);
      // Push to feasibility if seed isn't.
      let s = score(cur);
      let attempts = 0;
      while (s.minPair < opts.minPair && attempts < 200) {
        const trial = copy(cur);
        const ids = Object.keys(trial);
        const id = ids[Math.floor(Math.random() * ids.length)];
        trial[id] = [
          Math.round(trial[id][0] + (Math.random() * 2 - 1) * 15),
          Math.round(trial[id][1] + (Math.random() * 2 - 1) * 15),
        ];
        const ts = score(trial);
        if (ts.minPair > s.minPair && within(trial, ORIG, opts.maxDisplace)) {
          cur = trial; s = ts;
        }
        attempts++;
      }
      if (s.minPair < opts.minPair) continue;
    } else {
      cur = feasibleSeed(ORIG, score, opts.minPair, opts.seedPerturb);
      if (!cur) continue;
      // Snap into displacement budget.
      if (!within(cur, ORIG, opts.maxDisplace)) {
        for (const k of Object.keys(cur)) {
          const dx = cur[k][0] - ORIG[k][0], dy = cur[k][1] - ORIG[k][1];
          const d = Math.hypot(dx, dy);
          if (d > opts.maxDisplace) {
            cur[k] = [
              Math.round(ORIG[k][0] + dx * opts.maxDisplace / d),
              Math.round(ORIG[k][1] + dy * opts.maxDisplace / d),
            ];
          }
        }
      }
    }
    let curScore = score(cur);
    if (curScore.minPair < opts.minPair) continue;
    let temp = opts.initialTemp;
    for (let iter = 0; iter < opts.iters; iter++) {
      const trial = copy(cur);
      const ids = Object.keys(trial);
      const n = 1 + Math.floor(Math.random() * 3);
      for (let i = 0; i < n; i++) {
        const id = ids[Math.floor(Math.random() * ids.length)];
        trial[id] = [
          Math.round(trial[id][0] + (Math.random() * 2 - 1) * opts.nudgePx),
          Math.round(trial[id][1] + (Math.random() * 2 - 1) * opts.nudgePx),
        ];
      }
      if (!within(trial, ORIG, opts.maxDisplace)) continue;
      const s = score(trial);
      if (s.minPair < opts.minPair) continue;
      const dE = s.penalty - curScore.penalty;
      if (dE < 0 || Math.random() < Math.exp(-dE / temp)) {
        cur = trial; curScore = s;
        if (s.worst > bestScore.worst ||
            (s.worst === bestScore.worst && s.penalty < bestScore.penalty)) {
          best = copy(trial); bestScore = s;
        }
      }
      temp *= opts.coolRate;
    }
  }
  return { positions: best, score: bestScore };
}

if (process.argv[2] && import.meta.url === `file://${process.argv[1]}`) {
  const opts = loadInput(process.argv[2]);
  const result = optimize(opts);
  if (!result.positions) {
    console.error("no feasible config found within budget");
    process.exit(1);
  }
  const out = {
    positions: fromArr(result.positions),
    score: {
      worst: +result.score.worst.toFixed(2),
      countUnderTarget: result.score.count,
      minPair: +result.score.minPair.toFixed(2),
    },
  };
  console.log(JSON.stringify(out, null, 2));
}

export { optimize, makeScorer };
