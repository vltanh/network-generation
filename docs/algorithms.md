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
| [`lfr`](./algorithms/lfr.md) | Lancichinetti-Fortunato-Radicchi benchmark          | `unweighted_undirected/benchmark` (C++)                               |
| [`npso`](./algorithms/npso.md) | Non-uniform popularity-similarity optimisation    | `nPSO_model` (MATLAB), wrapped in a secant search over temperature    |

All seven consume the same inputs at the repo boundary: an undirected edge
list and a reference clustering (node_id → cluster_id, not necessarily a
partition). They diverge at stage 1 on *what summary they extract* and at
stage 2 on *how they sample*.

Each generator has its own page (linked from the table above) that walks
through profile extraction, stage-2 sampling, guarantees, non-guarantees,
determinism traps, and cost. Each page also links to an interactive
static-HTML GUI that animates the algorithm step by step on a small
synthetic example.

## Interactive GUIs

Each algorithm page has a matching static HTML GUI that walks through the
default-settings algorithm on a small (~25 node) synthetic example so that
every step (K_{k+1} core, constructive attachments, SBM dedup, ξ split,
temperature search, etc.) is visible. They are self-contained (no build
step, no framework; vanilla JS + SVG) and work offline.

- [sbm.html](https://vltanh.me/netgen/sbm.html)
- [ec-sbm-v1.html](https://vltanh.me/netgen/ec-sbm-v1.html)
- [ec-sbm-v2.html](https://vltanh.me/netgen/ec-sbm-v2.html)
- [abcd.html](https://vltanh.me/netgen/abcd.html)
- [abcd+o.html](https://vltanh.me/netgen/abcd+o.html)
- [lfr.html](https://vltanh.me/netgen/lfr.html)
- [npso.html](https://vltanh.me/netgen/npso.html)

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
| Global clustering coefficient           | —    | —         | —         | —    | —      | —    | ≈*   |
| *Outliers*                              |      |           |           |      |        |      |      |
| Outlier count from G preserved          | ✓    | —         | —         | ✓    | ✓      | ✓    | ✓    |
| Outliers are identifiable in output     | —    | —         | —         | —    | ✓      | —    | ✓    |
| *Reproducibility*                       |      |           |           |      |        |      |      |
| Byte-reproducible from `--seed`         | ✓    | ✓         | ✓         | ✓    | ✓      | ✓    | ✓    |

✓ = preserved exactly (deterministic function of the profile). ≈ =
targeted but perturbed by internal rewiring, post-hoc dedup of
self-loops / parallel edges, degree-matching residual, or search
tolerance. — = not a model parameter. ≈* = targeted via a secant search;
on inputs whose target exceeds the model's achievable range, convergence
is only to the best-so-far (see the [nPSO page](./algorithms/npso.md)
for a concrete non-convergence case on dnc).

Rows reflect each generator's *default* flags. `ec-sbm` defaults to
`excluded`, which drops outliers. `abcd+o`'s cluster-size list includes a
prepended outlier block of size n_outliers.

## Which generator should I use?

A few rules of thumb. The per-gen pages expand on these.

- **Exact empirical block structure and degrees**: [`sbm`](./algorithms/sbm.md).
  That is what the degree-corrected micro-SBM is for.
- **Each cluster edge-connected**: [`ec-sbm-v2`](./algorithms/ec-sbm-v2.md)
  with `--algorithm hybrid`.
- **Benchmark-style synthetic with only aggregate mixing**:
  [`abcd`](./algorithms/abcd.md), [`abcd+o`](./algorithms/abcd+o.md), or
  [`lfr`](./algorithms/lfr.md). ABCD/ABCD+o converge faster and preserve
  degree + cluster sizes exactly; LFR is the incumbent benchmark and
  uses power-law parametrisation.
- **High clustering coefficient / triangle density**:
  [`npso`](./algorithms/npso.md). The only generator here that targets
  it; but on inputs whose clustering exceeds the model's achievable
  range, nPSO returns the best-so-far rather than a match.

No generator guarantees *both* exact degree sequence *and* high triangle
count. The degree-corrected SBM family produces nearly-tree-like graphs
even when the input is highly clustered; nPSO's hyperbolic geometry gets
triangles but resamples degrees from a power law. Bridging the two is
active research territory; ec-sbm's constructive first stage is one
attempt.

## Self-loops and parallel edges

All seven generators emit simple graphs. sbm, ec-sbm-v1, and ec-sbm-v2
call `remove_parallel_edges` + `remove_self_loops` after
`gt.generate_sbm`; the ec-sbm pipelines additionally `drop_duplicates`
in the combine stage. ABCD / ABCD+o / LFR resolve loops and duplicates
inside their external samplers via rewiring; LFR's Python wrapper
additionally dedups the C++ binary's undirected double-listing. nPSO
reads edges from a MATLAB {0, 1} adjacency matrix via `triu(adj, 1)` /
`find(adj == 1)`, so by construction no self-loops or parallels reach
`edge.csv`.

## Reproducibility notes

All seven generators are byte-reproducible end-to-end under a fixed
`--seed`. The sha256 prefixes of the shipped example (dnc +
sbm-flat-best+cc, `--seed 1`, on the current host) are:

| Gen        | `edge.csv` (sha256[:12]) | `com.csv` (sha256[:12]) |
| ---------- | ------------------------ | ----------------------- |
| sbm        | `3f8356d4236f`           | `e240ae23f3ea`          |
| ec-sbm-v1  | `e2b5a6914b12`           | `e240ae23f3ea`          |
| ec-sbm-v2  | `f46e7d8b94c9`           | `e240ae23f3ea`          |
| abcd       | `057a8ef26ebc`           | `55c19725f859`          |
| abcd+o     | `be419667a464`           | `1151780594fc`          |
| lfr        | `ea9b42120eb3`           | `2db4f5ab80be`          |
| npso       | `90e5f99dc8b7`           | `b5854b5f88c7`          |

The first three share `com.csv` because SBM-family gens pass through the
input clustering after dropping singletons; the last four emit their own
cluster assignment each run.

Byte-identity holds across hosts when the toolchain versions match.
Cross-host differences (graph-tool version, Julia minor version, MATLAB
patch level) can produce different hashes while preserving the
distributional guarantees in the table above.

**Trap:** `--seed 0` silently disables graph-tool's PRNG (documented as
"entropy source") and breaks byte-reproducibility for `sbm` and `ec-sbm`.
The default everywhere is `--seed 1`; if you need `0`-equivalent
behaviour, use `1` and move on.

### Achieved vs target statistics (dnc + sbm-flat-best+cc, seed=1)

Input: 906 nodes, 10429 edges, mean degree 23.02, global ccoeff 0.548,
local ccoeff 0.494, mean k-core 15.99, 42 clusters.

| Gen        | N    | Edges | Mean deg | Global ccf | Local ccf | Clusters |
| ---------- | ---- | ----: | -------: | ---------: | --------: | -------: |
| input      | 906  | 10429 | 23.02    | 0.548      | 0.494     | 42       |
| sbm        | 902  |  7438 | 16.49    | 0.341      | 0.216     | 42       |
| ec-sbm-v1  | 906  | 10422 | 23.01    | 0.424      | 0.327     | 42       |
| ec-sbm-v2  | 906  | 10346 | 23.03    | 0.513      | 0.350     | 42       |
| abcd       | 906  | 10150 | 22.41    | 0.307      | 0.234     | 42       |
| abcd+o     | 673* | 10070 | 29.93    | 0.307      | 0.339     | 42       |
| lfr        | 906  | 10370 | 22.89    | 0.252      | 0.732     | 51       |
| npso       | 906  | 10794 | 23.83    | 0.099**    | 0.811     | 42       |

&ast; abcd+o's `com.csv` drops the 355-node outlier block (no "outliers
form a community" warning on this input). All 906 nodes remain in
`edge.csv`.

&ast;&ast; nPSO did not converge on this input. Target was 0.548, best
achieved was 0.099 at T=0.0625. The search exhausted its 100-iter budget;
the model's achievable range with these derived parameters does not
include the target. See the [nPSO page](./algorithms/npso.md) for the
trajectory.

### Runtime (dnc network, isolated cgroup, 4 cores, 16 GiB cap)

Measured via
[`tools/benchmark/bench_isolated.sh`](../tools/benchmark/bench_isolated.sh):
per generator, 2 warmup + 10 kept runs per seed across seeds 1-10. All
100 kept runs per generator produce byte-identical `edge.csv` within
each (gen, seed).

| Generator   | kept mean (s) | kept std (s) | min (s) | max (s) | peak RSS (MB) |
| ----------- | ------------: | -----------: | ------: | ------: | ------------: |
| sbm         |      1.65     |     0.03     |  1.62   |  1.73   |    267        |
| lfr         |      1.77     |     0.05     |  1.67   |  1.89   |    146        |
| ec-sbm-v2   |      2.39     |     0.10     |  2.30   |  3.02   |    268        |
| ec-sbm-v1   |      2.83     |     0.05     |  2.77   |  2.98   |    265        |
| abcd        |      3.75     |     0.10     |  3.61   |  4.25   |    499        |
| abcd+o      |      3.85     |     0.05     |  3.75   |  3.98   |    508        |
| npso        |      6.17     |     0.56     |  5.52   | 10.81   |    298        |

**Host:** Pop!_OS 22.04 LTS, i9-12900HK (20 threads, pinned to 4 via
taskset), 62 GiB RAM, 16 GiB memory cap via systemd-run cgroup v2.
Python 3.11.15, graph-tool 2.98, Julia 1.12.6, MATLAB R2024a. Absolute
wall-clock has noise from background processes; ordering and
byte-identity are the load-independent signals.

Takeaways: SBM is the fastest and nPSO is the slowest. ec-sbm-v2 is
faster than v1 (one `gt.generate_sbm` call vs v1's two). ABCD/ABCD+o are
external-process bound and pay Julia's ~2 s startup every run. LFR's C++
binary is the fastest stage-2 sampler, but the powerlaw fits at profile
add cost. nPSO's 100-iter search dominates.

### Host-sensitive numbers

The measurements above reflect a specific toolchain. Prior measurements
on an RHEL 9.6 / AMD EPYC / Python 3.14 / graph-tool 2.98 host gave a
different ordering (SBM and LFR swapped places, ec-sbm-v1 and v2
swapped, nPSO was slower in wall-clock because of a slower MATLAB
startup). The byte-identity guarantee is per-toolchain: the same host,
same seed, same toolchain versions produce the same output; different
toolchains may produce different outputs while preserving the
distributional guarantees listed above.
