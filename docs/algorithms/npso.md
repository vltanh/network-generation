# nPSO

[← back to index](../algorithms.md)

Non-uniform Popularity-Similarity Optimisation. Embeds nodes on a
hyperbolic disk and connects pairs that lie close in the geometry. A
temperature knob trades clustering coefficient against randomness.

Most generators in this project are driven by degree and block
statistics, not geometry. They place edges from counts, so triangles
happen only by coincidence. nPSO is the exception: it gets triangles
by embedding nodes on a hyperbolic disk and connecting pairs that are
geometrically close. A temperature knob `T ∈ (0, 1)` controls how
strictly "close" is enforced. Small `T` means only very close pairs
connect (high clustering). Large `T` means connections become
random-ish (low clustering). Matching an empirical clustering
coefficient means searching for the right `T`.

## Stage 1: profile

Profile reads the reference network + clustering and distils them
into a small, explicit contract. The default model is nPSO2, so
alongside the scalars profile also emits a weight vector `ρ_k =
size_k / N`, one entry per mixture component.

| Scalar | Meaning |
| --- | --- |
| `N` | Number of nodes. |
| `m` | Half of the mean degree, rounded. Controls how many existing nodes each arrival attaches to. |
| `γ` | Power-law exponent of the degree distribution, fit using the Python `powerlaw` package and floored at `γ ≥ 2`. |
| `C` | Number of mixture components. Under outlier-mode `singleton` (the default), each input outlier becomes its own 1-node cluster, bumping `C` by the outlier count. |
| `C_G` | Global clustering coefficient of the input (via `networkit`). This is the target for Stage 4's search. |

Outliers are nodes that are unclustered in the input or sole members
of a size-1 cluster. Under outlier-mode `singleton` they are
promoted to their own `__outlier_<id>__` pseudo-cluster at the
profile step, so each outlier becomes a valid singleton cluster for
Stage 2 to embed as a tiny mixture weight.

### Why nPSO2 (and not 1 or 3)

The paper defines three angular-distribution variants:

- **nPSO1** uses uniform weights `ρ_k = 1/C`. Paper's primary benchmark.
- **nPSO2** accepts caller-supplied `ρ_k`. Paper's way to generate "communities of different sizes".
- **nPSO3** mixes Gaussians with Gammas to build asymmetric lobes.

All three are available via `--npso-model`. The default is nPSO2
because our inputs have clusters of very different sizes. A long
right-hand tail of singleton outliers is typical. Treating them as
`1/C`-weighted components (nPSO1) would have each outlier claim a
full angular sector, producing outlier "communities" with several
nodes each. nPSO2 with `ρ_k = size_k / N` gives singleton outliers
correspondingly tiny mixture weight, which is closer to the reality
that they are outliers.

## Stage 2: the disk

Each node gets two polar coordinates. The *radial* position encodes
popularity (hubs near the centre, low-degree nodes at the rim). The
*angular* position encodes similarity (nearby angles = similar).

Nodes are indexed in descending order by degree, so the biggest hub
is `t_i = 1` and the lowest-degree node is `t_i = N`.

With `β = 1/(γ - 1)`, following the PSO growth process
(Papadopoulos et al. 2012, extended for communities in Muscoloni &
Cannistraci 2018), node `i`'s radial coordinate after all `N`
arrivals is

```
r_i = 2β · ln(t_i) + 2(1-β) · ln(N).
```

Default model is nPSO2: the angular coordinate is sampled from a
Gaussian mixture with `C` equidistant means `μ_k = 2π k / C`, common
width `σ = 2π / (6C)`, and profile-supplied weights `ρ_k = size_k /
N`:

```
θ_i ~ Σ_{k=1}^{C} ρ_k · N(μ_k, σ²)   (mod 2π).
```

Each sample first picks a component `k` with probability `ρ_k`, then
draws `θ_i` from `N(μ_k, σ²)`. Input cluster labels do not survive
this stage. Every node is re-assigned to the nearest component mean:

```
C'(i) = argmin_k d_ang(θ_i, μ_k),
```

where `d_ang` is the shortest circular distance between two angles.
Nodes drawn from low-weight components can land closer to a
high-weight component's mean and get re-assigned. That is by design,
and it is why singleton outliers (with `ρ = 1/N`) can end up absorbed
by nearby large clusters rather than staying isolated. When that
happens the outlier's component ends up empty, and the 1-node
cluster it would have formed becomes a true generator outlier in the
output (stripped by `drop_singleton_clusters`).

