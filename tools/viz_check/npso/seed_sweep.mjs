// One-shot sweep: pick the seed for the nPSO page's initial embedding
// that (a) places at least one node in every angular sector and (b)
// maximises average pairwise hyperbolic distance among rank pairs.
// Runs the page's npso_kernel.js verbatim under a minimal d3 shim and
// prints the winning seed to stdout. Bake the result into npso.html.
//
// Run:
//   node tools/viz_check/npso/seed_sweep.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const KERNEL_PATH = resolve(here, "../../../vltanh.github.io/netgen/js/npso_kernel.js");

// ── d3 shim: just the two RNG entrypoints the kernel touches. ────────
// randomLcg matches d3-random@3 byte-for-byte. The earlier shim used
// `s & 0x7fffffff / 0x80000000`, which throws away the sign bit and
// divides by 2^31; d3 actually does `(state >>> 0) / 0x100000000` —
// keeps the full 32 bits and divides by 2^32. The wrong shim made the
// sweep score embeddings the page would never produce.
const LCG_EPS = 1 / 0x100000000;
function randomLcg(seed) {
  let state = (0 <= seed && seed < 1 ? seed / LCG_EPS : Math.abs(seed)) | 0;
  return () => {
    state = (0x19660D * state + 0x3C6EF35F) | 0;
    return LCG_EPS * (state >>> 0);
  };
}
function makeRandomNormalFactory(source) {
  function randomNormal(mu, sigma) {
    let x, r;
    mu = mu == null ? 0 : +mu;
    sigma = sigma == null ? 1 : +sigma;
    return function () {
      let y;
      if (x != null) { y = x; x = null; }
      else do {
        x = source() * 2 - 1;
        y = source() * 2 - 1;
        r = x * x + y * y;
      } while (!r || r > 1);
      return mu + sigma * y * Math.sqrt(-2 * Math.log(r) / r);
    };
  }
  randomNormal.source = makeRandomNormalFactory;
  return randomNormal;
}
const d3 = {
  randomLcg,
  randomNormal: makeRandomNormalFactory(Math.random),
};

// ── Load the page's npso_kernel.js inside a vm context. ─────────────
const kernelSrc = readFileSync(KERNEL_PATH, "utf8");
const sandbox = { d3, window: {}, console };
vm.createContext(sandbox);
vm.runInContext(kernelSrc, sandbox);
const NPSOKernel = sandbox.window.NPSOKernel;
if (!NPSOKernel) {
  console.error("Failed to load NPSOKernel from", KERNEL_PATH);
  process.exit(1);
}

// ── Parameters for the small20 fixture / npso default. ──────────────
// PROF_DEGREES come from a 40-edge graph on 20 nodes → meanDeg=4 → M=2.
// outlier_mode=singleton expands two outliers into singleton clusters.
// Sorted cluster sizes are [8, 6, 4, 1, 1] for C1, C2, C3, {19}, {20}.
const N = 20;
const m = 2;
const gamma = 2.73;
const C = 5;
const mixingProportions = [0.4, 0.3, 0.2, 0.05, 0.05];

// ── Sweep. ──────────────────────────────────────────────────────────
//
// Score policy:
//   - Hard reject: any cluster ends up empty.
//   - Hard reject: a singleton-target cluster (mixing prop < 0.10) ends
//     up with anything other than exactly 2 nodes. We want some
//     imbalance vs. nominal: the singleton sectors should visibly hold
//     a tiny but non-trivial pair, not a single node — that pair is
//     what makes the "outliers attract a few neighbours" story land.
//   - Hard reject: a non-singleton cluster with < 2 nodes (can't measure
//     intra-cluster spread on a singleton).
//   - Score = w_intra · avg_intra_dist (per-cluster, then averaged
//     across clusters with ≥ 2 nodes) + w_inter · avg_inter_dist.
//     Imbalance penalty dropped — user wants the small overall skew
//     that comes from the GMM's natural variance.
const MAX_SEEDS = 65536;
const W_INTRA = 1.0;
const W_INTER = 0.3;
const SINGLETON_THRESHOLD = 0.10;
const SINGLETON_TARGET_COUNT = 2;
const NON_SINGLETON_MIN_COUNT = 2;
const singletonClusters = new Set();
mixingProportions.forEach((p, idx) => {
  if (p < SINGLETON_THRESHOLD) singletonClusters.add(idx + 1);
});
const expectedCounts = mixingProportions.map(p => p * N);

