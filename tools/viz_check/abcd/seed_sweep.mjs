// Sweep d3.randomLcg seeds for the ABCD netgen page (abcd.html) to find
// an initial seed whose pipeline exercises every rewire stage hard:
// many cluster-rewire ops + many cluster residues forwarded; many bg-
// rewire ops + many bg residues; many final-swap ops + many edges
// truly dropped at final.
//
// Loads shared.js + profile_kernel.js + abcd_kernel.js into a vm
// context behind minimal d3 + document stubs, then drives the same
// kernel the page drives (mirrors abcd.html's buildRealization without
// any pairOverride state).
//
// Run:
//   node tools/viz_check/abcd/seed_sweep.mjs
//
// Env knobs:
//   MAX_SEEDS  upper bound on STUB_SEED to scan (default 5000)
//   TOP_K      how many to print (default 10)
//   U_SEED     fixed U_SEED for the run (default 78, page's default)

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

const { NODES, EDGES, CLUSTER_OF, C1, C2, C3 } = NETGEN_DATA;

// Page-side kernel inputs: same shape as abcd.html.
//   KERNEL_S       cluster sizes (|C1|, |C2|, |C3|, 1, 1) for the two
//                  outliers as singletons (matches outlier_mode=singleton).
//   KERNEL_W       per-node degree (NODES order).
//   KERNEL_CLUSTERS 1-based cluster id per node.
const KERNEL_S = [C1.length, C2.length, C3.length, 1, 1];

const DEGREES = {};
NODES.forEach(n => { DEGREES[n] = 0; });
EDGES.forEach(e => { DEGREES[e.u]++; DEGREES[e.v]++; });
const KERNEL_W = NODES.map(n => DEGREES[n]);

const BLOCK_NAMES = ["C1", "C2", "C3", "S19", "S20"];
const BLOCK_NODES = { "C1": C1, "C2": C2, "C3": C3, "S19": [19], "S20": [20] };
const BLOCK_SIZE  = Object.fromEntries(BLOCK_NAMES.map(b => [b, BLOCK_NODES[b].length]));
const KERNEL_CID_FROM_BLOCK = { C1: 1, C2: 2, C3: 3, S19: 4, S20: 5 };

const queueRanks = NODES.slice().sort((a, b) =>
  DEGREES[b] - DEGREES[a] || a - b);

// Mirror of abcd.html's computeTrace (canonical-stochastic vertex
// assignment); LIVE_BLOCK_OF after first paint = result of this trace
// at ASSIGN_SEED=1. Sweep must respect this mapping or KERNEL_CLUSTERS
// drift from what the page actually feeds the sampler.
function liveBlockMapForAssign(assignSeed, xi) {
  const rng = randomLcg(assignSeed);
  const n = NODES.length;
  const phi = 1 - BLOCK_NAMES.reduce((s, b) =>
    s + Math.pow(BLOCK_SIZE[b] / n, 2), 0);
  const remaining = Object.fromEntries(BLOCK_NAMES.map(b => [b, BLOCK_SIZE[b]]));
  const slotsAvail = Object.fromEntries(BLOCK_NAMES.map(b => [b, BLOCK_NODES[b].slice()]));
  const live = {};
  for (const v of queueRanks) {
    const w = DEGREES[v];
    const xu = Math.ceil((1 - xi * phi) * w);
    let admissible = BLOCK_NAMES.filter(b =>
      remaining[b] > 0 && BLOCK_SIZE[b] - 1 >= xu);
    if (admissible.length === 0) {
      admissible = BLOCK_NAMES.filter(b => remaining[b] > 0);
    }
    const totalRem = admissible.reduce((s, b) => s + remaining[b], 0);
    const u = rng() * totalRem;
    let acc = 0, picked = admissible[0];
    for (const b of admissible) {
      acc += remaining[b];
      if (u < acc) { picked = b; break; }
    }
    const pool = slotsAvail[picked];
    const slotIdx = Math.floor(rng() * pool.length);
    pool.splice(slotIdx, 1);
    remaining[picked]--;
    live[v] = picked;
  }
  return live;
}

// xi from the profile kernel (same path the page uses).
const PROF_OUT = ProfileKernel.runProfile({
  gen: "abcd",
  edgelist: EDGES.map(e => [String(e.u), String(e.v)]),
  clustering: NODES.filter(n => CLUSTER_OF[n] !== "OUT")
                   .map(n => [String(n), CLUSTER_OF[n]]),
  outlier_mode: "singleton",
  drop_oo: false,
});
const SYNTHETIC_XI = parseFloat(PROF_OUT.files["mixing_parameter.txt"]);

const ASSIGN_SEED = parseInt(process.env.ASSIGN_SEED || "1", 10);
const LIVE_BLOCK_OF = liveBlockMapForAssign(ASSIGN_SEED, SYNTHETIC_XI);
const KERNEL_CLUSTERS = NODES.map(n => KERNEL_CID_FROM_BLOCK[LIVE_BLOCK_OF[n]]);

const U_SEED  = parseInt(process.env.U_SEED  || "78", 10);
const MAX_SEEDS = parseInt(process.env.MAX_SEEDS || "5000", 10);
const TOP_K     = parseInt(process.env.TOP_K     || "10",   10);

// Mirrors abcd.html buildRealization's RNG seeding.
function seedInt(uSeed, stubSeed) {
  return ((uSeed * 1009 + 17) ^ (stubSeed * 131 + 7)) >>> 0;
}

function runOne(stubSeed) {
  const cm = ABCDKernel.configModel({
    clusters: KERNEL_CLUSTERS,
    w: KERNEL_W,
    s: KERNEL_S,
    hasOutliers: false,
    xi: SYNTHETIC_XI,
    rng: randomLcg(seedInt(U_SEED, stubSeed) + 1),
    traceStages: true,
  });
  const stages = cm.stages;
  // Cluster-rewire metrics: per-cluster op count + residue forwarded.
  let crewireOps = 0, crewireResidue = 0;
  let crewireClustersWithOps = 0, crewireClustersWithResidue = 0;
  stages.perCluster.forEach(stage => {
    const ops = (stage.rewireOps || []).length;
    crewireOps += ops;
    crewireResidue += stage.residueForwarded || 0;
    if (ops > 0) crewireClustersWithOps += 1;
    if (stage.residueForwarded > 0) crewireClustersWithResidue += 1;
  });
  // bg-rewire metrics.
  const bgOps      = (stages.global.rewireOps || []).length;
  const bgResidue  = (stages.global.residueAfterRewire || 0) * 2; // pairs → endpoints
  // final-swap metrics.
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
  // Hard requirement: every rewire stage must do work AND forward / drop
  // at least some residue. Otherwise the panel reads "no activity" for
  // that stage on the page.
  if (r.crewireOps === 0 || r.crewireResidue === 0) continue;
  if (r.bgOps === 0 || r.bgResidue === 0) continue;
  if (r.finalOps === 0 || r.finalDrop === 0) continue;
  // Also require residue spread across multiple clusters so the cluster-
  // rewire walker shows interesting behaviour in more than one cluster.
  if (r.crewireClustersWithResidue < 2) continue;
  ranked.push(r);
}

// Composite score: weight each stage equally so the seed pulls hard on
// every panel rather than concentrating activity in one stage.
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
  xi: SYNTHETIC_XI,
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
