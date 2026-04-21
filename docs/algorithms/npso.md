# nPSO: the one that remembers triangles

[← back to index](../algorithms.md)

All six other generators in this repo share a weakness: they produce graphs that look nearly tree-like. Sparse, low triangle density, clustering coefficient around 0.01-0.05 where your real social graph might be 0.3 or higher. That's a known limitation of the degree-corrected SBM and configuration-model families — they simply don't target triangle counts.

**nPSO is the exception.** It's the only generator here that *targets* the global clustering coefficient. It gets there by a different route too: instead of sampling stubs, it **embeds nodes in a hyperbolic disk**, sets a temperature parameter that controls how clustering-heavy the geometry is, and **searches over that temperature** at stage 2 to match your input's triangle density.

This is expensive. It's also uniquely useful.

## Model class in 60 seconds

non-uniform Popularity-Similarity Optimisation. Nodes live on a hyperbolic disk. **Popularity** = radial position, driven by a power law. **Similarity** = angular position, drawn from a mixture of `c` Gaussians (one per cluster — the "non-uniform" in the name). Edges form between nodes that are geometrically close in hyperbolic distance. A **temperature** `T ∈ (0, 1)` controls how strictly "close" is enforced:

- T → 0: only the geometrically-closest nodes connect. High clustering coefficient.
- T → 1: connections become essentially random. Low clustering coefficient.

So matching an empirical clustering coefficient means finding the T that reproduces it.

## Stage 1: bare minimum

[`src/npso/profile.py`](../../src/npso/profile.py) emits only:
- `degree.csv`
- `cluster_sizes.csv`

No mixing parameter file. nPSO derives cross-cluster behaviour from angular positions; there's no separate knob.

Notably, cluster *sizes* are discarded downstream — nPSO only uses the *count* `c`. The actual sizes in your output are determined by nPSO's angular sector assignments, not by your input distribution.

## Stage 2: four derived params + a search

```python
N     = len(degrees)
m     = int(round(mean(degrees) / 2))                     # average half-degree
gamma = max(powerlaw.Fit(degrees).power_law.alpha, 2.0)   # power-law, floored at 2
c     = len(cluster_sizes)                                # number of clusters
```

And the target:
```python
target_global_ccoeff = compute_global_ccoeff_from_edgelist(input_edgelist)
```
measured exactly (not sampled) via networkit's `ClusteringCoefficient.exactGlobal`, with multi-edges and self-loops pre-stripped.

Now the interesting part — finding T.

## The temperature search

Clustering coefficient as a function of T is **monotonically decreasing** in (0, 1). So this is a 1D root-finding problem: find T such that `ccoeff(T) − target = 0`.

