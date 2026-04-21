# nPSO

[← back to index](../algorithms.md)

The other six generators in this repo produce graphs that tend toward
tree-like: sparse, low triangle density, clustering coefficient around
0.01-0.05 where your real social graph might be 0.3 or higher. This is a
known limitation of the degree-corrected SBM and configuration-model
families: they do not target triangles.

nPSO is the exception. It is the only generator here that *targets* the
global clustering coefficient. It does so by a different route: instead of
sampling stubs, it embeds nodes in a hyperbolic disk, sets a temperature
parameter that controls how clustering-heavy the geometry is, and
*searches* over that temperature at stage 2 to match the input's triangle
density.

This is expensive. And, on some inputs, the search does not converge
because the model's achievable range does not include the target. See the
"what you get on the shipped example" section below for a concrete case.

## Model class in 60 seconds

non-uniform Popularity-Similarity Optimisation. Nodes live on a hyperbolic
disk.

- **Popularity** = radial position, driven by a power law (exponent γ).
- **Similarity** = angular position, drawn from a mixture of c Gaussians
  (one per cluster; the "non-uniform" part). Each Gaussian contributes
  an angular sector.
- **Edges** form between nodes that are geometrically close in hyperbolic
  distance.
- **Temperature** T ∈ (0, 1) controls how strictly "close" is enforced.
  At T → 0, only the geometrically closest nodes connect (high clustering
  coefficient); at T → 1, connections become essentially random (low
  clustering coefficient).

Matching an empirical clustering coefficient means finding the T that
reproduces it.

## Stage 1: the bare minimum

[`src/npso/profile.py`](../../src/npso/profile.py) emits only:

- `degree.csv`
- `cluster_sizes.csv`

No mixing parameter file; nPSO derives cross-cluster behaviour from
angular positions. Cluster *sizes* are discarded at stage 2; nPSO uses
only the *count* c. The actual sizes in the output are determined by
nPSO's angular sector assignments, not by the input distribution.

## Stage 2: four derived params + a search

```python
N     = len(degrees)
m     = int(round(mean(degrees) / 2))                     # average half-degree
gamma = max(powerlaw.Fit(degrees).power_law.alpha, 2.0)   # floored at 2
c     = len(cluster_sizes)                                # number of clusters
```

And the target:

```python
target_global_ccoeff = compute_global_ccoeff_from_edgelist(input_edgelist)
```

measured exactly (not sampled) via networkit's
`ClusteringCoefficient.exactGlobal`, after removing multi-edges and
self-loops.

## The temperature search

Clustering coefficient as a function of T is monotonically decreasing in
(0, 1). So the search is a 1D root-finding problem: find T such that
`ccoeff(T) − target = 0`.

