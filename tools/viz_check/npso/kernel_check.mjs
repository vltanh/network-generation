// Two flavors live in this file:
//
// 1. Node port of the impl-3 walker in vltanh.github.io/netgen/npso.html.
//    Reads a fixture JSON from stdin (N, m, gamma, optional c +
//    mixing_proportions + seed + replay), prints achieved invariants on
//    stdout. Pair-by-pair logic mirrors edgesAtT() in the page; PRNG is
//    the same LCG d3.randomLcg uses (verbatim).
//
// 2. Faithful-replay against MATLAB nPSO_model. Pass `mode: "matlab_replay"`
//    plus the {N, m, T, gamma, C, mu, trace} blob from
//    tools/viz_check/npso/instrumented/runner.py. Produces edges and comm
//    that byte-equal the canonical MATLAB sampler under the same seed.
//
// Fixture shape:
//   {
//     "N": 20, "m": 2, "gamma": 2.73, "seed": 1,
//     "c": 5,                                       // optional, default 0 -> uniform angle
//     "mixing_proportions": [0.4, 0.3, 0.2, 0.05, 0.05],  // optional, len = c
//     "replay": [[u11, u12], [u21, u22], ...]       // optional; if given,
//                                                   // sampler bypasses its
//                                                   // edge-PRNG and consumes
//                                                   // these per-arrival uniforms.
//   }
//
// Invariants the JS algorithm guarantees by construction (and that the
// harness-side checker re-verifies):
//   - per arrival t in [m+2, N]: exactly m predecessor edges.
//   - per arrival t in [2, m+1]: exactly t-1 predecessor edges.
//   - simple graph: no self-loops, no parallel edges.
//   - edges (i, j) always satisfy i < j (canonicalised on emit).
//
// Run:
//   node tools/npso/kernel_check.mjs < fixture.json

function lcg(seed) {
  // Match d3-random's randomLcg verbatim (32-bit signed LCG).
  let s = (seed > 0 && seed < 1) ? Math.floor(seed * 0xfffff) : Math.abs(seed) | 0;
  return () => {
    s = (Math.imul(s, 0x19660d) + 0x3c6ef35f) | 0;
    return ((s & 0x7fffffff) / 0x80000000);
  };
}

// Box-Muller normal sampler driven by an LCG-style rng.
function randomNormalSource(rng) {
  return (mu, sigma) => {
    let u1 = 0, u2 = 0;
    while (u1 === 0) u1 = rng();
    while (u2 === 0) u2 = rng();
    const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    return mu + sigma * z;
  };
}

// Wrap an angle into [0, 2π).
function wrapAngle(theta) {
  let t = theta % (2 * Math.PI);
  if (t < 0) t += 2 * Math.PI;
  return t;
}

// R(T) closed form. β=1 branch (pure power-law, γ=2) handled as the
// L'Hopital limit; mirrors run_npso.m + edgesAtT in the page.
function R_of_T(T, m, N, gamma) {
  const beta = 1 / (gamma - 1);
  const log_N = Math.log(N);
  const s = Math.sin(Math.PI * T);
  if (s <= 0) return Infinity;
  if (Math.abs(beta - 1) < 1e-9) {
    return 2 * log_N - 2 * Math.log((2 * T * log_N) / (s * m));
  }
  const num = 2 * T * (1 - Math.exp(-(1 - beta) * log_N));
  const den = s * m * (1 - beta);
  return 2 * log_N - 2 * Math.log(num / den);
}

// Hyperbolic distance between two nodes given their hyperbolic radii
// and angular separation Δθ. Formula:
//   cosh d = cosh r_u · cosh r_v − sinh r_u · sinh r_v · cos Δθ
// Matches the page's DISTS computation and run_npso.m's hyperbolic_dist.
function hyperbolicDist(ru, rv, dtheta) {
  let dth = Math.abs(dtheta);
  if (dth > Math.PI) dth = 2 * Math.PI - dth;
  const val = Math.cosh(ru) * Math.cosh(rv) - Math.sinh(ru) * Math.sinh(rv) * Math.cos(dth);
  if (val <= 1) return Math.abs(ru - rv);
  return Math.acosh(val);
}