The search is a **secant-with-margin-guard over a bisection fallback**, implemented in [`_next_T`](../../src/npso/gen.py#L406):

- Default: midpoint of the current bracket `[min_T, max_T]`.
- Once both bracket endpoints have known residuals with *opposite signs*, try the secant formula:
  ```
  T_sec = min_T − f_min_T × (max_T − min_T) / (f_max_T − f_min_T)
  ```
- **Margin guard**: if `T_sec` lands within 5% of either endpoint (the secant has gone wild because the function is flat near the boundary), fall back to midpoint.

Stopping conditions:
- `best_diff < 0.005` (tight match on clustering coefficient).
- `|ccoeff_t − ccoeff_{t-1}| < 0.0001` (stagnation).
- `T < 0.0005` (degenerate bracket).
- 100 iterations.

At the end, the best `(T, edges, com)` seen is what gets written out. If the search doesn't converge, the best-so-far is used.

## Resumable search via JSON log

Each iteration appends `{T, ccoeff}` to `output_dir/search_log.json`, keyed by a SHA-256 of `(N, m, gamma, c, target_ccoeff, seed)`. On rerun with the same inputs, the log is replayed to restore the bracket state, and the search picks up where it left off. On input mismatch, the log is invalidated and deleted.

Writes are atomic (sibling tempfile + `os.replace` → POSIX atomic rename). The log doesn't store edge matrices; on a fresh resume with `best_T` already known, the wrapper re-runs MATLAB once at that T to restore matrices before continuing.

This matters because each MATLAB iter is ~1 second — when the search runs 10+ iters, losing state on interruption is painful.

## The MATLAB runner fallback chain

nPSO is the only generator here that depends on MATLAB, which is a licensing headache. The wrapper handles this in three layers:

1. **`EngineRunner`**: persistent session via the `matlab.engine` Python package. Requires a proper MATLAB install with the Python engine pip wheel installed. Returns matrices directly, no TSV round-trip.
2. **`SubprocessRunner`**: spawns a fresh `matlab` per iter via a bash one-liner that optionally lmod-loads MATLAB if it isn't in PATH. Writes TSVs, reads them back. Slow (fresh MATLAB startup per iter) but works on vanilla hosts.
3. **Mid-run fallback**: if `EngineRunner.run_iter` fails (MATLAB execution error inside an otherwise-alive engine), the main loop spawns a one-off `SubprocessRunner` for that iter and keeps the engine for subsequent ones.

The MATLAB wrapper [`src/npso/matlab/run_npso.m`](../../src/npso/matlab/run_npso.m) is tiny:

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

One wrinkle: the two paths produce edges in different *orderings*. Subprocess does outer-u-inner-v (row-major). Engine uses `find(triu)` (column-major). Both are deterministic within a path given a fixed seed; they're not byte-compatible with each other. Downstream code shouldn't rely on cross-path stability.

## What you get

- **N exact**.
- **Number of clusters = c exact** (angular sectors).
- **Global clustering coefficient ≈ target** within 0.005 or best-so-far after 100 iters.
- **Degree distribution ~ power-law(γ)** asymptotically.

## What you don't get

- **Exact degree sequence.** Power-law asymptote only.
- **Cluster sizes.** Angular sectors have their own distribution; your input cluster sizes are thrown away.
- **Block structure.** Fresh clustering assignments by angular sector; no correspondence to input.
- **Outlier identity.** `cluster_id=1` is nPSO's internal outlier bucket (same convention as [ABCD+o](./abcd+o.md), coincidentally) and gets stripped from `com.csv` — but the nodes remain in `edge.csv` with their edges intact.

## Determinism

- Single MATLAB RNG source: `rng(seed)` at the top of `run_npso.m`. The same seed is used across all 100 secant iters; the trajectory is fully determined by `(N, m, gamma, c, target, seed)`.
- MATLAB pinned single-threaded three ways: pipeline default `N_THREADS=1`, subprocess flag `-singleCompThread`, MATLAB call `maxNumCompThreads(n_threads)`.
- networkit's `exactGlobal` clustering coefficient is closed-form deterministic.
- `PYTHONHASHSEED=0` exported from `pipeline.sh:42` for the profile stage.

## Cost

On the dnc example, single-threaded:

- Kept mean: ~13 s
- Cold: ~69 s

nPSO is the slowest of the seven generators. Cold cost is dominated by MATLAB engine startup (~10 s). Warm cost is the 10-iter search itself — each iter runs `nPSO_model` on N nodes, which is O(N²) for the pairwise hyperbolic distance computation.

## When to use

- **Yes, nPSO**: you care about clustering coefficient / triangle density. Nothing else in this repo targets it.
- **Maybe**: you can tolerate ~13 s of wall-clock and the MATLAB dependency.
- **No**: you need exact degree sequence, exact block structure, or specific outlier semantics.

## Where to look next

- [Source: `src/npso/gen.py`](../../src/npso/gen.py) (main wrapper + search)
- [Source: `src/npso/matlab/run_npso.m`](../../src/npso/matlab/run_npso.m)
- [Source: `src/npso/profile.py`](../../src/npso/profile.py)
- [Upstream: nPSO_model](https://github.com/biomedical-cybernetics/nPSO_model)
- [ABCD+o (also has a cluster_id=1 outlier convention)](./abcd+o.md)
- [Index of all generators](../algorithms.md)
