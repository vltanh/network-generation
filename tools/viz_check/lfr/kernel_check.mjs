#!/usr/bin/env node
// Faithful JS port of LFR's degree-sequence sampler stage.
// Trace-driven: consumes a recorded ran4() stream from the instrumented
// canonical binary (tools/viz_check/lfr/instrumented/benchmark-instrumented)
// and re-derives degree_seq independently. Compared byte-for-byte against
// the canonical-emitted degseq.
//
// Stage covered (line-for-line port of Sources/benchm.cpp):
//   - solve_dmin (bisection)
//   - integer_average parity test (min vs min+1)
//   - powerlaw cumulative
//   - per-node degree sample via lower_bound on cumulative + min_degree
//   - sort + parity correction
//
// Stages NOT covered in this port (deferred to a future session):
//   - cluster size sampler (powerlaw on community sizes)
//   - internal/external degree split + global mu
//   - per-cluster + global config-model edge sampling
//   - rewire passes
// Those stages consume the remainder of the trace; the JS port asserts
// only that the FIRST num_nodes draws produce the canonical degree_seq.
//
// Inputs (stdin JSON):
//   { mode: "replay",
//     N: <int>, k: <float>, maxk: <int>, t1: <float>,
//     trace_path: "/path/to/ran4_trace.txt",
//     degseq_path: "/path/to/canonical_degseq.txt" }
// Output (stdout JSON):
//   { ok: true|false, diff: "..." }

import { readFileSync } from "node:fs";

function integral(a, b) {
  if (Math.abs(a + 1) > 1e-10) return (1 / (a + 1)) * Math.pow(b, a + 1);
  return Math.log(b);
}
function averageDegree(dmax, dmin, gamma) {
  return (
    (1 / (integral(gamma, dmax) - integral(gamma, dmin))) *
    (integral(gamma + 1, dmax) - integral(gamma + 1, dmin))
  );
}
function solveDmin(dmax, dmed, gamma) {
  let dmin_l = 1;
  let dmin_r = dmax;
  let avg1 = averageDegree(dmin_r, dmin_l, gamma);
  let avg2 = dmin_r;
  if (avg1 - dmed > 0 || avg2 - dmed < 0) return -1;
  while (Math.abs(avg1 - dmed) > 1e-7) {
    const mid = (dmin_r + dmin_l) / 2;
    const temp = averageDegree(dmax, mid, gamma);
    if ((temp - dmed) * (avg2 - dmed) > 0) {
      avg2 = temp;
      dmin_r = mid;
    } else {
      avg1 = temp;
      dmin_l = mid;
    }
  }
  return dmin_l;
}
function integerAverage(n, min, tau) {
  let a = 0;
  for (let h = min; h < n + 1; h++) a += Math.pow(1 / h, tau);
  let pf = 0;
  for (let i = min; i < n + 1; i++) pf += (1 / a) * Math.pow(1 / i, tau) * i;
  return pf;
}
function powerlawCumulative(n, min, tau) {
  let a = 0;
  for (let h = min; h < n + 1; h++) a += Math.pow(1 / h, tau);
  const cum = [];
  let pf = 0;
  for (let i = min; i < n + 1; i++) {
    pf += (1 / a) * Math.pow(1 / i, tau);
    cum.push(pf);
  }
  return cum;
}
function lowerBound(arr, target) {
  let lo = 0, hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  return lo;
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
      ok: false, diff: "only mode=replay supported",
    }) + "\n");
    process.exit(2);
  }
  const N = job.N | 0;
  const max_degree = job.maxk | 0;
  const tau = +job.t1;
  const average_k = +job.k;

  const dmin = solveDmin(max_degree, average_k, -tau);
  if (dmin === -1) {
    process.stdout.write(JSON.stringify({
      ok: false, diff: `solveDmin failed for maxk=${max_degree} k=${average_k} tau=${tau}`,
    }) + "\n");
    process.exit(2);
  }
  let min_degree = Math.trunc(dmin);
  const m1 = integerAverage(max_degree, min_degree, tau);
  const m2 = integerAverage(max_degree, min_degree + 1, tau);
  if (Math.abs(m1 - average_k) > Math.abs(m2 - average_k)) min_degree++;

  const cumulative = powerlawCumulative(max_degree, min_degree, tau);

  const trace_text = readFileSync(job.trace_path, "utf8");
  const trace = trace_text.split("\n").filter(Boolean).map((s) => +s);
  if (trace.length < N) {
    process.stdout.write(JSON.stringify({
      ok: false, diff: `trace shorter than N=${N} (${trace.length} entries)`,
    }) + "\n");
    process.exit(2);
  }

  const degree_seq = [];
  for (let i = 0; i < N; i++) {
    const u = trace[i];
    const idx = lowerBound(cumulative, u);
    degree_seq.push(idx + min_degree);
  }
  degree_seq.sort((a, b) => a - b);
  let sum = 0; for (const x of degree_seq) sum += x;
  if (sum % 2 !== 0) {
    let maxIdx = 0;
    for (let i = 1; i < degree_seq.length; i++) {
      if (degree_seq[i] > degree_seq[maxIdx]) maxIdx = i;
    }
    degree_seq[maxIdx] -= 1;
  }

  const degseq_text = readFileSync(job.degseq_path, "utf8");
  const canon = degseq_text.split("\n").filter(Boolean).map((s) => +s);
  if (canon.length !== degree_seq.length) {
    process.stdout.write(JSON.stringify({
      ok: false, diff: `length mismatch js=${degree_seq.length} canon=${canon.length}`,
    }) + "\n");
    process.exit(0);
  }
  const diffs = [];
  for (let i = 0; i < canon.length; i++) {
    if (canon[i] !== degree_seq[i]) diffs.push([i, canon[i], degree_seq[i]]);
  }
  if (diffs.length === 0) {
    process.stdout.write(JSON.stringify({
      ok: true,
      diff: `degree_seq byte-equal (N=${N}, sum=${sum - (sum % 2)}, min_degree=${min_degree}, cumulative_len=${cumulative.length})`,
    }) + "\n");
  } else {
    process.stdout.write(JSON.stringify({
      ok: false,
      diff: `${diffs.length} positions differ; first3=${JSON.stringify(diffs.slice(0, 3))} (min_degree=${min_degree})`,
    }) + "\n");
  }
}

main().catch((e) => {
  process.stderr.write(String(e.stack || e) + "\n");
  process.exit(1);
});