function computeEmbedding(N, m, gamma, c, mixingProportions, seed) {
  const beta = 1 / (gamma - 1);
  const log_N = Math.log(N);
  const rng = lcg(seed);
  const normal = randomNormalSource(rng);
  const pickU = rng;

  // Per-cluster μ_k = 2π·k / C, σ = 2π / (6·C) (paper default).
  const mu = [];
  for (let k = 0; k < c; k++) mu.push((2 * Math.PI * k) / c);
  const sigma = c > 0 ? (2 * Math.PI) / (6 * c) : 0;

  let cum = [];
  if (c > 0 && mixingProportions && mixingProportions.length === c) {
    let acc = 0;
    for (const p of mixingProportions) { acc += p; cum.push(acc); }
  }

  function pickComponent() {
    const u = pickU();
    for (let k = 0; k < cum.length - 1; k++) if (u <= cum[k]) return k;
    return cum.length - 1;
  }

  const POLAR = []; // index 0 = arrival 1, etc.
  const ASSIGNED = [];
  for (let i = 0; i < N; i++) {
    const t_i = i + 1;
    let theta;
    if (c > 0) {
      const k = pickComponent();
      theta = wrapAngle(normal(mu[k], sigma));
    } else {
      theta = wrapAngle(rng() * 2 * Math.PI);
    }
    // Argmin reassignment to nearest mu (page convention).
    let bestK = 0, bestD = Infinity;
    for (let k = 0; k < c; k++) {
      let d = Math.abs(theta - mu[k]);
      if (d > Math.PI) d = 2 * Math.PI - d;
      if (d < bestD) { bestD = d; bestK = k; }
    }
    const r_hyp = 2 * beta * Math.log(t_i) + 2 * (1 - beta) * log_N;
    POLAR.push({ r_hyp, theta });
    ASSIGNED.push(bestK);
  }

  // Pairwise hyperbolic distances, indexed by (i, j) with i < j (0-based).
  const DISTS = {};
  for (let i = 0; i < N; i++) {
    for (let j = i + 1; j < N; j++) {
      DISTS[`${i}-${j}`] = hyperbolicDist(POLAR[i].r_hyp, POLAR[j].r_hyp,
                                          POLAR[i].theta - POLAR[j].theta);
    }
  }

  // Per-arrival uniforms for impl-3 weighted sampling without
  // replacement. Separate seed stream so the pair draws don't alias
  // with the angular draws (matches the page exactly).
  const rngEdge = lcg(seed * 31 + 7);
  const U_NODE = [];
  for (let i = 0; i < N; i++) {
    const us = [];
    for (let k = 0; k < m; k++) us.push(rngEdge());
    U_NODE.push(us);
  }
  return { POLAR, DISTS, U_NODE, ASSIGNED };
}

// Implementation 3 (Muscoloni & Cannistraci 2018 page 7): each new node
// arriving at time t_i picks m targets from the i earlier arrivals
// without replacement, weighted by the Fermi-Dirac probability
// p(d_ij, R(T)) = 1 / (1 + exp((d_ij − R) / (2T))). For arrivals with
// fewer than m predecessors, connect to all predecessors. Pre-drawn
// per-node uniforms allow continuous T scrub on the page.
function edgesImpl3(T, emb, m, N, gamma, replay) {
  const R = R_of_T(T, m, N, gamma);
  const inv2T = 1 / (2 * T);
  const out = [];
  for (let i = 1; i < N; i++) {
    const candidates = [];
    const weights = [];
    for (let j = 0; j < i; j++) {
      const d = emb.DISTS[`${j}-${i}`];
      const p = 1 / (1 + Math.exp((d - R) * inv2T));
      candidates.push(j);
      weights.push(p);
    }
    if (candidates.length <= m) {
      candidates.forEach(jj => out.push([Math.min(i, jj), Math.max(i, jj)]));
      continue;
    }
    const us = replay ? replay[i] : emb.U_NODE[i];
    for (let k = 0; k < m; k++) {
      let sum = 0;
      for (const w of weights) sum += w;
      if (sum <= 0) break;
      const u = us[k] * sum;
      let acc = 0, pick = 0;
      for (let j = 0; j < weights.length; j++) {
        acc += weights[j];
        if (acc >= u) { pick = j; break; }
      }
      out.push([Math.min(i, candidates[pick]), Math.max(i, candidates[pick])]);
      candidates.splice(pick, 1);
      weights.splice(pick, 1);
    }
  }
  return out;
}