function clusterCounts(assigned) {
  const counts = new Array(C + 1).fill(0);
  for (let i = 0; i < N; i++) counts[assigned[i]]++;
  return counts;
}

function distFor(emb, a, b) {
  if (a === b) return 0;
  const lo = Math.min(a, b), hi = Math.max(a, b);
  const d = emb.DISTS[`${lo}-${hi}`];
  return (typeof d === "number" && isFinite(d)) ? d : null;
}

function scoreEmb(emb) {
  const counts = clusterCounts(emb.ASSIGNED);
  // Hard target: singletons split as {1, 2} (one of each), non-singleton
  // counts each in [5, 7]. With three non-singletons totalling 17 and
  // singletons totalling 3, the sum lands on N=20 by construction.
  const singletonCounts = [];
  for (let c = 1; c <= C; c++) {
    if (counts[c] === 0) return null;
    if (singletonClusters.has(c)) {
      singletonCounts.push(counts[c]);
    } else {
      if (counts[c] < 5 || counts[c] > 7) return null;
    }
  }
  singletonCounts.sort((a, b) => a - b);
  if (singletonCounts.length !== 2 ||
      singletonCounts[0] !== 1 || singletonCounts[1] !== 2) {
    return null;
  }
  // Intra-cluster: avg pairwise distance within each cluster (ranks
  // 1..N use 1-based indices in DISTS, so feed those). Then average
  // across clusters that have at least one pair.
  const byCluster = new Array(C + 1).fill(null).map(() => []);
  for (let r = 1; r <= N; r++) byCluster[emb.ASSIGNED[r - 1]].push(r);
  let intraSum = 0, intraGroups = 0;
  for (let c = 1; c <= C; c++) {
    const ranks = byCluster[c];
    if (ranks.length < 2) continue;
    let s = 0, n = 0;
    for (let i = 0; i < ranks.length; i++) {
      for (let j = i + 1; j < ranks.length; j++) {
        const d = distFor(emb, ranks[i], ranks[j]);
        if (d != null) { s += d; n++; }
      }
    }
    if (n > 0) { intraSum += s / n; intraGroups++; }
  }
  const intraAvg = intraGroups > 0 ? intraSum / intraGroups : 0;
  // Inter-cluster: pairs across different clusters.
  let interS = 0, interN = 0;
  for (let a = 1; a < N; a++) {
    for (let b = a + 1; b <= N; b++) {
      if (emb.ASSIGNED[a - 1] === emb.ASSIGNED[b - 1]) continue;
      const d = distFor(emb, a, b);
      if (d != null) { interS += d; interN++; }
    }
  }
  const interAvg = interN > 0 ? interS / interN : 0;
  // Track imbalance for the report only — not used in the score now.
  let imbalance = 0;
  for (let c = 1; c <= C; c++) {
    imbalance += Math.abs(counts[c] - expectedCounts[c - 1]);
  }
  return {
    score: W_INTRA * intraAvg + W_INTER * interAvg,
    intraAvg,
    interAvg,
    imbalance,
    counts: counts.slice(1),
  };
}

let best = null;
for (let s = 1; s <= MAX_SEEDS; s++) {
  let emb;
  try {
    emb = NPSOKernel.computeEmbedding({ N, m, gamma, C, mixingProportions, seed: s });
  } catch (e) {
    continue;
  }
  const r = scoreEmb(emb);
  if (r == null) continue;
  if (!best || r.score > best.score) {
    best = { seed: s, ...r };
  }
}

console.log(JSON.stringify({
  seed: best ? best.seed : -1,
  score: best ? best.score : null,
  intraAvg: best ? best.intraAvg : null,
  interAvg: best ? best.interAvg : null,
  imbalance: best ? best.imbalance : null,
  clusterCounts: best ? best.counts : null,
  expectedCounts,
  scanned: MAX_SEEDS,
}, null, 2));
