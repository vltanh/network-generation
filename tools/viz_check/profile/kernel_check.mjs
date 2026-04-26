// Faithful JS port of src/profile_common.py + per-gen profile.py for the
// five purely-deterministic profiles: sbm, abcd, abcd+o, lfr, npso (npso
// only the gen-agnostic outputs; derived.txt depends on powerlaw.Fit and
// nk.globals.ClusteringCoefficient.exactGlobal which are out of scope).
//
// Profile has no PRNG. JS reads {gen, edgelist, clustering, outlier_mode,
// drop_oo} on stdin and emits {files: {fname: content_str}} on stdout.
// The harness then byte-diffs each emitted file against the canonical
// per-gen profile.py's on-disk artifact.
//
// Run:
//   node tools/viz_check/profile/kernel_check.mjs < payload.json

const COMBINED_OUTLIER_CLUSTER_ID = "__outliers__";

// ── float formatting matching Python str(float) ──────────────────
// Python uses shortest round-trip repr. JS toString matches except
// that integer-valued floats lose ".0". We restore it.
function pyFloatStr(x) {
  if (typeof x !== "number") x = Number(x);
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x > 0 ? "inf" : "-inf";
  let s = x.toString();
  if (Number.isInteger(x) && !s.includes("e") && !s.includes("E") && !s.includes(".")) {
    s = s + ".0";
  }
  return s;
}

// ── reading ────────────────────────────────────────────────────
function readClustering(rows) {
  // rows: [[node_id, cluster_id], ...]
  // Mirror pandas read_csv(usecols=[0,1], dtype=str).dropna() then
  // dict(zip(...)) + value_counts().to_dict() + set(keys).
  const node2com = new Map();
  const counts = new Map();
  const nodes = new Set();
  for (const [n, c] of rows) {
    if (n == null || c == null || n === "" || c === "") continue;
    const ns = String(n);
    const cs = String(c);
    node2com.set(ns, cs);
    counts.set(cs, (counts.get(cs) || 0) + 1);
    nodes.add(ns);
  }
  return { nodes, node2com, clusterCounts: counts };
}

function readEdgelist(edges, state) {
  // edges: [[u, v], ...]; mutates state.nodes, returns neighbors map.
  const neighbors = new Map();
  for (const [u, v] of edges) {
    if (u == null || v == null || u === "" || v === "") continue;
    const us = String(u);
    const vs = String(v);
    if (us === vs) continue;
    if (!neighbors.has(us)) neighbors.set(us, new Set());
    if (!neighbors.has(vs)) neighbors.set(vs, new Set());
    neighbors.get(us).add(vs);
    neighbors.get(vs).add(us);
    state.nodes.add(us);
    state.nodes.add(vs);
  }
  return neighbors;
}

// ── outlier identification + mode transform ────────────────────
function identifyOutliers(state) {
  const { nodes, node2com, clusterCounts } = state;
  const outliers = new Set();
  for (const u of nodes) {
    if (!node2com.has(u)) outliers.add(u);
  }
  const singletons = [];
  for (const [c, sz] of clusterCounts) {
    if (sz === 1) singletons.push(c);
  }
  for (const c of singletons) clusterCounts.delete(c);
  for (const [u, c] of Array.from(node2com.entries())) {
    if (!clusterCounts.has(c)) {
      node2com.delete(u);
      outliers.add(u);
    }
  }
  return outliers;
}

function applyOutlierMode(state, neighbors, outliers, mode, dropOO) {
  const { nodes, node2com, clusterCounts } = state;
  if (!["excluded", "singleton", "combined"].includes(mode)) {
    throw new Error(`unknown outlier mode: ${mode}`);
  }
  if (dropOO && mode !== "excluded") {
    for (const u of outliers) {
      const nb = neighbors.get(u);
      if (!nb) continue;
      const filtered = new Set();
      for (const v of nb) if (!outliers.has(v)) filtered.add(v);
      neighbors.set(u, filtered);
    }
  }
  if (mode === "excluded") {
    for (const u of outliers) {
      nodes.delete(u);
      neighbors.delete(u);
    }
    for (const v of Array.from(neighbors.keys())) {
      const nb = neighbors.get(v);
      const filtered = new Set();
      for (const w of nb) if (!outliers.has(w)) filtered.add(w);
      neighbors.set(v, filtered);
    }
  } else if (mode === "singleton") {
    for (const u of outliers) {
      const cid = `__outlier_${u}__`;
      node2com.set(u, cid);
      clusterCounts.set(cid, 1);
    }
  } else if (mode === "combined") {
    if (outliers.size > 0) {
      for (const u of outliers) node2com.set(u, COMBINED_OUTLIER_CLUSTER_ID);
      clusterCounts.set(COMBINED_OUTLIER_CLUSTER_ID, outliers.size);
    }
  }
}

