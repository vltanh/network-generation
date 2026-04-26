// Node port of the five match-degree algorithms in
// vltanh.github.io/netgen/matcher.html, plus replay-mode versions of
// the three randomized algos (random_greedy / rewire / hybrid) that
// consume an externally-supplied PRNG trace and produce byte-equal
// edges to canonical src/match_degree.py.
//
// Reads a JSON fixture from stdin (iids, target_deg, exist_neighbor,
// seed, algo) and prints achieved degrees + edges on stdout. Pass
// `mode: "replay"` plus `trace: [...]` to drive the canonical-
// equivalent algorithms; otherwise the page algorithms run via LCG.
//
// Run:
//   node tools/viz_check/match_degree/kernel_check.mjs < cell.json
//
// The page algos mirror the d3-style LCG used in matcher.html. The
// replay algos mirror src/match_degree.py exactly: same control flow,
// same set/list bookkeeping, same tie-breaks. Trace entries cover
// shuffle (j_seq), choices (indices), randrange (value), random
// (value).

function makeLCG(seed) {
  let s = (seed >>> 0) || 1;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function clone(obj) { return JSON.parse(JSON.stringify(obj)); }

function nodesWithResidual(r) {
  const out = [];
  for (const k in r) if (r[k] > 0) out.push(parseInt(k, 10));
  return out;
}

function adjFrom(payload) {
  const adj = {};
  for (const k of payload.iids) adj[k] = new Set();
  for (const k in payload.exist_neighbor) {
    const u = parseInt(k, 10);
    adj[u] = new Set(payload.exist_neighbor[k]);
  }
  return adj;
}

function residualFrom(payload) {
  const out = {};
  for (const k in payload.target_deg) out[parseInt(k, 10)] = payload.target_deg[k];
  return out;
}

function normEdge(u, v) { return u < v ? [u, v] : [v, u]; }

// ── greedy ─────────────────────────────────────────────────────
// Mirrors matcher.html's runGreedy: smallest-id partner, no PRNG.
function runGreedy(payload) {
  const residual = residualFrom(payload);
  const adj = adjFrom(payload);
  const edges = [];
  const abandoned = new Set();
  while (true) {
    const remaining = nodesWithResidual(residual).filter(n => !abandoned.has(n));
    if (remaining.length === 0) break;
    remaining.sort((a, b) => (residual[b] - residual[a]) || (a - b));
    const u = remaining[0];
    while (residual[u] > 0) {
      const candidates = [];
      for (const k in residual) {
        const v = parseInt(k, 10);
        if (v === u) continue;
        if (residual[v] <= 0) continue;
        if (adj[u].has(v)) continue;
        candidates.push(v);
      }
      if (candidates.length === 0) {
        abandoned.add(u);
        residual[u] = 0;
        break;
      }
      candidates.sort((a, b) => a - b);
      const v = candidates[0];
      adj[u].add(v); adj[v].add(u);
      edges.push(normEdge(u, v));
      residual[u]--; residual[v]--;
    }
  }
  return edges;
}

// ── true_greedy ────────────────────────────────────────────────
// Mirrors matcher.html: argmax-degree partner. Track current degrees
// from input edges (we don't have them here so we count adjacency
// size as a proxy; in matcher.html this is `deg` initialised from
// DEGREES + edges so far). The harness passes exist_neighbor sized
// per the input, so |adj[v]| matches the algorithm's `deg`.
function runTrueGreedy(payload) {
  const residual = residualFrom(payload);
  const adj = adjFrom(payload);
  const deg = {};
  for (const k of payload.iids) deg[k] = adj[k] ? adj[k].size : 0;
  const edges = [];
  const abandoned = new Set();
  while (true) {
    const remaining = nodesWithResidual(residual).filter(n => !abandoned.has(n));
    if (remaining.length === 0) break;
    remaining.sort((a, b) => (residual[b] - residual[a]) || (a - b));
    const u = remaining[0];
    while (residual[u] > 0) {
      const valid = [];
      for (const k in residual) {
        const v = parseInt(k, 10);
        if (v === u) continue;
        if (residual[v] <= 0) continue;
        if (adj[u].has(v)) continue;
        valid.push(v);
      }
      if (valid.length === 0) {
        abandoned.add(u);
        residual[u] = 0;
        break;
      }
      valid.sort((a, b) => (deg[b] - deg[a]) || (a - b));
      const v = valid[0];
      adj[u].add(v); adj[v].add(u);
      edges.push(normEdge(u, v));
      residual[u]--; residual[v]--;
      deg[u]++; deg[v]++;
    }
  }
  return edges;
}

// ── random_greedy ──────────────────────────────────────────────
// Weighted-random u (proportional to residual), uniform v from the
// valid set. Same as matcher.html's runRandomGreedy.
function runRandomGreedy(payload, seed) {
  const rng = makeLCG(seed);
  const residual = residualFrom(payload);
  const adj = adjFrom(payload);
  const edges = [];
  const abandoned = new Set();
  function weightedPick(items, weights) {
    let total = 0;
    for (const w of weights) total += w;
    if (total <= 0) return null;
    let r = rng() * total;
    for (let i = 0; i < items.length; i++) {
      r -= weights[i];
      if (r <= 0) return items[i];
    }
    return items[items.length - 1];
  }
  let safety = 50000;
  while (safety-- > 0) {
    const remaining = nodesWithResidual(residual).filter(n => !abandoned.has(n));
    if (remaining.length === 0) break;
    const weights = remaining.map(n => residual[n]);
    const u = weightedPick(remaining, weights);
    const valid = [];
    for (const k in residual) {
      const v = parseInt(k, 10);
      if (v === u) continue;
      if (residual[v] <= 0) continue;
      if (adj[u].has(v)) continue;
      valid.push(v);
    }
    if (valid.length === 0) {
      abandoned.add(u);
      residual[u] = 0;
      continue;
    }
    const v = valid[Math.floor(rng() * valid.length)];
    adj[u].add(v); adj[v].add(u);
    edges.push(normEdge(u, v));
    residual[u]--; residual[v]--;
  }
  return edges;
}

// ── rewire ─────────────────────────────────────────────────────
// Configuration-model pairing + 2-opt repair. Mirrors matcher.html.
// keepResidual=true leaves residuals > 0 for hybrid's phase 2.
function runRewire(payload, seed, keepResidual) {
  const rng = makeLCG(seed);
  const residual = residualFrom(payload);
  const adj = adjFrom(payload);
  const edges = [];
  const stubs = [];
  for (const k in residual) {
    const u = parseInt(k, 10);
    for (let i = 0; i < residual[u]; i++) stubs.push(u);
  }
  if (stubs.length % 2 !== 0) stubs.pop();
  for (let i = stubs.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [stubs[i], stubs[j]] = [stubs[j], stubs[i]];
  }
  const invalid = [];
  for (let i = 0; i + 1 < stubs.length; i += 2) {
    const u = stubs[i], v = stubs[i + 1];
    if (u === v || adj[u].has(v)) {
      invalid.push([u, v]);
    } else {
      adj[u].add(v); adj[v].add(u);
      edges.push(normEdge(u, v));
      residual[u]--; residual[v]--;
    }
  }
  // 2-opt repair, multiple passes.
  const MAX_RETRIES = 10;
  for (let attempt = 0; attempt < MAX_RETRIES && invalid.length > 0; attempt++) {
    let lastLen = invalid.length;
    let recycle = lastLen;
    while (invalid.length > 0) {
      recycle--;
      if (recycle < 0) {
        if (invalid.length < lastLen) {
          lastLen = invalid.length;
          recycle = lastLen;
        } else {
          break;
        }
      }
      const [u, v] = invalid.shift();
      if (edges.length === 0) {
        invalid.push([u, v]);
        continue;
      }
      const idx = Math.floor(rng() * edges.length);
      const [x, y] = edges[idx];
      let new1, new2;
      if (rng() < 0.5) {
        new1 = normEdge(u, x);
        new2 = normEdge(v, y);
      } else {
        new1 = normEdge(u, y);
        new2 = normEdge(v, x);
      }
      const isValid = (e) => {
        const [a, b] = e;
        if (a === b) return false;
        if (adj[a].has(b)) return false;
        return true;
      };
      const k1 = `${new1[0]}-${new1[1]}`;
      const k2 = `${new2[0]}-${new2[1]}`;
      if (k1 === k2) {
        invalid.push([u, v]);
        continue;
      }
      if (isValid(new1) && isValid(new2)) {
        // Remove the old (x, y) edge.
        adj[x].delete(y); adj[y].delete(x);
        edges.splice(idx, 1);
        adj[new1[0]].add(new1[1]); adj[new1[1]].add(new1[0]);
        adj[new2[0]].add(new2[1]); adj[new2[1]].add(new2[0]);
        edges.push(new1);
        edges.push(new2);
        residual[u]--; residual[v]--;
      } else {
        invalid.push([u, v]);
      }
    }
  }
  if (!keepResidual) {
    // Standalone rewire: leftover residual is "abandoned" (no edges added).
    // We just leave residual as-is; the harness reads target - achieved.
  }
  return edges;
}

// ── hybrid ─────────────────────────────────────────────────────
// Phase 1: rewire with keepResidual=true. Phase 2: true_greedy on
// whatever residual remains.
function runHybrid(payload, seed) {
  const phase1Edges = runRewire(payload, seed, true);
  // Build residual + adj after phase 1.
  const residual = residualFrom(payload);
  const adj = adjFrom(payload);
  for (const [u, v] of phase1Edges) {
    adj[u].add(v); adj[v].add(u);
    residual[u]--; residual[v]--;
  }
  // Phase 2: true_greedy on the leftover residual + updated adj.
  const deg = {};
  for (const k of payload.iids) deg[k] = adj[k].size;
  const edges = phase1Edges.slice();
  const abandoned = new Set();
  while (true) {
    const remaining = nodesWithResidual(residual).filter(n => !abandoned.has(n));
    if (remaining.length === 0) break;
    remaining.sort((a, b) => (residual[b] - residual[a]) || (a - b));
    const u = remaining[0];
    while (residual[u] > 0) {
      const valid = [];
      for (const k in residual) {
        const v = parseInt(k, 10);
        if (v === u) continue;
        if (residual[v] <= 0) continue;
        if (adj[u].has(v)) continue;
        valid.push(v);
      }
      if (valid.length === 0) {
        abandoned.add(u);
        residual[u] = 0;
        break;
      }
      valid.sort((a, b) => (deg[b] - deg[a]) || (a - b));
      const v = valid[0];
      adj[u].add(v); adj[v].add(u);
      edges.push(normEdge(u, v));
      residual[u]--; residual[v]--;
      deg[u]++; deg[v]++;
    }
  }
  return edges;
}

// ── trace consumer ─────────────────────────────────────────────
function makeTrace(trace) {
  let cursor = 0;
  return {
    next(expectSite) {
      if (cursor >= trace.length) {
        throw new Error(`trace exhausted at expected site=${expectSite}`);
      }
      const e = trace[cursor++];
      if (e.site !== expectSite) {
        throw new Error(
          `trace mismatch at ${cursor - 1}: expected=${expectSite} got=${e.site}`,
        );
      }
      return e;
    },
    consumed: () => cursor,
    length: () => trace.length,
  };
}

function edgeKey(u, v) { return u < v ? `${u}-${v}` : `${v}-${u}`; }

function _adjFromExist(payload) {
  const adj = new Map();
  for (const k of payload.iids) adj.set(k, new Set());
  for (const k in payload.exist_neighbor) {
    const u = parseInt(k, 10);
    if (!adj.has(u)) adj.set(u, new Set());
    for (const v of payload.exist_neighbor[k]) adj.get(u).add(v);
  }
  return adj;
}

// ── replay: greedy ─────────────────────────────────────────────
// Mirrors src/match_degree.py:match_missing_degrees_greedy. Static
// max-heap, lazy delete on deg-mismatch, batch partner picks per u via
// sorted(non-neighbors)[:avail_k]. Different from runGreedy (page algo),
// which dynamically re-evaluates max-residual every outer iter.
function _heapPushMin(heap, item) {
  // Tuple item: [a, b]; min-heap on lexicographic. Use sort for clarity
  // (small heaps; the canonical mirror semantics matters more than
  // asymptotics here).
  heap.push(item);
  heap.sort((x, y) => (x[0] - y[0]) || (x[1] - y[1]));
}
function _heapPopMin(heap) {
  return heap.shift();
}

function runGreedyReplay(payload, _trace) {
  // available_node_degrees: insertion-order dict, keyed sorted iid asc.
  const targetEntries = Object.entries(payload.target_deg)
    .map(([k, v]) => [parseInt(k, 10), Number(v)])
    .filter(([_, v]) => v > 0)
    .sort((a, b) => a[0] - b[0]);
  const availDeg = new Map(targetEntries);
  const availSet = new Set(targetEntries.map(([k, _]) => k));
  const adj = _adjFromExist(payload);
  // Initial heap (no re-push; lazy delete).
  const heap = [];
  for (const [n, d] of targetEntries) heap.push([-d, n]);
  heap.sort((x, y) => (x[0] - y[0]) || (x[1] - y[1]));
  const edges = new Set();
  while (heap.length > 0) {
    const [_, u] = _heapPopMin(heap);
    if (!availDeg.has(u)) continue;
    const invalid = new Set(adj.get(u) || []);
    invalid.add(u);
    const nonNeighbors = [];
    for (const n of availSet) if (!invalid.has(n)) nonNeighbors.push(n);
    const availK = Math.min(availDeg.get(u), nonNeighbors.length);
    nonNeighbors.sort((a, b) => a - b);
    for (let k = 0; k < availK; k++) {
      const edgeEnd = nonNeighbors[k];
      edges.add(edgeKey(u, edgeEnd));
      if (!adj.has(u)) adj.set(u, new Set());
      if (!adj.has(edgeEnd)) adj.set(edgeEnd, new Set());
      adj.get(u).add(edgeEnd);
      adj.get(edgeEnd).add(u);
      availDeg.set(edgeEnd, availDeg.get(edgeEnd) - 1);
      if (availDeg.get(edgeEnd) === 0) {
        availSet.delete(edgeEnd);
        availDeg.delete(edgeEnd);
      }
    }
    availDeg.delete(u);
    availSet.delete(u);
  }
  return Array.from(edges).map(s => s.split("-").map(Number));
}

// ── replay: true_greedy ────────────────────────────────────────
// Mirrors src/match_degree.py:match_missing_degrees_true_greedy.
// Dynamic heap with re-push after every single edge; lazy-delete on
// stale (-deg, n) tuples. Different from runTrueGreedy (page algo),
// which processes u's full burst before reconsidering.
function runTrueGreedyReplay(payload, _trace) {
  const targetEntries = Object.entries(payload.target_deg)
    .map(([k, v]) => [parseInt(k, 10), Number(v)])
    .filter(([_, v]) => v > 0)
    .sort((a, b) => a[0] - b[0]);
  const currentDeg = new Map(targetEntries);
  const adj = _adjFromExist(payload);
  const heap = [];
  for (const [n, d] of targetEntries) heap.push([-d, n]);
  heap.sort((x, y) => (x[0] - y[0]) || (x[1] - y[1]));
  const edges = new Set();
  while (heap.length > 0) {
    const [negDeg, u] = _heapPopMin(heap);
    const degU = -negDeg;
    if (!currentDeg.has(u) || currentDeg.get(u) !== degU) continue;
    const invalid = adj.get(u) || new Set();
    const validTargets = [];
    for (const n of currentDeg.keys()) {
      if (n === u) continue;
      if (invalid.has(n)) continue;
      validTargets.push(n);
    }
    if (validTargets.length === 0) {
      currentDeg.delete(u);
      continue;
    }
    // v = max(valid_targets, key=lambda x: (current_degrees[x], -x))
    let v = null;
    let vDeg = -1;
    for (const n of validTargets) {
      const d = currentDeg.get(n);
      if (d > vDeg || (d === vDeg && (v === null || n < v))) {
        v = n; vDeg = d;
      }
    }
    edges.add(edgeKey(u, v));
    if (!adj.has(u)) adj.set(u, new Set());
    if (!adj.has(v)) adj.set(v, new Set());
    adj.get(u).add(v); adj.get(v).add(u);
    currentDeg.set(u, currentDeg.get(u) - 1);
    currentDeg.set(v, currentDeg.get(v) - 1);
    if (currentDeg.get(u) > 0) {
      _heapPushMin(heap, [-currentDeg.get(u), u]);
    } else {
      currentDeg.delete(u);
    }
    if (currentDeg.get(v) > 0) {
      _heapPushMin(heap, [-currentDeg.get(v), v]);
    } else if (currentDeg.has(v)) {
      currentDeg.delete(v);
    }
  }
  return Array.from(edges).map(s => s.split("-").map(Number));
}

// ── replay: random_greedy ──────────────────────────────────────
// Mirrors src/match_degree.py:match_missing_degrees_random_greedy.
function runRandomGreedyReplay(payload, trace) {
  const tr = makeTrace(trace);
  // available_degrees = {iid: deg}, sorted-by-iid, deg > 0.
  const targetEntries = Object.entries(payload.target_deg)
    .map(([k, v]) => [parseInt(k, 10), Number(v)])
    .filter(([_, v]) => v > 0)
    .sort((a, b) => a[0] - b[0]);
  const availableDegrees = new Map(targetEntries);
  const availableNodes = targetEntries.map(([k, _]) => k);
  const adj = _adjFromExist(payload);
  const edges = new Set();
  const stuck = new Set();
  while (availableNodes.length > 0) {
    // weights = [available_degrees[n] for n in available_nodes]
    const uEntry = tr.next("choices");
    const uIdx = uEntry.indices[0];
    const u = availableNodes[uIdx];
    const invalid = adj.get(u);
    const validTargets = [];
    for (const n of availableNodes) {
      if (n === u) continue;
      if (invalid.has(n)) continue;
      validTargets.push(n);
    }
    if (validTargets.length === 0) {
      const i = availableNodes.indexOf(u);
      availableNodes.splice(i, 1);
      stuck.add(u);
      continue;
    }
    const vEntry = tr.next("choices");
    const vIdx = vEntry.indices[0];
    const v = validTargets[vIdx];
    edges.add(edgeKey(u, v));
    adj.get(u).add(v);
    adj.get(v).add(u);
    availableDegrees.set(u, availableDegrees.get(u) - 1);
    availableDegrees.set(v, availableDegrees.get(v) - 1);
    if (availableDegrees.get(u) === 0) {
      const i = availableNodes.indexOf(u);
      availableNodes.splice(i, 1);
    }
    if (availableDegrees.get(v) === 0) {
      const i = availableNodes.indexOf(v);
      if (i >= 0) availableNodes.splice(i, 1);
    }
  }
  return Array.from(edges).map(s => s.split("-").map(Number));
}

// ── replay: rewire ─────────────────────────────────────────────
// Mirrors src/match_degree.py:match_missing_degrees_rewire +
// graph_utils.run_rewire_attempts. Returns {validEdges, invalidEdges,
// adj} so hybrid can chain.
function _runRewireReplayCore(payload, trace, adj) {
  const tr = makeTrace(trace);
  // stubs: sorted by iid, [iid] * deg.
  const stubs = [];
  const targetEntries = Object.entries(payload.target_deg)
    .map(([k, v]) => [parseInt(k, 10), Number(v)])
    .sort((a, b) => a[0] - b[0]);
  for (const [iid, deg] of targetEntries) {
    for (let i = 0; i < deg; i++) stubs.push(iid);
  }
  if (stubs.length % 2 !== 0) stubs.pop();
  // Replay shuffle.
  const sh = tr.next("shuffle");
  if (sh.n !== stubs.length) {
    throw new Error(`shuffle n mismatch: trace ${sh.n} vs stubs ${stubs.length}`);
  }
  for (let i = stubs.length - 1, k = 0; i >= 1; i--, k++) {
    const j = sh.j_seq[k];
    [stubs[i], stubs[j]] = [stubs[j], stubs[i]];
  }
  const validEdges = new Set();
  const validPool = [];
  const invalidEdges = [];
  for (let i = 0; i < stubs.length; i += 2) {
    const u = stubs[i], v = stubs[i + 1];
    const k = edgeKey(u, v);
    const existsAdj = adj.get(u) && adj.get(u).has(v);
    if (u === v || validEdges.has(k) || existsAdj) {
      invalidEdges.push([Math.min(u, v), Math.max(u, v)]);
    } else {
      validEdges.add(k);
    }
  }
  // valid_pool = sorted(valid_edges). Edges as (u, v) tuples lex-sorted.
  for (const ek of Array.from(validEdges).sort((a, b) => {
    const [a1, a2] = a.split("-").map(Number);
    const [b1, b2] = b.split("-").map(Number);
    return (a1 - b1) || (a2 - b2);
  })) {
    const [a, b] = ek.split("-").map(Number);
    validPool.push([a, b]);
  }
  function isValid(e) {
    const [u, v] = e;
    if (u === v) return false;
    if (validEdges.has(edgeKey(u, v))) return false;
    if (adj.get(u) && adj.get(u).has(v)) return false;
    return true;
  }
  // run_rewire_attempts loop, max_retries=10.
  const MAX = 10;
  for (let attempt = 0; attempt < MAX; attempt++) {
    if (invalidEdges.length === 0) break;
    let lastRecycle = invalidEdges.length;
    let recycle = lastRecycle;
    while (invalidEdges.length > 0) {
      recycle--;
      if (recycle < 0) {
        if (invalidEdges.length < lastRecycle) {
          lastRecycle = invalidEdges.length;
          recycle = lastRecycle;
        } else {
          break;
        }
      }
      const e1 = invalidEdges.shift();
      // process_one_edge:
      if (validPool.length === 0) {
        invalidEdges.push(e1);
        break;  // returned True
      }
      const idxEntry = tr.next("randrange");
      const idx = idxEntry.value;
      const e2 = validPool[idx];
      const coinEntry = tr.next("random");
      const coin = coinEntry.value;
      let newE1, newE2;
      if (coin < 0.5) {
        newE1 = [Math.min(e1[0], e2[0]), Math.max(e1[0], e2[0])];
        newE2 = [Math.min(e1[1], e2[1]), Math.max(e1[1], e2[1])];
      } else {
        newE1 = [Math.min(e1[0], e2[1]), Math.max(e1[0], e2[1])];
        newE2 = [Math.min(e1[1], e2[0]), Math.max(e1[1], e2[0])];
      }
      const k1 = edgeKey(newE1[0], newE1[1]);
      const k2 = edgeKey(newE2[0], newE2[1]);
      if (isValid(newE1) && isValid(newE2) && k1 !== k2) {
        validEdges.delete(edgeKey(e2[0], e2[1]));
        validPool[idx] = validPool[validPool.length - 1];
        validPool.pop();
        validEdges.add(k1);
        validEdges.add(k2);
        validPool.push(newE1);
        validPool.push(newE2);
      } else {
        invalidEdges.push(e1);
      }
    }
  }
  return { validEdges, invalidEdges, adj };
}

function runRewireReplay(payload, trace) {
  const adj = _adjFromExist(payload);
  const { validEdges } = _runRewireReplayCore(payload, trace, adj);
  return Array.from(validEdges).map(s => s.split("-").map(Number));
}

// ── replay: hybrid ─────────────────────────────────────────────
// Mirrors src/match_degree.py:match_missing_degrees_hybrid: rewire
// then true_greedy on the remaining out-degrees.
function runHybridReplay(payload, trace) {
  const adj = _adjFromExist(payload);
  const { validEdges, invalidEdges } = _runRewireReplayCore(payload, trace, adj);
  if (invalidEdges.length === 0) {
    return Array.from(validEdges).map(s => s.split("-").map(Number));
  }
  // remaining_out_degs = {n: 0 for n in sorted(out_degs.keys())}
  const targetKeys = Object.keys(payload.target_deg)
    .map(k => parseInt(k, 10))
    .sort((a, b) => a - b);
  const remaining = new Map();
  for (const n of targetKeys) remaining.set(n, 0);
  for (const [u, v] of invalidEdges) {
    remaining.set(u, remaining.get(u) + 1);
    remaining.set(v, remaining.get(v) + 1);
  }
  // Strip zeros, preserving order.
  const remainingFiltered = new Map();
  for (const [k, v] of remaining) if (v > 0) remainingFiltered.set(k, v);
  // Update adj with valid_edges before true_greedy.
  for (const ek of validEdges) {
    const [u, v] = ek.split("-").map(Number);
    if (!adj.has(u)) adj.set(u, new Set());
    if (!adj.has(v)) adj.set(v, new Set());
    adj.get(u).add(v); adj.get(v).add(u);
  }
  // true_greedy on remainingFiltered + adj (mirrors canonical heap +
  // (degree desc, id asc) tie-break).
  const currentDeg = new Map(remainingFiltered);
  // current_degrees in canonical Python is keyed by the input dict's
  // iteration order, which here is sorted-iid. Heap pop gives largest
  // degree first, ties by smallest iid via tuple ordering.
  const greedyEdges = new Set();
  const stuck = new Set();
  while (currentDeg.size > 0) {
    // Pop the max-residual node, tiebreak id asc.
    let best = null;
    let bestDeg = -1;
    for (const [n, d] of currentDeg) {
      if (d > bestDeg || (d === bestDeg && n < best)) {
        best = n; bestDeg = d;
      }
    }
    const u = best;
    const invalid = adj.get(u) || new Set();
    const validTargets = [];
    for (const n of currentDeg.keys()) {
      if (n === u) continue;
      if (invalid.has(n)) continue;
      validTargets.push(n);
    }
    if (validTargets.length === 0) {
      stuck.add(u);
      currentDeg.delete(u);
      continue;
    }
    // v = max(valid_targets, key=lambda x: (current_degrees[x], -x))
    let v = null;
    let vDeg = -1;
    for (const n of validTargets) {
      const d = currentDeg.get(n);
      if (d > vDeg || (d === vDeg && (v === null || n < v))) {
        v = n; vDeg = d;
      }
    }
    greedyEdges.add(edgeKey(u, v));
    if (!adj.has(u)) adj.set(u, new Set());
    if (!adj.has(v)) adj.set(v, new Set());
    adj.get(u).add(v); adj.get(v).add(u);
    currentDeg.set(u, currentDeg.get(u) - 1);
    currentDeg.set(v, currentDeg.get(v) - 1);
    if (currentDeg.get(u) === 0) currentDeg.delete(u);
    if (currentDeg.get(v) === 0) currentDeg.delete(v);
  }
  // Union with validEdges.
  const all = new Set(validEdges);
  for (const e of greedyEdges) all.add(e);
  return Array.from(all).map(s => s.split("-").map(Number));
}

// ── runner ─────────────────────────────────────────────────────
function isSimple(edges, exist) {
  const seen = new Set();
  for (const [u, v] of edges) {
    if (u === v) return false;
    const k = `${u}-${v}`;
    if (seen.has(k)) return false;
    seen.add(k);
    const exU = exist[u] || [];
    if (exU.includes(v)) return false;
  }
  return true;
}

function achievedFrom(payload, edges) {
  const out = {};
  for (const k of payload.iids) out[k] = 0;
  for (const [u, v] of edges) { out[u]++; out[v]++; }
  return out;
}

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf-8");
}

