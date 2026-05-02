// Sweep d3.randomLcg seeds for the ABCD+o netgen page (abcd+o.html) to
// find an initial seed whose pipeline exercises every rewire stage hard:
// many cluster-rewire ops + many cluster residues forwarded; many bg-
// rewire ops + many bg residues; many final-swap ops + many edges
// truly dropped at final.
//
// Mirrors abcd's seed_sweep.mjs with two ABCD+o-specific changes:
//   - hasOutliers: true on the kernel call (cluster_id = 1 is the
//     combined outlier block, ŷ_u = 0).
//   - 4 blocks: outlier + C1 + C2 + C3, with the assignment trace
//     driving which page-side nodes land in which kernel cluster.
//
// Run:
//   node tools/viz_check/abcd_o/seed_sweep.mjs
//
// Env knobs:
//   MAX_SEEDS  upper bound on STUB_SEED to scan (default 5000)
//   TOP_K      how many to print (default 10)
//   U_SEED     fixed U_SEED for the run (default 5, page's default)
//   ASSIGN_SEED  fixed assignment seed (default 1)

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const NETGEN = resolve(here, "../../../../web/vltanh.github.io/netgen");
const SHARED_PATH      = resolve(NETGEN, "shared.js");
const PROFILE_PATH     = resolve(NETGEN, "js/profile_kernel.js");
const ABCD_PATH        = resolve(NETGEN, "js/abcd_kernel.js");

// d3.randomLcg byte-for-byte d3-random@3 (matches the page's RNG).
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

const sandbox = {
  d3: d3Stub,
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
};
sandbox.window = sandbox;
sandbox.global = sandbox;
vm.createContext(sandbox);

for (const p of [SHARED_PATH, PROFILE_PATH, ABCD_PATH]) {
  const src = readFileSync(p, "utf8");
  vm.runInContext(src, sandbox, { filename: p });
}

const NETGEN_DATA = sandbox.NETGEN;
const ABCDKernel  = sandbox.ABCDKernel;
const ProfileKernel = sandbox.ProfileKernel;
if (!NETGEN_DATA || !ABCDKernel || !ProfileKernel) {
  console.error("Failed to load shared.js / kernels.");
  process.exit(1);
}

const { NODES, EDGES, CLUSTER_OF, C1, C2, C3, OUT } = NETGEN_DATA;
const OUT_N = OUT;

// Profile: ABCD+o defaults — singleton outlier mode + drop_oo. Mirrors
// abcd+o.html which filters "OUT" nodes out of the input clustering so
// identify_outliers picks them up as unclustered.
const PROF_OUT = ProfileKernel.runProfile({
  gen: "abcd+o",
  edgelist: EDGES.map(e => [String(e.u), String(e.v)]),
  clustering: NODES.filter(n => CLUSTER_OF[n] !== "OUT")
                   .map(n => [String(n), CLUSTER_OF[n]]),
  outlier_mode: "singleton",
  drop_oo: true,
});
const SYNTHETIC_XI = parseFloat(PROF_OUT.files["mixing_parameter.txt"]);
const N_OUTLIERS = parseInt(PROF_OUT.files["n_outliers.txt"], 10);

// Per-node DEG_KEPT (post-drop_oo).
const INPUT_OUT_SET = new Set(OUT_N);
const DEG_KEPT = {};
NODES.forEach(n => { DEG_KEPT[n] = 0; });
EDGES.forEach(({ u, v }) => {
  if (INPUT_OUT_SET.has(u) && INPUT_OUT_SET.has(v)) return;
  DEG_KEPT[u]++; DEG_KEPT[v]++;
});

// Block scheme matches abcd+o.html: outlier first, then C1/C2/C3.
const BLOCK_OUT = "outlier";
const BLOCK_NAMES = [BLOCK_OUT, "C1", "C2", "C3"];
const REAL_BLOCK_NAMES = ["C1", "C2", "C3"];
const BLOCK_NODES = { [BLOCK_OUT]: OUT_N.slice(), C1: C1, C2: C2, C3: C3 };
const BLOCK_SIZE = {
  [BLOCK_OUT]: N_OUTLIERS,
  C1: C1.length, C2: C2.length, C3: C3.length,
};
const KERNEL_CID_FROM_BLOCK = { [BLOCK_OUT]: 1, C1: 2, C2: 3, C3: 4 };
const KERNEL_S = [N_OUTLIERS, C1.length, C2.length, C3.length];
const KERNEL_W = NODES.map(n => DEG_KEPT[n]);

