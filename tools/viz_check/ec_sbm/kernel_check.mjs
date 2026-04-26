#!/usr/bin/env node
// Faithful JS port of ec_sbm stage-2 (gen_kec_core constructive cores),
// trace-driven for byte-equality vs canonical Python.
//
// Inputs (stdin JSON):
//   { mode: "replay",
//     profile_dir: "...",        // path to .state/profile/
//     seed: <int>,                // recorded for compatibility; trace has the draws
//     trace: [...] }              // from instrumented/runner.py
// Output (stdout JSON):
//   { edges: [[u,v],...], deg_final: [...] }
//
// Trace entries:
//   { site: "set_iter", order: [<iid>,...] }   — emitted BEFORE each phase-1
//                                                 `for v in processed_nodes`.
//   { site: "np_choice", n, idx, value }       — emitted by phase-2 fallback.

import { readFileSync } from "node:fs";
import { join } from "node:path";

function readCsv(path) {
  return readFileSync(path, "utf8").split("\n").filter(Boolean);
}

function loadInputs(profileDir) {
  const node_id_lines = readCsv(join(profileDir, "node_id.csv"));
  const cluster_id_lines = readCsv(join(profileDir, "cluster_id.csv"));
  const assignment_lines = readCsv(join(profileDir, "assignment.csv"));
  const degree_lines = readCsv(join(profileDir, "degree.csv"));
  const mincut_lines = readCsv(join(profileDir, "mincut.csv"));
  const node_id2id = new Map();
  for (let i = 0; i < node_id_lines.length; i++) {
    node_id2id.set(i, node_id_lines[i]);
  }
  const num_clusters = cluster_id_lines.length;
  const node2cluster = new Map();
  const clustering = new Map();        // insertion order matches first-appearance in assignment
  for (let iid = 0; iid < assignment_lines.length; iid++) {
    const c = parseInt(assignment_lines[iid], 10);
    if (c !== -1) {
      node2cluster.set(iid, c);
      if (!clustering.has(c)) clustering.set(c, []);
      clustering.get(c).push(iid);
    }
  }
  const deg = degree_lines.map((s) => parseInt(s, 10));
  const mcs = mincut_lines.map((s) => parseInt(s, 10));
  // probs as Map keyed by `r,c` -> int. Empty file → empty map.
  const probs = new Map();
  let edge_counts_text;
  try { edge_counts_text = readFileSync(join(profileDir, "edge_counts.csv"), "utf8"); }
  catch { edge_counts_text = ""; }
  for (const line of edge_counts_text.split("\n")) {
    if (!line) continue;
    const [r, c, w] = line.split(",").map((s) => parseInt(s, 10));
    probs.set(`${r},${c}`, w);
  }
  return { node_id2id, num_clusters, node2cluster, clustering, deg, mcs, probs };
}

function probsGet(probs, r, c) {
  return probs.get(`${r},${c}`) || 0;
}
function probsAdd(probs, r, c, delta) {
  const k = `${r},${c}`;
  probs.set(k, (probs.get(k) || 0) + delta);
}

function normalizeEdge(u, v) { return u <= v ? [u, v] : [v, u]; }
function edgeKey(u, v) { const [a, b] = normalizeEdge(u, v); return `${a},${b}`; }

function sortByDegThenIid(nodes, int_deg) {
  return nodes.slice().sort((a, b) => {
    const da = int_deg[a], db = int_deg[b];
    if (da !== db) return db - da;        // -int_deg ascending = int_deg descending
    return a - b;
  });
}