function checkPerArrivalM(edges, N, m) {
  const byArrival = {};
  for (const [a, b] of edges) {
    if (a === b) continue;
    const arrival = Math.max(a, b);
    const pred = Math.min(a, b);
    if (!byArrival[arrival]) byArrival[arrival] = new Set();
    byArrival[arrival].add(pred);
  }
  const bad = [];
  for (let t = 1; t < N; t++) {
    const expected = (t < m + 1) ? t : m;
    const actual = (byArrival[t] || new Set()).size;
    if (actual !== expected) bad.push([t, expected, actual]);
  }
  return { ok: bad.length === 0, bad };
}

function checkSimpleGraph(edges) {
  let selfLoops = 0;
  const seen = new Set();
  let parallels = 0;
  for (const [a, b] of edges) {
    if (a === b) { selfLoops++; continue; }
    const lo = Math.min(a, b), hi = Math.max(a, b);
    const key = `${lo}|${hi}`;
    if (seen.has(key)) parallels++;
    else seen.add(key);
  }
  return { ok: selfLoops === 0 && parallels === 0, selfLoops, parallels };
}

// ── faithful MATLAB replay ─────────────────────────────────────
// Consumes a trace produced by tools/viz_check/npso/instrumented/runner.py.
// The trace gives N angles + (N-m-1) per-arrival pick vectors. We compute
// coords (radial + angular), comm (arg-min cluster), and replay each
// arrival's m predecessors from the trace.
//
// Output: edges sorted lex (canonicalised (min,max)) + comm; matches the
// MATLAB sampler byte-for-byte under the same seed.
function matlabReplay(payload) {
  const { N, m, T, gamma, C, mu, trace } = payload;
  const angles = trace.angles;
  const picks = trace.picks;
  const beta = 1 / (gamma - 1);
  const log_N = Math.log(N);

  // comm = arg min |angle - mu_k| (with wrap-around). Mirrors MATLAB's
  // min(pi - abs(pi - abs(theta - mu_k))) form line-for-line.
  const comm = new Array(N);
  for (let i = 0; i < N; i++) {
    let best = 0, bestVal = Infinity;
    for (let k = 0; k < C; k++) {
      let raw = Math.abs(angles[i] - mu[k]);
      if (raw > Math.PI) raw = 2 * Math.PI - raw;
      // MATLAB: pi - abs(pi - abs(...))
      const distVal = Math.PI - Math.abs(Math.PI - raw);
      if (distVal < bestVal) { bestVal = distVal; best = k; }
    }
    comm[i] = best + 1; // MATLAB 1-based
  }

  const edges = [];
  let pickCursor = 0;
  for (let t = 2; t <= N; t++) {
    if (t - 1 <= m) {
      for (let j = 1; j <= t - 1; j++) {
        edges.push([Math.min(t, j), Math.max(t, j)]);
      }
    } else {
      const picksT = picks[pickCursor++];
      for (const j of picksT) {
        edges.push([Math.min(t, j), Math.max(t, j)]);
      }
    }
  }
  if (pickCursor !== picks.length) {
    throw new Error(`pick trace not fully consumed: ${pickCursor}/${picks.length}`);
  }
  edges.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  return { edges, comm, picks_consumed: pickCursor, picks_total: picks.length };
}

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf-8");
}

const src = await readStdin();
const fx = JSON.parse(src);

if (fx.mode === "matlab_replay") {
  const out = matlabReplay(fx);
  process.stdout.write(JSON.stringify(out) + "\n");
} else {
  const N = fx.N;
  const m = fx.m;
  const gamma = fx.gamma;
  const c = fx.c || 0;
  const rho = fx.mixing_proportions || [];
  const seed = fx.seed || 1;
  const T_target = fx.T != null ? fx.T : 0.3;
  const replay = Array.isArray(fx.replay) ? fx.replay : null;

  const emb = computeEmbedding(N, m, gamma, c, rho, seed);

  const edges = edgesImpl3(T_target, emb, m, N, gamma, replay);
  const arr = checkPerArrivalM(edges, N, m);
  const sim = checkSimpleGraph(edges);

  const out = {
    seed,
    T: T_target,
    mode: replay ? "replay" : "rng",
    edges,
    edges_count: edges.length,
    per_arrival_m_ok: arr.ok,
    per_arrival_m_bad: arr.bad,
    simple_ok: sim.ok,
    self_loops: sim.selfLoops,
    parallels: sim.parallels,
    U_NODE: emb.U_NODE,
  };
  process.stdout.write(JSON.stringify(out) + "\n");
}
