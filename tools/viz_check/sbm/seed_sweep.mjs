// Sweep d3.randomLcg seeds for the SBM netgen page (sbm.html) to find
// initial seeds whose post-pipeline output (sample → simplify → match-
// degree) leaves at least one real cluster (C1/C2/C3) internally
// disconnected, while also accumulating many simplify drops and many
// match-degree top-up edges.
//
// Loads the page's shared.js + profile_kernel.js + sbm_kernel.js +
// match_degree_kernel.js inside a vm context behind minimal d3 +
// document stubs, then runs the same pipeline the page runs.
//
// Run:
//   node tools/viz_check/sbm/seed_sweep.mjs
//
// Reports the top-K seeds by composite score.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const NETGEN = resolve(here, "../../../vltanh.github.io/netgen");
const SHARED_PATH      = resolve(NETGEN, "shared.js");
const PROFILE_PATH     = resolve(NETGEN, "js/profile_kernel.js");
const SBM_PATH         = resolve(NETGEN, "js/sbm_kernel.js");
const MATCH_PATH       = resolve(NETGEN, "js/match_degree_kernel.js");

// ── d3 shim: randomLcg byte-for-byte d3-random@3, plus stubs for the
// helpers shared.js touches at module-load time (none, in practice;
// d3.select / d3.forceSimulation are only called when widgets mount).
const LCG_EPS = 1 / 0x100000000;
function randomLcg(seed) {
  let state = (0 <= seed && seed < 1 ? seed / LCG_EPS : Math.abs(seed)) | 0;
  return () => {
    state = (0x19660D * state + 0x3C6EF35F) | 0;
    return LCG_EPS * (state >>> 0);
  };
}
const d3Stub = new Proxy({ randomLcg }, {
  get(target, prop) {
    if (prop in target) return target[prop];
    return () => {};
  },
});

// `document` deliberately omitted so shared.js's `if (typeof document
// !== "undefined")` guards short-circuit at module-load time.
const sandbox = {
  d3: d3Stub,
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
};
sandbox.window = sandbox;
sandbox.global = sandbox;
vm.createContext(sandbox);

for (const p of [SHARED_PATH, PROFILE_PATH, SBM_PATH, MATCH_PATH]) {
  const src = readFileSync(p, "utf8");
  vm.runInContext(src, sandbox, { filename: p });
}

const NETGEN_DATA = sandbox.NETGEN;
const ProfileKernel = sandbox.ProfileKernel;
const SBMKernel = sandbox.SBMKernel;
const MatchDegreeKernel = sandbox.MatchDegreeKernel;
if (!NETGEN_DATA || !ProfileKernel || !SBMKernel || !MatchDegreeKernel) {
  console.error("Failed to load NETGEN / kernels. Sandbox keys:", Object.keys(sandbox));
  process.exit(1);
}

const { NODES, EDGES, CLUSTER_OF, C1, C2, C3, OUT } = NETGEN_DATA;
const REAL_CLUSTERS = { C1, C2, C3 };

// ── Profile (mirrors sbm.html's combined-mode profile). ─────────────
const PROF = ProfileKernel.runProfile({
  gen: "sbm",
  edgelist: EDGES.map(e => [String(e.u), String(e.v)]),
  clustering: NODES.filter(n => CLUSTER_OF[n] !== "OUT")
                   .map(n => [String(n), CLUSTER_OF[n]]),
  outlier_mode: "combined",
  drop_oo: false,
});
const BLOCK_LABELS = PROF.files["cluster_id.csv"].trim().split("\n");
const BLOCK_NAMES  = BLOCK_LABELS.map(c => c === ProfileKernel.COMBINED_OUTLIER_CLUSTER_ID ? "OUT" : c);
const BLOCK_NODES  = { "C1": C1, "C2": C2, "C3": C3, "OUT": OUT };
const NUM_BLOCKS   = BLOCK_NAMES.length;

const EDGE_COUNTS = Array.from({ length: NUM_BLOCKS }, () => new Array(NUM_BLOCKS).fill(0));
PROF.files["edge_counts.csv"].trim().split("\n").forEach(row => {
  const [r, c, w] = row.split(",").map(Number);
  EDGE_COUNTS[r][c] = w;
});

const TARGET_DEG = {};
NODES.forEach(n => { TARGET_DEG[n] = 0; });
EDGES.forEach(e => { TARGET_DEG[e.u]++; TARGET_DEG[e.v]++; });

const blockPairs = [];
for (let r = 0; r < NUM_BLOCKS; r++)
  for (let s = r; s < NUM_BLOCKS; s++) blockPairs.push([r, s]);

function buildFreshUrns() {
  return BLOCK_NAMES.map((name) => {
    const urn = [];
    BLOCK_NODES[name].forEach(n => {
      for (let i = 0; i < TARGET_DEG[n]; i++) urn.push({ node: n, geomIdx: i });
    });
    return urn;
  });
}