const src = await readStdin();
const payload = JSON.parse(src);
const algo = payload.algo;
const seed = payload.seed;
const mode = payload.mode || "rng";

let edges;
if (mode === "replay") {
  const trace = payload.trace || [];
  switch (algo) {
    case "greedy":         edges = runGreedyReplay(payload, trace); break;
    case "true_greedy":    edges = runTrueGreedyReplay(payload, trace); break;
    case "random_greedy":  edges = runRandomGreedyReplay(payload, trace); break;
    case "rewire":         edges = runRewireReplay(payload, trace); break;
    case "hybrid":         edges = runHybridReplay(payload, trace); break;
    default:
      process.stderr.write(`unknown algo: ${algo}\n`);
      process.exit(2);
  }
} else {
  switch (algo) {
    case "greedy":         edges = runGreedy(payload); break;
    case "true_greedy":    edges = runTrueGreedy(payload); break;
    case "random_greedy":  edges = runRandomGreedy(payload, seed); break;
    case "rewire":         edges = runRewire(payload, seed, false); break;
    case "hybrid":         edges = runHybrid(payload, seed); break;
    default:
      process.stderr.write(`unknown algo: ${algo}\n`);
      process.exit(2);
  }
}

const ach = achievedFrom(payload, edges);
const out = {
  algo,
  seed,
  edges,
  achieved_deg: ach,
  simple_graph: isSimple(edges, payload.exist_neighbor),
};
process.stdout.write(JSON.stringify(out) + "\n");
