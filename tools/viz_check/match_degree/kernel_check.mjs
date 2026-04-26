// Node port of the five match-degree algorithms in
// vltanh.github.io/netgen/matcher.html. Reads a JSON fixture from
// stdin (iids, target_deg, exist_neighbor, seed, algo) and prints
// achieved degrees + edges on stdout.
//
// Run:
//   node tools/match_degree/kernel_check.mjs < cell.json
//
// The JS algorithms ported here are the same five rendered by
// matcher.html: greedy / true_greedy / random_greedy / rewire /
// hybrid. The PRNG is the same LCG matcher.html's makeLCG uses
// (1664525 / 1013904223). Python and JS use independent PRNGs;
// determinism cross-checks are limited to greedy and true_greedy
// (no random calls), structural-only on the rest.

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

let edges;
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

const ach = achievedFrom(payload, edges);
const out = {
  algo,
  seed,
  edges,
  achieved_deg: ach,
  simple_graph: isSimple(edges, payload.exist_neighbor),
};
process.stdout.write(JSON.stringify(out) + "\n");