// Mirror of abcd+o.html's two-phase computeAssignTrace.
function computeAssignTrace(seed) {
  const rng = randomLcg(seed);
  const n = NODES.length;
  const xi = SYNTHETIC_XI;
  let Lbar = 0;
  NODES.forEach(u => { Lbar += Math.min(1, xi * DEG_KEPT[u]); });
  const threshold = Lbar + N_OUTLIERS - Lbar * N_OUTLIERS / n - 1;
  const eligible = NODES.filter(u => DEG_KEPT[u] <= threshold);
  const nMinus = n - N_OUTLIERS;
  const phiSum = REAL_BLOCK_NAMES.reduce((s, b) =>
    s + Math.pow(BLOCK_SIZE[b] / nMinus, 2), 0);
  const phi = 1 - phiSum * (nMinus * xi) / (nMinus * xi + N_OUTLIERS);
  // Phase 1
  const outlierPool = eligible.slice();
  const trace = [];
  for (let i = 0; i < N_OUTLIERS && outlierPool.length > 0; i++) {
    const idx = Math.floor(rng() * outlierPool.length);
    const v = outlierPool.splice(idx, 1)[0];
    trace.push({ v, picked: BLOCK_OUT });
  }
  const outlierSet = new Set(trace.map(t => t.v));
  // Phase 2
  const nonOutliers = NODES.filter(u => !outlierSet.has(u))
    .sort((a, b) => DEG_KEPT[b] - DEG_KEPT[a] || a - b);
  const remaining = {}; const slotsAvail = {};
  REAL_BLOCK_NAMES.forEach(b => {
    remaining[b] = BLOCK_SIZE[b];
    slotsAvail[b] = BLOCK_NODES[b].slice();
  });
  for (const v of nonOutliers) {
    const w = DEG_KEPT[v];
    const xu = Math.ceil((1 - xi * phi) * w);
    let admissible = REAL_BLOCK_NAMES.filter(b =>
      remaining[b] > 0 && BLOCK_SIZE[b] - 1 >= xu);
    if (admissible.length === 0) admissible = REAL_BLOCK_NAMES.filter(b => remaining[b] > 0);
    const totalRem = admissible.reduce((s, b) => s + remaining[b], 0);
    const u = rng() * totalRem;
    let acc = 0, picked = admissible[0];
    for (const b of admissible) {
      acc += remaining[b];
      if (u < acc) { picked = b; break; }
    }
    const pool = slotsAvail[picked];
    pool.splice(Math.floor(rng() * pool.length), 1);
    remaining[picked]--;
    trace.push({ v, picked });
  }
  return trace;
}

const ASSIGN_SEED = parseInt(process.env.ASSIGN_SEED || "1", 10);
const TRACE = computeAssignTrace(ASSIGN_SEED);
const LIVE_BLOCK_OF = {};
TRACE.forEach(t => { LIVE_BLOCK_OF[t.v] = t.picked; });
const KERNEL_CLUSTERS = NODES.map(n => KERNEL_CID_FROM_BLOCK[LIVE_BLOCK_OF[n]]);

const U_SEED   = parseInt(process.env.U_SEED   || "5", 10);
const MAX_SEEDS = parseInt(process.env.MAX_SEEDS || "5000", 10);
const TOP_K    = parseInt(process.env.TOP_K    || "10",   10);

function seedInt(uSeed, stubSeed) {
  return ((uSeed * 1009 + 17) ^ (stubSeed * 131 + 7)) >>> 0;
}

function runOne(stubSeed) {
  const cm = ABCDKernel.configModel({
    clusters: KERNEL_CLUSTERS,
    w: KERNEL_W,
    s: KERNEL_S,
    hasOutliers: true,
    xi: SYNTHETIC_XI,
    rng: randomLcg(seedInt(U_SEED, stubSeed) + 1),
    traceStages: true,
  });
  const stages = cm.stages;
  let crewireOps = 0, crewireResidue = 0;
  let crewireClustersWithOps = 0, crewireClustersWithResidue = 0;
  stages.perCluster.forEach(stage => {
    const ops = (stage.rewireOps || []).length;
    crewireOps += ops;
    crewireResidue += stage.residueForwarded || 0;
    if (ops > 0) crewireClustersWithOps += 1;
    if (stage.residueForwarded > 0) crewireClustersWithResidue += 1;
  });
  const bgOps      = (stages.global.rewireOps || []).length;
  const bgResidue  = (stages.global.residueAfterRewire || 0) * 2;
  const finalOps   = (stages.final ? stages.final.rewireOps.length : 0);
  const finalDrop  = (stages.final ? stages.final.residueAfter : 0) * 2;
  return {
    stubSeed, edges: cm.edges.length,
    crewireOps, crewireResidue,
    crewireClustersWithOps, crewireClustersWithResidue,
    bgOps, bgResidue,
    finalOps, finalDrop,
  };
}

const ranked = [];
for (let s = 1; s <= MAX_SEEDS; s++) {
  let r;
  try { r = runOne(s); } catch (e) { continue; }
  if (r.crewireOps === 0 || r.crewireResidue === 0) continue;
  if (r.bgOps === 0 || r.bgResidue === 0) continue;
  if (r.finalOps === 0 || r.finalDrop === 0) continue;
  if (r.crewireClustersWithResidue < 2) continue;
  ranked.push(r);
}

function score(r) {
  return (
    r.crewireOps + r.crewireResidue +
    r.bgOps      + r.bgResidue      +
    r.finalOps   + r.finalDrop
  );
}

ranked.sort((a, b) =>
  (score(b) - score(a)) ||
  (b.crewireClustersWithResidue - a.crewireClustersWithResidue) ||
  (a.stubSeed - b.stubSeed)
);

const summary = {
  scanned: MAX_SEEDS,
  uSeed: U_SEED,
  assignSeed: ASSIGN_SEED,
  xi: SYNTHETIC_XI,
  nOutliers: N_OUTLIERS,
  hits: ranked.length,
  top: ranked.slice(0, TOP_K).map(r => ({
    stubSeed: r.stubSeed,
    score: score(r),
    crewire: { ops: r.crewireOps, residue: r.crewireResidue,
               clustersWithOps: r.crewireClustersWithOps,
               clustersWithResidue: r.crewireClustersWithResidue },
    bgrewire: { ops: r.bgOps, residue: r.bgResidue },
    finalswap: { ops: r.finalOps, dropped: r.finalDrop },
    edges: r.edges,
  })),
};

process.stdout.write(JSON.stringify(summary, null, 2) + "\n");
