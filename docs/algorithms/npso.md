# nPSO

[← back to index](../algorithms.md)

Algorithm walkthrough + interactive viz: [vltanh.me/netgen/npso.html](https://vltanh.me/netgen/npso.html).

## CLI flags

Dispatcher (`run_generator.sh`):

| Flag | Default | Effect |
| --- | --- | --- |
| `--npso-dir <p>` | `externals/npso` | path to `nPSO_model` checkout. Requires `matlab` on `PATH`. |
| `--npso-model <m>` | `nPSO2` | one of `nPSO1`, `nPSO2`, `nPSO3` |

Pipeline ([`src/npso/pipeline.sh`](../../src/npso/pipeline.sh)):

| Flag | Default | Effect |
| --- | --- | --- |
| `--package-dir <p>` | `externals/npso` | dispatcher-equivalent of `--npso-dir` (short form at pipeline layer) |
| `--model <m>` | `nPSO2` | dispatcher-equivalent of `--npso-model` |
| `--outlier-mode <excluded\|singleton\|combined>` | `singleton` | how `profile.py` folds outliers |
| `--drop-outlier-outlier-edges` / `--keep-outlier-outlier-edges` | keep | strip OO edges from input edgelist |
| `--match-degree` / `--no-match-degree` | off | optional Stage-4 degree rewire |
| `--match-degree-algorithm <greedy\|true_greedy\|random_greedy\|rewire\|hybrid>` | `true_greedy` | only with `--match-degree` |
| `--remap` / `--no-remap` | on | MATLAB sampler emits fresh `1..N` IDs |

Standalone `gen.py` (runs without Stage 1 if caller has fit the
parameters): see `parse_args()` at
[`src/npso/gen.py:464`](../../src/npso/gen.py#L464).

| Flag | Effect |
| --- | --- |
| `--N <int>` | Node count |
| `--m <int>` | Half-degree target (`mean(degrees) / 2`, rounded) |
| `--gamma <float>` | Power-law exponent of the degree distribution |
| `--c <int>` | Number of mixture components |
| `--target-ccoeff <float>` | Global clustering-coefficient target |
| `--mixing-proportions <csv>` | Comma-separated `ρ_k` for nPSO2; empty for nPSO1 / nPSO3 |
| `--npso-dir <p>` | `nPSO_model` checkout |
| `--model <m>` | `nPSO1` \| `nPSO2` \| `nPSO3` (default `nPSO2`) |
| `--seed <n>` | Seed (default `1`) |
| `--n-threads <n>` | MATLAB `maxNumCompThreads` (default `1`) |
| `--output-folder <p>` | Where to write `edge.csv` + `com.csv` |

When called via `pipeline.sh`, these flags are filled from `derived.txt`
emitted by Stage 1 (`_export_derived` at
[`src/npso/profile.py:110`](../../src/npso/profile.py#L110)).

See [../advanced-usage.md](../advanced-usage.md) for the
dispatcher-namespaced vs pipeline-short flag naming convention.

## Stage 1: profile

Entrypoint: `setup_inputs(edgelist_path, clustering_path, output_dir, …)`
at [`src/npso/profile.py:70`](../../src/npso/profile.py#L70). Distils
the input network + clustering into the scalar contract below.

| Scalar | Source | Meaning |
| --- | --- | --- |
| `N` | `len(node_id.csv)` | Number of nodes |
| `m` | `round(mean(degrees) / 2)` | Half mean degree, controls per-arrival attachments |
| `γ` | `_fit_gamma(degrees)` at [`profile.py:56`](../../src/npso/profile.py#L56) | `powerlaw.Fit(...).power_law.alpha`, floored at `≥ 2` |
| `C` | `len(cluster_id.csv)` | Mixture component count (under `singleton` mode each outlier is its own component) |
| `C_G` | `_compute_global_ccoeff(...)` at [`profile.py:47`](../../src/npso/profile.py#L47) | networkit's `exactGlobal` clustering coefficient: Stage 4's target |
| `ρ_k` | `_mixing_proportions(...)` at [`profile.py:62`](../../src/npso/profile.py#L62) | size-proportional weights `size_k / N`, one per component (nPSO2 only) |

Outputs written by `_export_derived(...)` at
[`profile.py:110`](../../src/npso/profile.py#L110): `derived.txt`
(scalars) + `mixing_proportions.csv`.

Outliers (unclustered nodes, or sole members of a size-1 cluster) under
`--outlier-mode singleton` are promoted to their own
`__outlier_<id>__` pseudo-cluster, so each becomes a tiny mixture
component (`ρ = 1/N`) for Stage 2.

## Stage 2: the disk

Each node gets two polar coordinates. *Radial* = popularity (hubs near
centre). *Angular* = similarity (nearby angles ⇒ similar). Nodes are
indexed in descending degree, so the biggest hub is `i = 1`.

With `β = 1/(γ - 1)`, node `i`'s radial coordinate at simulation time
`t ≥ i` is the PSO growth law:

```
r_i(t) = 2β · ln(i) + 2(1-β) · ln(t).
```

Default model is **nPSO2**: angular sampled from a Gaussian mixture
with `C` equidistant means `μ_k = 2π k / C`, common width
`σ = 2π / (6C)`, and profile-supplied weights `ρ_k = size_k / N`:

```
θ_i ~ Σ_{k=1}^{C} ρ_k · N(μ_k, σ²)   (mod 2π).
```

The MATLAB driver picks a model variant via `build_distr(C, model, weights)`
at [`run_npso.m:46`](../../src/npso/matlab/run_npso.m#L46):

- `nPSO1` → integer `C` (paper default GMM with equal `ρ_k`)
- `nPSO2` → `gmdistribution(mu', sigma_sq, p)` with caller weights
- `nPSO3` → `create_mixture_gaussian_gamma_pdf(C)` (asymmetric lobes)

After sampling, every node is re-assigned to its nearest component
mean: `C'(i) = argmin_k d_ang(θ_i, μ_k)`. Input cluster labels do not
survive Stage 2; nodes drawn from low-weight components can land in
high-weight components and get reabsorbed (singleton outliers
typically end up as true generator outliers, stripped by
`drop_singleton_clusters` in post-process).

## Stage 3: temperature

Per-pair hyperbolic distance on the Poincaré disk:

```
h_ij = arccosh(cosh r_i · cosh r_j − sinh r_i · sinh r_j · cos d_ang(θ_i, θ_j)).
```

Connection radius `R(T)` is a closed form in `T`, `N`, `m`, `β`. The
pair connects with Fermi-Dirac probability:

```
p(i, j) = 1 / (1 + exp((h_ij − R(T)) / (2T))).
```

Edges are placed via the paper's **implementation 3**: each arrival at
time `t_i` picks exactly `m` targets from earlier arrivals without
replacement, with probability proportional to `p(i, j)`. Total edge
count = `m(m+1)/2 + (N - m - 1) · m`, independent of `T`. The first
`m + 1` arrivals form `K_{m+1}` deterministically (autoAll branch);
arrival `m + 2` onward is the first weighted-pick step.

Reference: `nPSO_model.m` in `externals/npso/`. The MATLAB sampler is
called from `EngineRunner.run(...)` at
[`src/npso/gen.py:123`](../../src/npso/gen.py#L123) (when the MATLAB
Engine for Python is importable) or from `SubprocessRunner.run(...)`
at [`src/npso/gen.py:79`](../../src/npso/gen.py#L79) (fallback,
launches `matlab -batch`).

## Stage 4: the secant search

`run_npso_generation(...)` at
[`src/npso/gen.py:190`](../../src/npso/gen.py#L190) runs a 100-iter
search to find the `T` whose realised `cc(T)` matches `C_G`.

`_next_T(min_T, max_T, f_min, f_max)` at
[`src/npso/gen.py:440`](../../src/npso/gen.py#L440):

```python
def _next_T(min_T, max_T, f_min_T, f_max_T):
    mid = min_T + (max_T - min_T) / 2
    if f_min_T is None or f_max_T is None:
        return mid
    if f_min_T * f_max_T > 0:
        return mid                    # bisect while same-signed
    denom = f_max_T - f_min_T
    if denom == 0:
        return mid
    T_sec = min_T - f_min_T * (max_T - min_T) / denom
    margin = 0.05 * (max_T - min_T)
    if T_sec <= min_T + margin or T_sec >= max_T - margin:
        return mid                    # margin guard
    return T_sec
```

Bracket starts `[T_min, T_max] = [0, 1]`. Residual signs drive bracket
update *inverted* from textbook secant: `cc > target` ⇒ `T` too small
⇒ `T_min` moves right; `cc < target` ⇒ `T` too large ⇒ `T_max` moves
left.

Stop conditions: residual `< 0.005`, step `< 0.0001`, or 100 iters.
The wrapper keeps the lowest-residual iterate regardless of whether
convergence was formally reached.

Persisted `search_log.json` (cache key = SHA-256 of
`(N, m, gamma, c, target, seed, model, mixing_proportions)`, written
by `_input_hash(...)` at
[`gen.py:400`](../../src/npso/gen.py#L400)) lets reruns at the same
parameters skip the search and reuse the converged `T`.

## Output guarantees

- **N** exact (CLI arg).
- **Edge count** `m(m+1)/2 + (N - m - 1) · m` exact (implementation 3 places exactly `m` edges per arrival).
- **Degree distribution** matches a power law with the fitted `γ` in
  expectation; per-node degrees are not preserved.
- **Global clustering coefficient** targeted via the secant T-search;
  on inputs whose target falls outside the model's achievable range,
  convergence is only to the best-so-far and the search exhausts its
  100-iter budget.
- **Cluster count.** Profile sets `C` (mixture components / angular
  sectors), but `com.csv` carries cluster ids beyond `[1..C]`: MATLAB's
  `gmdistribution` posterior occasionally assigns to degenerate mixture
  components, so the emitted `comm` vector spans more labels than the
  input `C`. On dnc with profile `c=442`, output `com.csv` has 161 unique
  cluster ids.

## Determinism

- Single MATLAB RNG: `rng(seed)` at the top of
  [`run_npso.m:1-4`](../../src/npso/matlab/run_npso.m#L1-L4). One seed
  drives every nPSO sample within the search; the trajectory is fully
  determined by `(N, m, gamma, c, target, seed)`.
- MATLAB pinned single-threaded three ways on the subprocess path:
  pipeline default `N_THREADS=1`; subprocess flag `-singleCompThread`;
  MATLAB call `maxNumCompThreads(n_threads)` (see
  `_matlab_subprocess_script(n_threads)` at
  [`gen.py:50`](../../src/npso/gen.py#L50)).
- networkit's `exactGlobal` clustering coefficient is closed-form
  deterministic.
- `PYTHONHASHSEED=0` exported by
  [`pipeline.sh`](../../src/npso/pipeline.sh) for the profile stage.

## Cost

nPSO is the slowest of the seven generators. Cold cost (first run in a
fresh shell) is dominated by MATLAB engine startup. Warm cost is the
100-iter temperature search itself; each iter runs `nPSO_model` on `N`
nodes (`O(N²)` for pairwise hyperbolic distances). Concrete numbers
live in `examples/benchmark/summary.csv`, refreshed by
[`tools/benchmark/bench_isolated.sh`](../../tools/benchmark/bench_isolated.sh).

## Where to look next

- [`src/npso/gen.py`](../../src/npso/gen.py): main wrapper + secant search
- [`src/npso/profile.py`](../../src/npso/profile.py): profile entrypoint
- [`src/npso/matlab/run_npso.m`](../../src/npso/matlab/run_npso.m): MATLAB driver
- [`externals/npso/nPSO_model.m`](../../externals/npso/nPSO_model.m): upstream sampler
- [Upstream: nPSO_model on GitHub](https://github.com/biomedical-cybernetics/nPSO_model)
- [Interactive walkthrough: vltanh.me/netgen/npso.html](https://vltanh.me/netgen/npso.html)
- [ABCD+o](./abcd+o.md), [LFR](./lfr.md) (same `powerlaw.Fit` for degrees)
- [Index of all generators](../algorithms.md)