function generateCluster(cluster_nodes, k, deg, probs, node2cluster, cursor) {
  const n = cluster_nodes.length;
  if (n === 0 || k === 0) return new Map();
  k = Math.min(k, n - 1);
  const int_deg = deg.slice();
  const cluster_nodes_ordered = sortByDegThenIid(cluster_nodes, int_deg);
  let processed_nodes = [];               // tracked as ordered list; actual iteration order comes from trace
  const processed_set = new Set();
  const edges = new Map();

  function ensureEdgeCapacity(u, v) {
    const cu = node2cluster.get(u), cv = node2cluster.get(v);
    if (probsGet(probs, cu, cv) === 0 || int_deg[v] === 0) {
      int_deg[u] += 1;
      int_deg[v] += 1;
      probsAdd(probs, cu, cv, 1);
      probsAdd(probs, cv, cu, 1);
    }
  }
  function applyEdge(u, v) {
    const [a, b] = normalizeEdge(u, v);
    edges.set(`${a},${b}`, [a, b]);
    int_deg[u] -= 1;
    int_deg[v] -= 1;
    const cu = node2cluster.get(u), cv = node2cluster.get(v);
    probsAdd(probs, cu, cv, -1);
    probsAdd(probs, cv, cu, -1);
  }

  let i = 0;
  while (i <= k) {
    const u = cluster_nodes_ordered[i];
    const ent = cursor.next("set_iter");
    for (const v of ent.order) {
      ensureEdgeCapacity(u, v);
      applyEdge(u, v);
    }
    processed_set.add(u);
    processed_nodes.push(u);
    i += 1;
  }
  while (i < n) {
    const u = cluster_nodes_ordered[i];
    const processed_nodes_ordered = sortByDegThenIid(
      Array.from(processed_set), int_deg,
    );
    const candidates = new Set(processed_set);
    let ii = 0, iii = 0;
    while (ii < k && iii < processed_nodes_ordered.length) {
      const v = processed_nodes_ordered[iii];
      iii += 1;
      ensureEdgeCapacity(u, v);
      if (int_deg[v] === 0) continue;
      applyEdge(u, v);
      candidates.delete(v);
      ii += 1;
    }
    while (ii < k) {
      const list_cands = Array.from(candidates).sort((a, b) => a - b);
      const ent = cursor.next("np_choice");
      const v = list_cands[ent.idx];
      if (v !== ent.value) {
        throw new Error(
          `np_choice candidate mismatch: trace.value=${ent.value} ` +
          `js list_cands[${ent.idx}]=${v}`,
        );
      }
      ensureEdgeCapacity(u, v);
      applyEdge(u, v);
      candidates.delete(v);
      ii += 1;
    }
    processed_set.add(u);
    processed_nodes.push(u);
    i += 1;
  }
  for (let j = 0; j < deg.length; j++) deg[j] = int_deg[j];
  return edges;
}

function generateInternalEdges(clustering, mcs, deg, probs, node2cluster, cursor) {
  const edges = new Map();
  for (const [cid, cluster_nodes] of clustering.entries()) {
    const subEdges = generateCluster(
      cluster_nodes, mcs[cid], deg, probs, node2cluster, cursor,
    );
    for (const [k, v] of subEdges.entries()) edges.set(k, v);
  }
  return edges;
}

class TraceCursor {
  constructor(trace) { this.trace = trace; this.i = 0; }
  next(expected_site) {
    if (this.i >= this.trace.length) {
      throw new Error(`trace exhausted at site=${expected_site}`);
    }
    const ent = this.trace[this.i++];
    if (ent.site !== expected_site) {
      throw new Error(`trace site mismatch at i=${this.i - 1}: expected ${expected_site} got ${ent.site}`);
    }
    return ent;
  }
  done() { return this.i === this.trace.length; }
}

async function readStdin() {
  const chunks = [];
  for await (const ch of process.stdin) chunks.push(ch);
  return Buffer.concat(chunks).toString("utf8");
}

async function main() {
  const job = JSON.parse(await readStdin());
  if (job.mode !== "replay") {
    process.stdout.write(JSON.stringify({
      _error: "only mode=replay supported in ec_sbm kernel_check.mjs",
    }) + "\n");
    process.exit(2);
  }
  const inputs = loadInputs(job.profile_dir);
  const cursor = new TraceCursor(job.trace);
  const edges = generateInternalEdges(
    inputs.clustering, inputs.mcs, inputs.deg, inputs.probs,
    inputs.node2cluster, cursor,
  );
  if (!cursor.done()) {
    process.stderr.write(
      `WARN: trace not fully consumed, ${cursor.trace.length - cursor.i} entries remain\n`,
    );
  }
  const edge_list = Array.from(edges.values()).sort((a, b) => {
    if (a[0] !== b[0]) return a[0] - b[0];
    return a[1] - b[1];
  });
  process.stdout.write(JSON.stringify({
    edges: edge_list,
    deg_final: inputs.deg,
    trace_consumed: cursor.i,
    trace_total: cursor.trace.length,
  }) + "\n");
}

main().catch((e) => {
  process.stderr.write(String(e.stack || e) + "\n");
  process.exit(1);
});