// ── mappings ───────────────────────────────────────────────────
function computeNodeDegree(state, neighbors) {
  // Sort by (-deg, id). String ids => lex order on tie.
  const arr = [];
  for (const u of state.nodes) {
    const d = neighbors.has(u) ? neighbors.get(u).size : 0;
    arr.push([u, d]);
  }
  arr.sort((a, b) => {
    if (a[1] !== b[1]) return b[1] - a[1];
    return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0;
  });
  const nodeId2iid = new Map();
  arr.forEach(([u, _], i) => nodeId2iid.set(u, i));
  return { nodeDegSorted: arr, nodeId2iid };
}

function computeCommSize(state) {
  const arr = Array.from(state.clusterCounts.entries());
  arr.sort((a, b) => {
    if (a[1] !== b[1]) return b[1] - a[1];
    return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0;
  });
  const clusterId2iid = new Map();
  arr.forEach(([c, _], i) => clusterId2iid.set(c, i));
  return { commSizeSorted: arr, clusterId2iid };
}

// ── exporters: each returns the file content string ─────────────
function exportNodeId(nodeDegSorted) {
  return nodeDegSorted.map(([u, _]) => u).join("\n") + (nodeDegSorted.length ? "\n" : "");
}

function exportClusterId(commSizeSorted) {
  return commSizeSorted.map(([c, _]) => c).join("\n") + (commSizeSorted.length ? "\n" : "");
}

function exportAssignment(nodeDegSorted, node2com, clusterId2iid) {
  const rows = nodeDegSorted.map(([u, _]) => {
    if (node2com.has(u)) return String(clusterId2iid.get(node2com.get(u)));
    return "-1";
  });
  return rows.join("\n") + (rows.length ? "\n" : "");
}

function exportDegree(nodeDegSorted) {
  const rows = nodeDegSorted.map(([_, d]) => String(d));
  return rows.join("\n") + (rows.length ? "\n" : "");
}

function exportCommSize(commSizeSorted) {
  const rows = commSizeSorted.map(([_, sz]) => String(sz));
  return rows.join("\n") + (rows.length ? "\n" : "");
}

function exportMixingParam(value) {
  return pyFloatStr(value);
}

function exportNOutliers(n) {
  return String(n);
}

