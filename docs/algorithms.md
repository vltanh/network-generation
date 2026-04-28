# Network generators: technical reference

Algorithm walkthroughs (with interactive viz) live at
[https://vltanh.me/netgen/](https://vltanh.me/netgen/). This page collects
the per-toolchain technical details: pipeline contract, output guarantees,
determinism + reproducibility, runtime cost, and CLI flags.

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
list and a reference clustering (`node_id → cluster_id`, not necessarily a
partition). Stage 1 extracts a per-gen profile; stage 2 samples from it.

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
| ec-sbm-v1  | `e4e3b6cf1b68`           | `5a5afc352f13`          |
| ec-sbm-v2  | `e0d6d1d7feb5`           | `5a5afc352f13`          |
| abcd       | `057a8ef26ebc`           | `55c19725f859`          |
| abcd+o     | `be419667a464`           | `1151780594fc`          |
| lfr        | `ea9b42120eb3`           | `2db4f5ab80be`          |
| npso       | `5dc2b4ee3023`           | `e7e3a6a047b8`          |

`sbm` passes the input clustering through after dropping singletons;
ec-sbm-v1 and ec-sbm-v2 share `com.csv` (same set of node→cluster pairs
re-ordered by `match_degree`); the last four emit their own cluster
assignment each run.

**Trap:** `--seed 0` silently disables graph-tool's PRNG (documented as
"entropy source") and breaks byte-reproducibility for `sbm` and `ec-sbm`.
The default everywhere is `--seed 1`; if you need `0`-equivalent
behaviour, use `1` and move on.

### Achieved vs target statistics (dnc + sbm-flat-best+cc, seed=1)

Input: 906 nodes, 10429 edges, mean degree 23.02, global ccoeff 0.548,
local ccoeff 0.494, mean k-core 15.99, 87 clusters.

| Gen        | N    | Edges | Mean deg | Global ccf | Local ccf | Clusters |
| ---------- | ---- | ----: | -------: | ---------: | --------: | -------: |
| input      | 906  | 10429 | 23.02    | 0.548      | 0.494     | 87       |
| sbm        | 902  |  7438 | 16.49    | 0.341      | 0.216     | 87       |
| ec-sbm-v1  | 906  | 10425 | 23.01    | 0.424      | 0.321     | 87       |
| ec-sbm-v2  | 906  | 10342 | 22.83    | 0.501      | 0.342     | 87       |
| abcd       | 906  | 10150 | 22.41    | 0.307      | 0.234     | 87       |
| abcd+o     | 673* | 10070 | 29.93    | 0.307      | 0.339     | 87       |
| lfr        | 906  | 10370 | 22.89    | 0.252      | 0.732     | 10       |
| npso       | 906  | 10794 | 23.83    | 0.098**    | 0.558     | 161      |

&ast; abcd+o's `edge.csv` carries 673 distinct endpoints; the remaining
profile-declared outliers (n_outliers = 355) end up edgeless and drop
out of the materialised edge list. `com.csv` covers 551 of those
endpoints under the 87 real clusters; the rest are surviving outliers.

&ast;&ast; nPSO did not converge on this input. Target was 0.548, best
achieved was 0.098 at T=0.0625. The search exhausted its 100-iter budget;
the model's achievable range with these derived parameters does not
include the target. See the [nPSO page](./algorithms/npso.md) for the
trajectory.

### Runtime ordering

SBM is the fastest stage-2 sampler and nPSO the slowest. ec-sbm-v2 is
faster than v1 (one `gt.generate_sbm` call vs v1's two). ABCD/ABCD+o are
external-process bound and pay Julia's ~2 s startup every run. LFR's C++
binary is itself the fastest sampler, but the powerlaw fits at profile
time add cost. nPSO's 100-iter temperature search dominates.

Concrete numbers live in `examples/benchmark/summary.csv`, refreshed by
[`tools/benchmark/bench_isolated.sh`](../tools/benchmark/bench_isolated.sh).
