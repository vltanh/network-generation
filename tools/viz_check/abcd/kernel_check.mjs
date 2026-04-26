// Faithful JS port of the ABCD config-model + populate_clusters pipeline,
// driven by a trace recorded by tools/viz_check/abcd/instrumented/instrumented.jl.
//
// Reads {w, s, xi, n_outliers, trace} on stdin, prints {edges, clusters}
// on stdout. The output edge set must match the canonical Julia sampler
// byte-for-byte (after sorting).
//
// The trace records the resolved value of every randomized site:
//   outlier_sample, vertex_assign, randround, shuffle, uniform,
//   uniform_int, rand_set
// Each pure operation in this file consumes the next trace entry of the
// expected `site` type and uses its recorded value, then performs the
// surrounding deterministic bookkeeping verbatim from the Julia source.

import { readFileSync } from "node:fs";

function readAllStdin() {
  return readFileSync(0, "utf-8");
}

function makeTrace(entries) {
  let cursor = 0;
  return {
    next(expectSite) {
      if (cursor >= entries.length) {
        throw new Error(`trace exhausted at expected ${expectSite}`);
      }
      const e = entries[cursor++];
      if (e.site !== expectSite) {
        throw new Error(
          `trace mismatch at ${cursor - 1}: expected=${expectSite} got=${e.site}`,
        );
      }
      return e;
    },
    consumed: () => cursor,
    length: () => entries.length,
  };
}

function ekey(a, b) {
  return a < b ? `${a}-${b}` : `${b}-${a}`;
}
function epair(a, b) {
  return a < b ? [a, b] : [b, a];
}
function parseKey(k) {
  return k.split("-").map(Number);
}

// ---------------------------------------------------------------------------
// populate_clusters replay.
// ---------------------------------------------------------------------------
//
// w is sorted descending. For each non-outlier vertex i in 1..n the trace
// entry "vertex_assign" tells us which cluster index loc the vertex was
// assigned to. For hasoutliers, the trace entry "outlier_sample" tells us
// which vertex indices got assigned to cluster 1.
function populateClustersReplay(w, s, xi, hasOutliers, tr) {
  const n = w.length;
  const clusters = new Array(n).fill(-1);
  if (hasOutliers) {
    const e = tr.next("outlier_sample");
    for (const idx1 of e.picked) clusters[idx1 - 1] = 1;
  }
  for (let i1 = 1; i1 <= n; i1++) {
    if (clusters[i1 - 1] !== -1) continue;
    const e = tr.next("vertex_assign");
    clusters[i1 - 1] = e.picked;
  }
  return clusters;
}