// ── edge-count matrix ──────────────────────────────────────────
function computeEdgeCount(state, neighbors, clusterId2iid) {
  const counts = new Map();
  for (const u of state.nodes) {
    const cu = state.node2com.get(u);
    if (cu === undefined) continue;
    const ciu = clusterId2iid.get(cu);
    const nb = neighbors.get(u);
    if (!nb) continue;
    for (const v of nb) {
      const cv = state.node2com.get(v);
      if (cv === undefined) continue;
      const civ = clusterId2iid.get(cv);
      const key = `${ciu},${civ}`;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  return counts;
}

function exportEdgeCount(edgeCounts) {
  const triples = [];
  for (const [k, w] of edgeCounts) {
    const [r, c] = k.split(",").map(Number);
    triples.push([r, c, w]);
  }
  triples.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  if (triples.length === 0) return "";
  return triples.map(([r, c, w]) => `${r},${c},${w}`).join("\n") + "\n";
}

// ── numpy pairwise summation (matches numpy/_core/src/umath/loops_utils.h.src) ──
// Required so np.mean(mus) byte-matches between Python and JS for the
// LFR mean-mu reduction. JS doubles are IEEE-754 float64, same as numpy.
function numpyPairwiseSum(arr) {
  const PW_BLOCKSIZE = 128;
  const n = arr.length;
  if (n < 8) {
    let s = 0;
    for (let i = 0; i < n; i++) s += arr[i];
    return s;
  }
  if (n <= PW_BLOCKSIZE) {
    const r = [arr[0], arr[1], arr[2], arr[3], arr[4], arr[5], arr[6], arr[7]];
    let i;
    const tail = n - (n % 8);
    for (i = 8; i < tail; i += 8) {
      r[0] += arr[i + 0];
      r[1] += arr[i + 1];
      r[2] += arr[i + 2];
      r[3] += arr[i + 3];
      r[4] += arr[i + 4];
      r[5] += arr[i + 5];
      r[6] += arr[i + 6];
      r[7] += arr[i + 7];
    }
    let res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    for (; i < n; i++) res += arr[i];
    return res;
  }
  let n2 = Math.floor(n / 2);
  n2 -= n2 % 8;
  return numpyPairwiseSum(arr.slice(0, n2)) + numpyPairwiseSum(arr.slice(n2));
}

// ── mixing parameter ───────────────────────────────────────────
function computeMixingParameter(state, neighbors, reduction) {
  if (reduction !== "mean" && reduction !== "global") {
    throw new Error(`unknown reduction: ${reduction}`);
  }
  const inDeg = new Map();
  const outDeg = new Map();
  for (const u of state.nodes) {
    const cu = state.node2com.get(u);
    if (cu === undefined) continue;
    const nb = neighbors.get(u);
    if (!nb) continue;
    for (const v of nb) {
      const cv = state.node2com.get(v);
      if (cv === undefined) continue;
      if (cu === cv) inDeg.set(u, (inDeg.get(u) || 0) + 1);
      else outDeg.set(u, (outDeg.get(u) || 0) + 1);
    }
  }
  if (reduction === "mean") {
    // Match np.mean: sum of mus / count. Iterate sorted(nodes).
    const sorted = Array.from(state.nodes).sort();
    const mus = [];
    for (const u of sorted) {
      const total = (inDeg.get(u) || 0) + (outDeg.get(u) || 0);
      if (total === 0) continue;
      mus.push((outDeg.get(u) || 0) / total);
    }
    if (mus.length === 0) return 0.0;
    return numpyPairwiseSum(mus) / mus.length;
  }
  let outsSum = 0;
  for (const v of outDeg.values()) outsSum += v;
  let inSum = 0;
  for (const v of inDeg.values()) inSum += v;
  const total = outsSum + inSum;
  if (total === 0) return 0.0;
  return outsSum / total;
}

// ── per-gen registry ───────────────────────────────────────────
const REGISTRY = {
  "sbm": {
    outputs: (state, neighbors) => {
      const { nodeDegSorted } = computeNodeDegree(state, neighbors);
      const { commSizeSorted, clusterId2iid } = computeCommSize(state);
      const edgeCounts = computeEdgeCount(state, neighbors, clusterId2iid);
      return {
        "node_id.csv": exportNodeId(nodeDegSorted),
        "cluster_id.csv": exportClusterId(commSizeSorted),
        "assignment.csv": exportAssignment(nodeDegSorted, state.node2com, clusterId2iid),
        "degree.csv": exportDegree(nodeDegSorted),
        "edge_counts.csv": exportEdgeCount(edgeCounts),
      };
    },
  },
  "abcd": {
    outputs: (state, neighbors) => {
      const { nodeDegSorted } = computeNodeDegree(state, neighbors);
      const { commSizeSorted } = computeCommSize(state);
      const xi = computeMixingParameter(state, neighbors, "global");
      return {
        "degree.csv": exportDegree(nodeDegSorted),
        "cluster_sizes.csv": exportCommSize(commSizeSorted),
        "mixing_parameter.txt": exportMixingParam(xi),
      };
    },
  },
  "abcd+o": {
    outputs: (state, neighbors, ctx) => {
      // n_outliers is the pre-apply outlier count.
      const { nodeDegSorted } = computeNodeDegree(state, neighbors);
      const { commSizeSorted } = computeCommSize(state);
      const realClusters = commSizeSorted.filter(([cid, _]) => {
        return cid !== COMBINED_OUTLIER_CLUSTER_ID && !cid.startsWith("__outlier_");
      });
      const xi = computeMixingParameter(state, neighbors, "global");
      return {
        "degree.csv": exportDegree(nodeDegSorted),
        "cluster_sizes.csv": exportCommSize(realClusters),
        "n_outliers.txt": exportNOutliers(ctx.preApplyOutlierCount),
        "mixing_parameter.txt": exportMixingParam(xi),
      };
    },
  },
  "lfr": {
    outputs: (state, neighbors) => {
      const { nodeDegSorted } = computeNodeDegree(state, neighbors);
      const { commSizeSorted } = computeCommSize(state);
      const mu = computeMixingParameter(state, neighbors, "mean");
      return {
        "degree.csv": exportDegree(nodeDegSorted),
        "cluster_sizes.csv": exportCommSize(commSizeSorted),
        "mixing_parameter.txt": exportMixingParam(mu),
      };
    },
  },
  "npso": {
    // derived.txt depends on powerlaw + networkit; out of scope for the
    // JS port. Cover only degree.csv + cluster_sizes.csv.
    outputs: (state, neighbors) => {
      const { nodeDegSorted } = computeNodeDegree(state, neighbors);
      const { commSizeSorted } = computeCommSize(state);
      return {
        "degree.csv": exportDegree(nodeDegSorted),
        "cluster_sizes.csv": exportCommSize(commSizeSorted),
      };
    },
  },
};

// ── runner ─────────────────────────────────────────────────────
function runProfile(payload) {
  const gen = payload.gen;
  if (!REGISTRY[gen]) throw new Error(`unsupported gen: ${gen}`);
  const state = readClustering(payload.clustering);
  const neighbors = readEdgelist(payload.edgelist, state);
  const outliers = identifyOutliers(state);
  const preApplyOutlierCount = outliers.size;
  applyOutlierMode(state, neighbors, outliers, payload.outlier_mode, !!payload.drop_oo);
  return REGISTRY[gen].outputs(state, neighbors, { preApplyOutlierCount });
}

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf-8");
}

const src = await readStdin();
const payload = JSON.parse(src);
const files = runProfile(payload);
process.stdout.write(JSON.stringify({ files }) + "\n");