// ── Pipeline: sample, simplify, match_degree (true_greedy by default
// per src/sbm/pipeline.sh). Mirrors sbm.html g3 → g4 → g5. ───────────
function runPipeline(seed) {
  const urns = buildFreshUrns();
  const budget = SBMKernel.buildPairBudget(EDGE_COUNTS, blockPairs);
  const seen = new Map();
  const trace = SBMKernel.buildTraceFrom(urns, budget, blockPairs, seen, randomLcg(seed));

  const kept = [];
  let nLoops = 0, nMulti = 0;
  for (const t of trace) {
    if (t.loop) { nLoops++; continue; }
    if (t.multi) { nMulti++; continue; }
    kept.push([t.u, t.v]);
  }
  const drops = nLoops + nMulti;

  // Build adjacency from kept edges; compute deficit from achievedDeg.
  const baseAdj = {};
  NODES.forEach(n => { baseAdj[n] = new Set(); });
  const achievedDeg = {};
  NODES.forEach(n => { achievedDeg[n] = 0; });
  for (const [u, v] of kept) {
    if (!baseAdj[u].has(v)) {
      baseAdj[u].add(v); baseAdj[v].add(u);
      achievedDeg[u]++; achievedDeg[v]++;
    }
  }
  const deficit = {};
  NODES.forEach(n => {
    const d = TARGET_DEG[n] - achievedDeg[n];
    if (d > 0) deficit[n] = d;
  });

  const mdSteps = MatchDegreeKernel.runTrueGreedy({
    iids: NODES.slice(),
    deficit,
    baseAdj,
  }, {});
  const final = mdSteps[mdSteps.length - 1];
  const mdEdges = final.edges; // array of [u, v]
  const unplacedSum = Object.values(final.unplaced).reduce((a, b) => a + b, 0);

  // Final adjacency = base + match_degree edges. Cluster-internal
  // adjacency restricted to real clusters (drop OUT, treat C1/C2/C3
  // independently).
  const finalAdj = {};
  NODES.forEach(n => { finalAdj[n] = new Set(baseAdj[n]); });
  for (const [u, v] of mdEdges) {
    finalAdj[u].add(v); finalAdj[v].add(u);
  }

  const disconn = [];
  for (const cName of Object.keys(REAL_CLUSTERS)) {
    const members = REAL_CLUSTERS[cName];
    if (members.length <= 1) continue;
    // BFS over intra-cluster edges only.
    const memberSet = new Set(members);
    const visited = new Set();
    const queue = [members[0]];
    visited.add(members[0]);
    while (queue.length > 0) {
      const u = queue.shift();
      for (const v of finalAdj[u]) {
        if (!memberSet.has(v) || visited.has(v)) continue;
        visited.add(v);
        queue.push(v);
      }
    }
    if (visited.size < members.length) {
      const missing = members.filter(n => !visited.has(n));
      disconn.push({ cluster: cName, isolated: missing });
    }
  }

  return {
    seed,
    sampleEdges: trace.length,
    nLoops,
    nMulti,
    drops,
    keptEdges: kept.length,
    mdEdges: mdEdges.length,
    unplacedSum,
    disconnected: disconn,
    finalEdges: kept.length + mdEdges.length,
  };
}

// ── Sweep + score. ──────────────────────────────────────────────────
//
// Hard requirement: ≥ 1 real cluster ends internally disconnected AND
// match_degree leaves ≥ 1 stub unplaced (so the top-up visibly fails to
// close the deficit on its own).
// Soft: composite = drops + mdEdges (proxy for "many simplify drops + many
// match-degree edges"). Tie-break: more disconnected clusters wins, then
// smaller seed.
const MAX_SEEDS = parseInt(process.env.MAX_SEEDS || "10000", 10);
const TOP_K     = parseInt(process.env.TOP_K     || "10",    10);

const ranked = [];
for (let s = 1; s <= MAX_SEEDS; s++) {
  let r;
  try { r = runPipeline(s); } catch (e) { continue; }
  if (r.disconnected.length === 0) continue;
  if (r.unplacedSum === 0) continue;
  ranked.push(r);
}

ranked.sort((a, b) =>
  (b.disconnected.length - a.disconnected.length) ||
  ((b.drops + b.mdEdges) - (a.drops + a.mdEdges)) ||
  (a.seed - b.seed)
);

const summary = {
  scanned: MAX_SEEDS,
  hits: ranked.length,
  top: ranked.slice(0, TOP_K).map(r => ({
    seed: r.seed,
    drops: r.drops,
    nLoops: r.nLoops,
    nMulti: r.nMulti,
    mdEdges: r.mdEdges,
    unplaced: r.unplacedSum,
    finalEdges: r.finalEdges,
    disconnected: r.disconnected,
  })),
};

process.stdout.write(JSON.stringify(summary, null, 2) + "\n");