The search in [`_next_T`](../../src/npso/gen.py#L406) is a secant step
with a margin guard over a bisection fallback:

- Default: midpoint of the current bracket `[min_T, max_T]`.
- Once both bracket endpoints have known residuals with opposite signs,
  try the secant formula:
  ```
  T_sec = min_T − f_min_T × (max_T − min_T) / (f_max_T − f_min_T)
  ```
- Margin guard: if T_sec lands within 5% of either endpoint, fall back to
  midpoint. Without this guard the secant can stall near a bracket
  boundary when the function flattens.

Stopping conditions:

- `best_diff < 0.005` (tight match).
- `|ccoeff_t − ccoeff_{t-1}| < 0.0001` (stagnation).
- `T < 0.0005` (degenerate bracket).
- 100 iterations.

At the end, the best `(T, edges, com)` seen is what gets written. If the
search has not converged, the best-so-far is used.

## Resumable search via JSON log

Each iteration appends `{T, ccoeff}` to `output_dir/search_log.json`,
keyed by a SHA-256 of `(N, m, gamma, c, target_ccoeff, seed)`. On rerun
with the same inputs, the log is replayed to restore bracket state and
the search picks up where it left off. On input mismatch the log is
invalidated and deleted.

Writes are atomic via sibling tempfile + `os.replace`. The log does not
store edge matrices. On a fresh resume with `best_T` already known, the
wrapper re-runs MATLAB once at that T to restore matrices before
continuing. If that re-run fails, the log is treated as stale and
discarded.

Each MATLAB iter is ~1 second. Losing state on interruption is painful
when the search runs 10+ iters.

## The MATLAB runner fallback chain

nPSO is the only generator here that depends on MATLAB. The wrapper
handles the licensing variability in three layers:

1. **`EngineRunner`**: persistent session via the `matlab.engine` Python
   package. Requires a proper MATLAB install plus the Python engine
   wheel. Returns matrices directly (no TSV round-trip).
2. **`SubprocessRunner`**: spawns a fresh `matlab` per iter via a bash
   one-liner that optionally `module load matlab` if `matlab` is not in
   PATH. Writes TSVs, reads them back. Slow (fresh startup per iter) but
   works on vanilla hosts.
3. **Mid-run fallback**: if `EngineRunner.run_iter` fails (MATLAB error
   inside an otherwise-alive engine), the main loop spawns a one-off
   `SubprocessRunner` for that iter and keeps the engine for subsequent
   ones.

The MATLAB wrapper
[`src/npso/matlab/run_npso.m`](../../src/npso/matlab/run_npso.m) is short:

```matlab
function [edges, comm] = run_npso(N, m, T, gamma, c, output_prefix, seed)
    if nargin >= 7, rng(seed); end
    [adj, ~, comm, ~] = nPSO_model(N, m, T, gamma, c, 0);
    comm = double(comm(:));

    if nargout == 0
        % Subprocess path: write edge.tsv / com.tsv.
        ...
    else
        % Engine path: return matrices.
        [u_list, v_list] = find(triu(adj, 1));
        edges = double([u_list, v_list]);
    end
end
```

One wrinkle: the two paths produce edges in different orderings.
Subprocess uses outer-u inner-v (row-major on the upper triangle). Engine
uses `find(triu)` which is column-major. Both are deterministic within a
path given a fixed seed. They are not byte-compatible across paths.

## What you get on the shipped example

Default run on dnc + sbm-flat-best+cc at `--seed 1`:

| Stat | Input | nPSO output | Note |
| --- | --- | --- | --- |
| N | 906 | 906 | exact |
| Edges | 10429 | 10794 | within 3.5% (m is set to round(mean_deg / 2), so edges are within rounding) |
| Mean degree | 23.02 | 23.83 | |
| Global clustering coeff. | 0.548 | 0.099 | **did not converge**; see below |
| Local clustering coeff. | 0.494 | 0.811 | |
| Num clusters | 42 | 42 | exact (c = len(cluster_sizes)) |

**Non-convergence caveat.** The dnc input has an empirical global
clustering coefficient of 0.548. The nPSO model with
`(N=906, m=12, γ=2.0, c=442)` peaks at around 0.099 even when T is pushed
below 0.01: the hyperbolic disk with these parameters cannot generate
clustering that high. The search ran its 100-iter budget and settled at
`best_T=0.0625` with `ccoeff=0.099` and `diff=0.449`. The output is the
best nPSO could do, but it is nowhere near the target.

The relevant parameters here are m (average half-degree) and γ
(power-law exponent). Raising m or lowering γ raises the achievable
clustering coefficient; the current flooring of γ at 2.0 is conservative
and rules out a higher-clustering regime. Treat nPSO's target as
advisory, not guaranteed: check the achieved ccoeff in `run.log` and the
trajectory in `search_log.json`.

## Output guarantees

- **N** exact.
- **Number of clusters** = c exact (angular sectors).
- **Global clustering coefficient**: at target within 0.005, *or* best
  achieved after 100 iters. On inputs where the model's achievable range
  does not include the target, the best is not close.
- **Degree distribution** at power-law(γ) asymptotically.

## What you do not get

- **Exact degree sequence.** Power-law asymptote only.
- **Cluster sizes.** Angular sectors have their own distribution; the
  input cluster sizes are thrown away.
- **Block structure.** Fresh clustering by angular sector; no
  correspondence to input.
- **Outlier identity.** cluster_id=1 is nPSO's internal outlier bucket
  (same convention as [ABCD+o](./abcd+o.md)). It is stripped from
  `com.csv`. The nodes remain in `edge.csv` with their edges intact.

## Determinism

- Single MATLAB RNG source: `rng(seed)` at the top of `run_npso.m`. The
  same seed is used across all 100 search iters; the trajectory is fully
  determined by `(N, m, gamma, c, target, seed)`.
- MATLAB pinned single-threaded three ways on the subprocess path:
  pipeline default `N_THREADS=1`, subprocess flag `-singleCompThread`,
  MATLAB call `maxNumCompThreads(n_threads)`.
- networkit's `exactGlobal` clustering coefficient is closed-form
  deterministic.
- `PYTHONHASHSEED=0` exported by
  [`pipeline.sh`](../../src/npso/pipeline.sh) for the profile stage.

## Cost

10 seeds x 10 kept runs on 4 cores, 16 GiB cgroup cap:

- kept mean: 6.17 s
- kept std: 0.56 s

nPSO is the slowest of the seven generators. Cold cost (first run in a
fresh shell) is much higher, dominated by MATLAB engine startup. Warm
cost is the 10-iter temperature search itself; each iter runs
`nPSO_model` on N nodes, which is O(N²) for the pairwise hyperbolic
distance computation.

## When to use

- **Yes**: you care about clustering coefficient and can tolerate the
  non-convergence risk.
- **Maybe**: you can afford ~6 s of wall-clock and the MATLAB dependency.
- **No**: you need exact degree sequence, exact block structure, or
  specific outlier semantics.

## Where to look next

- [Source: `src/npso/gen.py`](../../src/npso/gen.py) (main wrapper + search)
- [Source: `src/npso/matlab/run_npso.m`](../../src/npso/matlab/run_npso.m)
- [Source: `src/npso/profile.py`](../../src/npso/profile.py)
- [Upstream: nPSO_model](https://github.com/biomedical-cybernetics/nPSO_model)
- [Interactive GUI: npso steps at default settings](./npso.html)
- [ABCD+o (also has a cluster_id=1 outlier convention)](./abcd+o.md)
- [Index of all generators](../algorithms.md)