## Stage 3: temperature

For each pair of nodes `(i, j)`, nPSO computes a hyperbolic distance
`h_ij` on the Poincaré disk,

```
h_ij = arccosh(cosh r_i · cosh r_j − sinh r_i · sinh r_j · cos d_ang(θ_i, θ_j)),
```

and compares it to the current disk radius `R(T)` (a closed form in
`T`, `N`, `m`, `β`, derived in the paper). Connection is a
Fermi-Dirac coin flip:

```
p(i, j) = 1 / (1 + exp((h_ij − R(T)) / (2T))).
```

At `T → 0` the sigmoid turns into a hard cutoff: every pair with
`h_ij < R(T)` connects and nothing else does. Triangles are plentiful
because close triples all lie inside the cutoff. At `T → 1` the
sigmoid is shallow and most pairs flip a near-fair coin regardless of
distance, so edges look random and triangle density collapses.
`cc(T)` is the clustering coefficient of a specific nPSO sample at
temperature `T`. Different draws at the same `T` give different
values, so the curve is noisy.

Edges are placed via the paper's **implementation 3**: each node
arriving at time `t_i` picks exactly `m` targets from earlier
arrivals without replacement, with probability proportional to
`p(i, j)`. Total edge count is `m(m+1)/2 + (N-m-1) · m`, independent
of `T`. The `T` knob reshuffles *which* pairs get picked but not how
many. The paper proves impl 1 (per-pair Bernoulli against `p(i, j)`)
and impl 3 are equivalent in expectation. The MATLAB binary uses impl
3, and so does this documentation.

## Stage 4: the secant search

`cc(T)` trends downward in `T` but is not strictly monotone (see
Stage 3). The code nonetheless treats this as 1D root-finding. Start
with `[T_min, T_max] = [0, 1]` and bisect. Residual signs drive the
bracket update backwards from the textbook: `cc > target` means `T`
is too small, so `T_min` moves right. `cc < target` means `T` is too
large, so `T_max` moves left.

Once both bracket ends have opposite-signed residuals, secant kicks
in:

```
T_sec = T_min − f_min / (f_max − f_min) · (T_max − T_min).
```

**Margin guard.** If the secant iterate falls within 5% of the
bracket width from either endpoint, the code falls back to the
bracket midpoint for that iter. Without the guard, secant stalls
hugging the endpoint when `cc(T)` flattens near a bracket edge.

**Stop conditions.** residual < 0.005, step < 0.0001, or 100 iters.
The wrapper keeps the lowest-residual iterate regardless of whether
convergence was formally reached.

### Caveat 1: bisect + secant assumes monotone

The framework assumes `cc(T)` is monotone so residual signs carry
information. The mean curve drifts downward but is not strictly so,
and single-shot noise is substantial. Equal-signed residuals can
cluster on the wrong side, the secant never fires, and bisection
walks into a local minimum. Better options when `cc(T)` is noisy or
non-monotone: grid-scan `T` on a log-uniform schedule and keep the
best-residual iterate; sample several times per `T` and minimise over
the mean; or a golden-section search if unimodality is plausible. The
current code does none of these.

### Caveat 2: the target may be too high

nPSO's achievable `cc` ceiling depends on `(N, m, γ, C)`. If the
target sits above the ceiling at every `T`, even driving `T` toward
0 cannot push `cc(T)` up to it. When that happens the search never
crosses the target, residuals stay same-signed across the whole
bracket, and bisection walks `T → 0` until the step collapses. The
DNC case study below is the extreme: target 0.548, achievable top
barely above 0.1.

## Case study: dnc

Default run on `dnc` + `sbm-flat-best+cc` at `--seed 1` (nPSO2,
outlier-mode singleton):

| Stat | Input | nPSO output | Note |
| --- | --- | --- | --- |
| `N` | 906 | 906 | exact |
| edges | 10,429 | 10,794 | within 3.5%; `m = round(mean_deg / 2)`, rounded |
| mean degree | 23.02 | 23.78 | follows from edge count |
| global cc | 0.548 | 0.098 | **did not converge** |
| mean local cc | 0.494 | 0.557 | local overshoots the input while global stays low; different triangle structure |
| num clusters | 42 | 161 | nPSO2 with `C = 442` mixture components argmin-reassigns nodes across them; 161 components survive `drop_singleton_clusters` |

The 281 components that do not survive are the generator's own
outliers (singleton clusters by definition, stripped from
`com.csv` as a shipping guard). Their nodes remain in `edge.csv`
with their edges intact.

