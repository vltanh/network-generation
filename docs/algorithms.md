# Network generators: what they preserve, what they don't

This repo wraps seven synthetic-network generators under a single two-stage
pipeline. Stage 1 (`profile.py`) reads a real network plus a reference
clustering and extracts a small statistical profile. Stage 2 (`gen.py`)
consumes that profile and samples a synthetic network from a parametric
model. The two stages are intentionally decoupled: the same profile can be
consumed by different generators, and the same generator can be pointed at
different profiles.

The question each generator's page answers: **given an empirical network
G, what does that generator's output G' guarantee to look like?** We use
"guarantee" loosely. Some statistics are exact matches (number of nodes,
block structure), some are distributional (degree distribution in
expectation), and some are only *targeted* via a search that may or may
not converge.

## The seven generators at a glance

| Generator                    | Model family                                        | Stage-2 sampler                                                       |
| ---------------------------- | --------------------------------------------------- | --------------------------------------------------------------------- |
| [`sbm`](./algorithms/sbm.md) | Degree-corrected stochastic block model             | `graph_tool.generate_sbm`                                             |
| [`ec-sbm-v1`](./algorithms/ec-sbm-v1.md) | SBM + edge-connectivity guarantee + outlier SBM | Constructive K_{k+1} core + full-SBM overlay + separate outlier SBM + heap-greedy matcher |
| [`ec-sbm-v2`](./algorithms/ec-sbm-v2.md) | SBM + edge-connectivity guarantee + residual-SBM outliers | Constructive core + residual SBM with block-preserving rewire + five-way matcher          |
| [`abcd`](./algorithms/abcd.md) | Artificial benchmark for community detection      | `ABCDGraphGenerator.jl`                                              |
| [`abcd+o`](./algorithms/abcd+o.md) | ABCD with explicit outliers                     | `ABCDGraphGenerator.jl` (n_outliers > 0)                              |
| [`lfr`](./algorithms/lfr.md) | Lancichinetti–Fortunato–Radicchi benchmark          | `unweighted_undirected/benchmark` (C++)                               |
| [`npso`](./algorithms/npso.md) | Non-uniform popularity-similarity optimisation    | `nPSO_model` (MATLAB), wrapped in a secant search over temperature    |

All seven consume the **same** inputs at the repo boundary: an undirected
edge list and a reference clustering (node_id → cluster_id, not
necessarily a partition). They diverge at stage 1 on *what summary they
extract* and at stage 2 on *how they sample*.

Each generator has its own page (linked from the table above) that walks
through profile extraction, stage-2 sampling, guarantees,
non-guarantees, determinism traps, and cost. Start there if you're
choosing between generators or trying to understand a specific one.

## Summary: who guarantees what

| Property                                | sbm  | ec-sbm-v1 | ec-sbm-v2 | abcd | abcd+o | lfr  | npso |
| --------------------------------------- | ---- | --------- | --------- | ---- | ------ | ---- | ---- |
| *Size*                                  |      |           |           |      |        |      |      |
| Number of nodes                         | ✓    | ✓         | ✓         | ✓    | ✓      | ✓    | ✓    |
| Cluster sizes                           | ✓    | ✓         | ✓         | ✓    | ✓      | —    | —    |
| *Degrees*                               |      |           |           |      |        |      |      |
| Degree sequence                         | ≈    | ≈         | ≈         | ≈    | ≈      | —    | —    |
| Degree dist. ~ fitted power-law         | —    | —         | —         | —    | —      | ✓    | ✓    |
| *Clustering and blocks*                 |      |           |           |      |        |      |      |
| Block structure (input partition)       | ✓    | ✓         | ✓         | —    | —      | —    | —    |
| Inter-cluster edge counts               | ≈    | ≈         | ≈         | —    | —      | —    | —    |
| Per-cluster edge connectivity ≥ k       | —    | ✓         | ✓         | —    | —      | —    | —    |
| *Mixing and topology*                   |      |           |           |      |        |      |      |
| Global mixing parameter ξ               | —    | —         | —         | ≈    | ≈      | —    | —    |
| Mean per-node mixing parameter μ        | —    | —         | —         | —    | —      | ≈    | —    |
| Global clustering coefficient           | —    | —         | —         | —    | —      | —    | ≈    |
| *Outliers*                              |      |           |           |      |        |      |      |
| Outlier count from G preserved          | ✓    | —         | —         | ✓    | ✓      | ✓    | ✓    |
| Outliers are identifiable in output     | —    | —         | —         | —    | ✓      | —    | ✓    |
| *Reproducibility*                       |      |           |           |      |        |      |      |
| Byte-reproducible from `--seed`         | ✓    | ✓         | ✓         | ✓    | ✓      | ✓    | ✓    |

✓ = preserved exactly (deterministic function of the profile). ≈ =
targeted but perturbed by internal rewiring, post-hoc dedup of
self-loops / parallel edges, degree-matching residual, or search
tolerance. — = not a model parameter.

Rows reflect each generator's *default* flags. `ec-sbm` defaults to
`excluded`, which drops outliers. `abcd+o`'s cluster-size list includes a
prepended outlier block of size n_outliers.

## Which generator should I use?

