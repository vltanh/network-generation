// Node port of the JS micro-SBM sampler in vltanh.github.io/netgen/sbm.html.
// Reads a fixture JSON from stdin (same shape the C++ tool consumes), prints
// achieved invariants on stdout. Pair-by-pair logic mirrors the C++ kernel
// in tools/sbm/kernel_check.cpp; PRNG is the same LCG d3.randomLcg uses.
//
// Run:
//   node tools/sbm/kernel_check.mjs < fixture.json
//
// The point is to verify, on the same fixture, that the JS algorithm
// produces output meeting the same structural invariants the C++ and
// gt.generate_sbm hit. Different PRNG, same algorithm.

function lcg(seed) {
  // Match d3-random's randomLcg verbatim (32-bit signed LCG).
  let s = (seed > 0 && seed < 1) ? Math.floor(seed * 0xfffff) : Math.abs(seed) | 0;
  return () => {
    s = (Math.imul(s, 0x19660d) + 0x3c6ef35f) | 0;
    return ((s & 0x7fffffff) / 0x80000000);
  };
}

function popRandom(urn, rng) {
  const i = Math.floor(rng() * urn.length);
  const v = urn[i];
  urn[i] = urn[urn.length - 1];
  urn.pop();
  return [v, i];
}

function popAt(urn, i) {
  // Replay path: caller supplies the index, we honour it (no rng call).
  const v = urn[i];
  urn[i] = urn[urn.length - 1];
  urn.pop();
  return v;
}

function runJsSampler(fixture, seed, replay = null) {
  // If `replay` is given (an array of { i_a, i_b } per step), the
  // sampler bypasses its rng and consumes the supplied indices instead.
  // Same algorithm, deterministic stream.
  const { blocks, degrees, e_rs, num_blocks } = fixture;
  const rng = replay ? null : lcg(seed);
  const urns = [];
  for (let r = 0; r < num_blocks; r++) urns.push([]);
  for (let v = 0; v < blocks.length; v++) {
    const r = blocks[v];
    for (let k = 0; k < degrees[v]; k++) urns[r].push(v);
  }
  const ePairs = {};
  for (const [r, s, c] of e_rs) {
    if (r > s) continue;
    ePairs[`${r}|${s}`] = c;
  }
  const pairs = [];
  for (let r = 0; r < num_blocks; r++) {
    for (let s = r; s < num_blocks; s++) {
      const c = ePairs[`${r}|${s}`] || 0;
      if (c > 0) pairs.push([r, s, c]);
    }
  }
  const edges = [];
  const trace = [];
  let step = 0;
  for (const [r, s, c] of pairs) {
    const mrs = (r === s) ? Math.floor(c / 2) : c;
    const ers = (r === s) ? 2 * mrs : mrs;
    if (urns[r].length < ers) {
      throw new Error(`has_n fail at pair (${r},${s}): urn r=${urns[r].length} < ers=${ers}`);
    }
    if (r !== s && urns[s].length < ers) {
      throw new Error(`has_n fail at pair (${r},${s}): urn s=${urns[s].length} < ers=${ers}`);
    }
    for (let k = 0; k < mrs; k++) {
      const r_size = urns[r].length;
      const s_size = (r === s) ? urns[r].length : urns[s].length;
      let ia, ib, u, v;
      if (replay) {
        ia = replay[step].i_a;
        ib = replay[step].i_b;
        u = popAt(urns[r], ia);
        v = popAt(r === s ? urns[r] : urns[s], ib);
      } else {
        const a = popRandom(urns[r], rng);
        const b = popRandom(r === s ? urns[r] : urns[s], rng);
        u = a[0]; ia = a[1]; v = b[0]; ib = b[1];
      }
      edges.push([u, v]);
      trace.push({ step, r, s, urnR: r_size, i_a: ia, urnS: s_size, i_b: ib, u, v });
      step++;
    }
  }
  return { edges, trace };
}

function achieved(blocks, degrees, edges, B) {
  const e_rs = Array.from({ length: B }, () => Array(B).fill(0));
  const ach_deg = Array(blocks.length).fill(0);
  let multi = 0, loops = 0;
  const seen = new Set();
  for (const [u, v] of edges) {
    ach_deg[u]++;
    ach_deg[v]++;
    const ru = blocks[u], rv = blocks[v];
    if (ru === rv) e_rs[ru][ru] += 2;
    else { e_rs[ru][rv] += 1; e_rs[rv][ru] += 1; }
    if (u === v) loops++;
    else {
      const lo = Math.min(u, v), hi = Math.max(u, v);
      const key = `${lo}|${hi}`;
      if (seen.has(key)) multi++; else seen.add(key);
    }
  }
  return { e_rs, ach_deg, loops, multi, edges: edges.length };
}

function arraysEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function matEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (!arraysEqual(a[i], b[i])) return false;
  return true;
}

async function readStdin() {
  let chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf-8");
}

const src = await readStdin();
const fx = JSON.parse(src);
const B = fx.num_blocks;
const expectedE = Array.from({ length: B }, () => Array(B).fill(0));
for (const [r, s, c] of fx.e_rs) expectedE[r][s] = c;

const replay = Array.isArray(fx.replay) ? fx.replay : null;
const { edges, trace } = runJsSampler(fx, fx.seed || 1, replay);
const ach = achieved(fx.blocks, fx.degrees, edges, B);
const e_match = matEqual(expectedE, ach.e_rs);
const d_match = arraysEqual(fx.degrees, ach.ach_deg);
const total_match = (ach.edges === expectedE.flat().reduce((a, b) => a + b, 0) / 2);

const out = {
  seed: fx.seed,
  mode: replay ? "replay" : "rng",
  edges_count: ach.edges,
  loops: ach.loops,
  multi: ach.multi,
  e_rs_exact: e_match,
  deg_exact: d_match,
  edge_total_exact: total_match,
  trace_first5: trace.slice(0, 5),
  edges,
};
process.stdout.write(JSON.stringify(out) + "\n");