nPSO with `(N = 906, m = 12, γ = 2.0, C = 442)` peaks at `cc ≈ 0.098`
across the full `[0, 1]` range: the hyperbolic disk with these
parameters cannot generate clustering as high as 0.548. The search
walks down the left half of the bracket by pure bisection, stalls
after 5 iters at `T_best = 0.125` with `cc = 0.098` and residual
0.450.

## Standalone `gen.py`

`src/npso/gen.py` takes the derived scalars plus the target as
required CLI flags, so it can run without Stage 1 if the caller has
fit the model parameters elsewhere.

| Flag | Meaning |
| --- | --- |
| `--N <int>` | Node count. |
| `--m <int>` | Half-degree target (`mean(degrees) / 2`, rounded). |
| `--gamma <float>` | Power-law exponent of the degree distribution. |
| `--c <int>` | Number of mixture components. |
| `--target-ccoeff <float>` | Global clustering-coefficient target. |
| `--mixing-proportions <csv>` | Comma-separated `ρ_k` for nPSO2; one per component. Empty for nPSO1 / nPSO3. |
| `--npso-dir <p>` | `nPSO_model` checkout. |
| `--model <m>` | `nPSO1` \| `nPSO2` \| `nPSO3` (default `nPSO2`). |
| `--seed <n>` | Seed (default `1`). |
| `--n-threads <n>` | MATLAB `maxNumCompThreads` (default `1`). |
| `--output-folder <p>` | Where to write `edge.csv` + `com.csv`. |

When called via `src/npso/pipeline.sh`, these flags are filled from
`derived.txt` emitted by Stage 1.

## Dispatcher + pipeline flags

Dispatcher (`run_generator.sh`):

- `--npso-dir <p>`: path to `nPSO_model` checkout. Default `externals/npso`. Requires `matlab` on `PATH`.
- `--npso-model <m>`: `nPSO1` \| `nPSO2` \| `nPSO3`. Default `nPSO2`.

Pipeline (`./src/npso/pipeline.sh`):

- `--package-dir <p>`: same role as `--npso-dir`, short form at pipeline layer.
- `--model <m>`: same values as `--npso-model`.
- `--outlier-mode <excluded|singleton|combined>`: default `singleton`.
- `--drop-outlier-outlier-edges` / `--keep-outlier-outlier-edges`: default keep.
- `--match-degree` / `--no-match-degree`: default off.
- `--match-degree-algorithm <greedy|true_greedy|random_greedy|rewire|hybrid>`: default `hybrid`.
- `--remap` / `--no-remap`: default on (MATLAB sampler emits fresh `1..N` IDs).

See [../advanced-usage.md](../advanced-usage.md).

## Determinism

- Single MATLAB RNG source: `rng(seed)` at the top of `run_npso.m`. The same seed is used across all 100 search iters; the trajectory is fully determined by `(N, m, gamma, c, target, seed)`.
- MATLAB pinned single-threaded three ways on the subprocess path: pipeline default `N_THREADS=1`, subprocess flag `-singleCompThread`, MATLAB call `maxNumCompThreads(n_threads)`.
- networkit's `exactGlobal` clustering coefficient is closed-form deterministic.
- `PYTHONHASHSEED=0` exported by [`pipeline.sh`](../../src/npso/pipeline.sh) for the profile stage.

## Cost

10 seeds x 10 kept runs on 4 cores, 16 GiB cgroup cap:

- kept mean: 6.17 s
- kept std: 0.56 s

nPSO is the slowest of the seven generators. Cold cost (first run in
a fresh shell) is much higher, dominated by MATLAB engine startup.
Warm cost is the 10-iter temperature search itself. Each iter runs
`nPSO_model` on `N` nodes, which is `O(N²)` for the pairwise
hyperbolic distance computation.

## Where to look next

- [Source: `src/npso/gen.py`](../../src/npso/gen.py) (main wrapper + search)
- [Source: `src/npso/matlab/run_npso.m`](../../src/npso/matlab/run_npso.m)
- [Source: `src/npso/profile.py`](../../src/npso/profile.py)
- [Upstream: nPSO_model](https://github.com/biomedical-cybernetics/nPSO_model)
- [Interactive GUI: npso steps at default settings](https://vltanh.me/netgen/npso.html)
- [ABCD+o](./abcd+o.md)
- [LFR](./lfr.md) (same power-law fit for degrees)
- [Index of all generators](../algorithms.md)