A few rules of thumb. The per-gen pages expand on these.

- **Exact empirical block structure and degrees**: [`sbm`](./algorithms/sbm.md).
  That's what the degree-corrected micro-SBM is for.
- **Each cluster edge-connected**: [`ec-sbm-v2`](./algorithms/ec-sbm-v2.md)
  with `--algorithm hybrid`.
- **Benchmark-style synthetic with only aggregate mixing**:
  [`abcd`](./algorithms/abcd.md), [`abcd+o`](./algorithms/abcd+o.md), or
  [`lfr`](./algorithms/lfr.md). ABCD/ABCD+o converge faster and preserve
  degree + cluster sizes exactly; LFR is the incumbent benchmark and
  uses power-law parametrisation.
- **High clustering coefficient / triangle density**:
  [`npso`](./algorithms/npso.md). The only generator here that targets
  it.

No generator guarantees *both* exact degree sequence *and* high triangle
count. The degree-corrected SBM family produces nearly-tree-like graphs
even when the input is highly clustered; nPSO's hyperbolic geometry gets
triangles but resamples degrees from a power-law. Bridging the two is
active research territory; ec-sbm's constructive first stage is one
attempt.

## Self-loops and parallel edges

All seven generators emit simple graphs. Sbm, ec-sbm-v1, and ec-sbm-v2
call `remove_parallel_edges` + `remove_self_loops` after
`gt.generate_sbm`; the ec-sbm pipelines additionally `drop_duplicates`
in the combine stage. ABCD / ABCD+o / LFR resolve loops and duplicates
inside their external samplers via rewiring; LFR's Python wrapper
additionally dedups the C++ binary's undirected double-listing. nPSO
reads edges from a MATLAB {0,1} adjacency matrix via `triu(adj, 1)` /
`find(adj == 1)`, so by construction no self-loops or parallels reach
`edge.csv`.

## Reproducibility notes

All seven generators are byte-reproducible end-to-end under a fixed
`--seed`. The canonical sha256 prefixes at `--seed 1` on
`dnc + sbm-flat-best+cc` (the shipped example):

| Gen        | `edge.csv` (first 12 chars) | `com.csv` (first 12 chars) |
| ---------- | --------------------------- | -------------------------- |
| sbm        | `a55621c176bf`              | `e240ae23f3ea`             |
| ec-sbm-v1  | `e2b5a6914b12`              | `e240ae23f3ea`             |
| ec-sbm-v2  | `f0b255d97b90`              | `e240ae23f3ea`             |
| abcd       | `057a8ef26ebc`              | `55c19725f859`             |
| abcd+o     | `be419667a464`              | `1151780594fc`             |
| lfr        | `ea9b42120eb3`              | `2db4f5ab80be`             |
| npso       | `90e5f99dc8b7`              | `b5854b5f88c7`             |

The first three share `com.csv` because SBM-family gens pass through the
input clustering after dropping singletons; the last four emit their own
cluster assignment each run.

**Trap:** `--seed 0` silently disables graph-tool's PRNG (documented as
"entropy source") and breaks byte-reproducibility for `sbm` and
`ec-sbm`. The default is `--seed 1` everywhere; if you need
`0`-equivalent behaviour, use `1` and live with it.

### Runtime (dnc network, seeds 1-10)

Measured via [`scripts/benchmark/bench_gens.sh`](../scripts/benchmark/bench_gens.sh):
per generator, 2 warmup + 10 kept runs per seed across seeds 1-10, all
inside a single shell per gen so interpreter, graph-tool / MATLAB
engine, and NFS caches are amortised. All 7 gens produce byte-identical
`edge.csv` within each (gen, seed).

| Generator   | kept mean (s) | kept std (s) | cold (seed 1, run 1) |
| ----------- | ------------: | -----------: | -------------------: |
| sbm         |      3.97     |     0.24     |      21.11 s         |
| lfr         |      4.04     |     0.17     |       7.31 s         |
| abcd        |      6.48     |     0.26     |       6.43 s         |
| abcd+o      |      6.59     |     0.17     |       6.82 s         |
| ec-sbm-v2   |      8.11     |     1.02     |       8.50 s         |
| ec-sbm-v1   |      9.75     |     0.42     |      10.68 s         |
| npso        |     13.17     |     0.48     |      69.26 s         |

**Host:** AMD EPYC-Genoa (32 cores, single-thread pinned for the
benchmark), 125 GiB RAM, RHEL 9.6, NFS-mounted workspace, Python 3.14,
graph-tool 2.98, Julia 1.11.6, MATLAB R2024a. Absolute wall-clock has
noise from a shared login host; ordering and byte-identity are the
load-independent signals.

Takeaways: `sbm` and `npso` pay a large cold cost (graph-tool import,
MATLAB engine start) that disappears after the first run in a shell.
`ec-sbm-v2`'s std is dominated by one NFS-contention outlier across the
100 kept runs (14.98 s max vs. 7.42 s min on seed=4); the median of
every seed's kept runs falls in the 7.5-8.8 s band. ABCD/ABCD+o/LFR are
external-process bound so their cold/warm gap is small. nPSO's warm cost
is the 10-iter temperature secant search itself, not startup.