// ---------------------------------------------------------------------------
// config_model replay.
// ---------------------------------------------------------------------------
//
// Mirrors externals/abcd/src/graph_sampler.jl::config_model line-for-line.
// All randomized choices are sourced from the trace; every other piece of
// state evolves identically to canonical.
function configModelReplay(clusters, w, s, xi, hasOutliers, tr) {
  // w may have been bumped by 1 inside config_model when randround forces an
  // odd-degree fix on the max-weight vertex of a cluster. We mutate a local
  // copy to mirror canonical's `w[cluster[maxw_idx]] += 1` behavior.
  w = w.slice();

  const numClusters = s.length;
  const clusterWeight = new Array(numClusters).fill(0);
  for (let i = 0; i < w.length; i++) clusterWeight[clusters[i] - 1] += w[i];
  const totalWeight = clusterWeight.reduce((a, b) => a + b, 0);
  const xig = xi;
  const wInternalRaw = w.map((wi) => wi * (1 - xig));
  if (hasOutliers) {
    for (let i = 0; i < clusters.length; i++) {
      if (clusters[i] === 1) wInternalRaw[i] = 0;
    }
  }

  const clusterList = Array.from({ length: numClusters }, () => []);
  for (let i = 0; i < clusters.length; i++) {
    clusterList[clusters[i] - 1].push(i + 1);
  }

  const edges = new Set();
  const wInternal = new Array(w.length).fill(0);

  for (let cidx0 = 0; cidx0 < numClusters; cidx0++) {
    const cluster = clusterList[cidx0]; // 1-based vertex ids
    // maxw_idx in Julia is the 1-based index into cluster of the max wInternalRaw.
    let maxIdx0 = 0;
    let maxVal = -Infinity;
    for (let k = 0; k < cluster.length; k++) {
      const val = wInternalRaw[cluster[k] - 1];
      if (val > maxVal) {
        maxVal = val;
        maxIdx0 = k;
      }
    }
    let wsum = 0;
    for (let k = 0; k < cluster.length; k++) {
      if (k !== maxIdx0) {
        const e = tr.next("randround");
        wInternal[cluster[k] - 1] = e.value;
        wsum += e.value;
      }
    }
    const maxw = Math.floor(wInternalRaw[cluster[maxIdx0] - 1]);
    let bump;
    if (wsum % 2 !== 0) {
      bump = maxw % 2 === 0 ? 1 : 0;
    } else {
      bump = maxw % 2 !== 0 ? 1 : 0;
    }
    wInternal[cluster[maxIdx0] - 1] = maxw + bump;
    if (wInternal[cluster[maxIdx0] - 1] > w[cluster[maxIdx0] - 1]) {
      // Canonical asserts the bump is exactly 1 here.
      w[cluster[maxIdx0] - 1] = wInternal[cluster[maxIdx0] - 1];
    }

    const stubs = [];
    for (const v of cluster) {
      for (let k = 0; k < wInternal[v - 1]; k++) stubs.push(v);
    }

    // shuffle
    const sh = tr.next("shuffle");
    if (sh.n !== stubs.length) {
      throw new Error(`shuffle mismatch (cluster=${cidx0 + 1}): trace n=${sh.n} stubs n=${stubs.length}`);
    }
    // Use the recorded `after` directly — JS doesn't try to replay the perm.
    for (let k = 0; k < stubs.length; k++) stubs[k] = sh.after[k];

    const localEdges = new Set();
    let recycle = [];
    for (let i = 0; i < stubs.length; i += 2) {
      const a = stubs[i], b = stubs[i + 1];
      const e = epair(a, b);
      const k = ekey(a, b);
      if (e[0] === e[1] || localEdges.has(k)) {
        recycle.push(e);
      } else {
        localEdges.add(k);
      }
    }

    let lastRecycle = recycle.length;
    let recycleCounter = lastRecycle;
    while (recycle.length > 0) {
      recycleCounter -= 1;
      if (recycleCounter < 0) {
        if (recycle.length < lastRecycle) {
          lastRecycle = recycle.length;
          recycleCounter = lastRecycle;
        } else {
          break;
        }
      }
      const p1 = recycle.shift();
      const fromRecycle = (2 * recycle.length) / stubs.length;
      let success = false;
      if (!(recycle.length === 0 && localEdges.size === 0)) {
        // The for loop iterates floor(length(stubs)/2) times.
        const innerIters = Math.floor(stubs.length / 2);
        for (let inner = 0; inner < innerIters; inner++) {
          const coin1 = tr.next("uniform").value;
          let usedRecycle, p2, recycleIdx;
          if (coin1 < fromRecycle || localEdges.size === 0) {
            usedRecycle = true;
            const idxEntry = tr.next("uniform_int");
            recycleIdx = idxEntry.value - 1; // Julia 1-based -> JS 0-based
            p2 = recycle[recycleIdx];
          } else {
            usedRecycle = false;
            const setEntry = tr.next("rand_set");
            p2 = epair(setEntry.element[0], setEntry.element[1]);
          }
          const coin2 = tr.next("uniform").value;
          let newp1, newp2;
          if (coin2 < 0.5) {
            newp1 = epair(p1[0], p2[0]);
            newp2 = epair(p1[1], p2[1]);
          } else {
            newp1 = epair(p1[0], p2[1]);
            newp2 = epair(p1[1], p2[0]);
          }
          let goodChoice;
          if (newp1[0] === newp2[0] && newp1[1] === newp2[1]) {
            goodChoice = false;
          } else if (newp1[0] === newp1[1] || localEdges.has(ekey(newp1[0], newp1[1]))) {
            goodChoice = false;
          } else if (newp2[0] === newp2[1] || localEdges.has(ekey(newp2[0], newp2[1]))) {
            goodChoice = false;
          } else {
            goodChoice = true;
          }
          if (goodChoice) {
            if (usedRecycle) {
              // Julia: recycle[recycle_idx], recycle[end] = recycle[end], recycle[recycle_idx]; pop!(recycle)
              recycle[recycleIdx] = recycle[recycle.length - 1];
              recycle.pop();
            } else {
              localEdges.delete(ekey(p2[0], p2[1]));
            }
            success = true;
            localEdges.add(ekey(newp1[0], newp1[1]));
            localEdges.add(ekey(newp2[0], newp2[1]));
            break;
          }
        }
      }
      if (!success) recycle.push(p1);
    }

    for (const k of localEdges) edges.add(k);
    for (const [a, b] of recycle) {
      wInternal[a - 1] -= 1;
      wInternal[b - 1] -= 1;
    }
  }

  // Global stage.
  const stubs = [];
  for (let i = 0; i < w.length; i++) {
    for (let k = wInternal[i] + 1; k <= w[i]; k++) stubs.push(i + 1);
  }

  const sh = tr.next("shuffle");
  if (sh.n !== stubs.length) {
    throw new Error(`global shuffle mismatch: trace n=${sh.n} stubs n=${stubs.length}`);
  }
  for (let k = 0; k < stubs.length; k++) stubs[k] = sh.after[k];

  if (stubs.length % 2 === 1) {
    // Canonical removes the stub of the highest-w vertex in stubs (first by
    // 1-based occurrence). Using identical scan to canonical.
    let maxi = 0;
    for (let i = 1; i < stubs.length; i++) {
      if (w[stubs[i] - 1] > w[stubs[maxi] - 1]) maxi = i;
    }
    const si = stubs[maxi];
    stubs.splice(maxi, 1);
    w[si - 1] -= 1;
  }

  const globalEdges = new Set();
  let recycle = [];
  for (let i = 0; i < stubs.length; i += 2) {
    const a = stubs[i], b = stubs[i + 1];
    const e = epair(a, b);
    const k = ekey(a, b);
    if (e[0] === e[1] || globalEdges.has(k) || edges.has(k)) {
      recycle.push(e);
    } else {
      globalEdges.add(k);
    }
  }

  let lastRecycle = recycle.length;
  let recycleCounter = lastRecycle;
  while (recycle.length > 0) {
    recycleCounter -= 1;
    if (recycleCounter < 0) {
      if (recycle.length < lastRecycle) {
        lastRecycle = recycle.length;
        recycleCounter = lastRecycle;
      } else {
        break;
      }
    }
    const p1 = recycle.pop();
    const fromRecycle = (2 * recycle.length) / stubs.length;
    const coin1 = tr.next("uniform").value;
    let p2;
    if (coin1 < fromRecycle) {
      const idxEntry = tr.next("uniform_int");
      const i = idxEntry.value - 1;
      // Julia: recycle[i], recycle[end] = recycle[end], recycle[i]; pop!(recycle)
      const tmp = recycle[i];
      recycle[i] = recycle[recycle.length - 1];
      recycle.pop();
      p2 = tmp;
    } else {
      const setEntry = tr.next("rand_set");
      p2 = epair(setEntry.element[0], setEntry.element[1]);
      globalEdges.delete(ekey(p2[0], p2[1]));
    }
    const coin2 = tr.next("uniform").value;
    let newp1, newp2;
    if (coin2 < 0.5) {
      newp1 = epair(p1[0], p2[0]);
      newp2 = epair(p1[1], p2[1]);
    } else {
      newp1 = epair(p1[0], p2[1]);
      newp2 = epair(p1[1], p2[0]);
    }
    for (const np of [newp1, newp2]) {
      const k = ekey(np[0], np[1]);
      if (np[0] === np[1] || globalEdges.has(k) || edges.has(k)) {
        recycle.push(np);
      } else {
        globalEdges.add(k);
      }
    }
  }

  for (const k of globalEdges) edges.add(k);

  if (recycle.length > 0) {
    let lr = recycle.length;
    let rc = lr;
    while (recycle.length > 0) {
      rc -= 1;
      if (rc < 0) {
        if (recycle.length < lr) {
          lr = recycle.length;
          rc = lr;
        } else {
          break;
        }
      }
      const p1 = recycle.pop();
      const setEntry = tr.next("rand_set");
      const x = epair(setEntry.element[0], setEntry.element[1]);
      const xkey = ekey(x[0], x[1]);
      edges.delete(xkey);
      const p2 = x;
      const coin = tr.next("uniform").value;
      let newp1, newp2;
      if (coin < 0.5) {
        newp1 = epair(p1[0], p2[0]);
        newp2 = epair(p1[1], p2[1]);
      } else {
        newp1 = epair(p1[0], p2[1]);
        newp2 = epair(p1[1], p2[0]);
      }
      for (const np of [newp1, newp2]) {
        const k = ekey(np[0], np[1]);
        if (np[0] === np[1] || edges.has(k)) {
          recycle.push(np);
        } else {
          edges.add(k);
        }
      }
    }
  }

  return edges;
}

// ---------------------------------------------------------------------------
// Driver.
// ---------------------------------------------------------------------------
function runReplay(payload) {
  const tr = makeTrace(payload.trace || []);
  const hasOutliers = (payload.n_outliers || 0) > 0;
  const clusters = populateClustersReplay(
    payload.w, payload.s, payload.xi, hasOutliers, tr,
  );
  const edges = configModelReplay(
    clusters, payload.w, payload.s, payload.xi, hasOutliers, tr,
  );
  // Canonical sorts edges for output. Match.
  const edgeArr = Array.from(edges).map(parseKey)
    .sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  return { edges: edgeArr, clusters, trace_consumed: tr.consumed(), trace_length: tr.length() };
}

const src = readAllStdin();
const payload = JSON.parse(src);
const out = runReplay(payload);
process.stdout.write(JSON.stringify(out) + "\n");
