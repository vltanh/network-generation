# EC-SBM v3

[← back to index](../algorithms.md)

## What changes from v2

**Stage 2 is a per-cluster PSO sample, not residual-degree-weighted
attach.** Both v1/v2 and v3 share the same
[`gen_clustered.py`](../../externals/ec-sbm/src/gen_clustered.py)
driver and select between methods via `--method`:
`res-deg-weighted` (the K_{k+1} clique + greedy-then-residual-degree-
weighted-random attach in
[`gen_kec_core.py`](../../externals/ec-sbm/src/gen_kec_core.py)) is
the v1/v2 path; `pso` calls a Python port of the
[Popularity-Similarity-Optimization](https://www.nature.com/articles/nature11459)
model in [`gen_pso_core.py`](../../externals/ec-sbm/src/gen_pso_core.py)
once per cluster, with the cluster's mincut `k` doubling as PSO's `m`
parameter (capped at `n-1` and floored at `--pso-m-floor`).

**Profile gains `--method`.** When `method=pso`,
[`profile.py`](../../externals/ec-sbm/src/profile.py) additionally
emits `cluster_ccoeff.csv`: one float per cluster iid, the empirical
intra-induced global clustering coefficient that v3's T-search
targets. The other profile artifacts are identical to v1/v2.

Stages 1, 3, 4 are unchanged from v2. The residual SBM still runs over
all blocks with the same `--scope all --gen-outlier-mode combined`
defaults, and `match_degree` defaults to
`cluster_preserving_true_greedy` (per-(min_block, max_block) budget
gating; mode is inferred from the algorithm name).

## Why PSO

The constructive core was tuned for k-edge-connectivity, not for
clustering coefficient. Empirical communities sit on a hyperbolic
manifold: hubs are central, peripheral nodes attach by similarity, and
triangle density drops smoothly with hyperbolic temperature `T`. PSO
captures both popularity (radial coordinate, ~ degree distribution
power-law) and similarity (angular coordinate). With `T = 0` it acts
deterministically on the closest `m` neighbors and saturates the
clustering coefficient; with `T → 1` it draws partners almost uniformly
and triangles disappear. So PSO ships a single dial that lets each
cluster's intra-cluster ccoeff move toward its empirical target without
giving up the k-edge-connectivity guarantee.

## Per-cluster walkthrough

Stage 2 iterates clusters in `cluster_iid` ascending order
(profile's size-rank-then-id ordering). For each cluster `c`:

1. **Pick the empirical target.** Read `(node, cluster_id)` from the
   input clustering and the empirical edgelist. Restrict to edges
   whose endpoints both sit in `c`. Compute
   `target_cc = 3 * triangles / triplets` on that induced subgraph
   (`networkit`-style global ccoeff). Singletons and pairs get
   `target_cc = 0`.
2. **Sort cluster nodes by descending residual degree.** Tie-break
   on iid asc. The residual degree is the per-node degree budget
   left after earlier clusters consumed theirs (the same `deg`
   array `gen_kec_core` mutates in v1 / v2). The highest-degree
   surviving node becomes PSO arrival index 1 (centre of the
   hyperbolic disk).
3. **Resolve `m`.** `m = min(max(k, --pso-m-floor), n - 1)`
   (default `m_floor = 1`). `m >= k` keeps the cluster
   k-edge-connected (the K_{m+1} sub-clique built by the "connect to
   all existing" branch when `t - 1 <= m` is itself m-edge-connected,
   and every later attachment of `m` edges preserves the mincut).
4. **Short-circuit if trivial.** When `n <= m + 1`, PSO produces the
   complete graph regardless of `T`. One call at `--pso-initial-t`,
   one log entry tagged `note: complete_graph`, no search.
5. **Run the T search** (`secant` by default; `bayesian` opt-in via
   Optuna TPE). Each iteration:
   1. Pick a candidate `T`. Secant: bisection or secant from the
      current bracket and the residuals at its endpoints. BO: Optuna
      TPE samples from its surrogate.
   2. Run PSO `--pso-search-samples-per-T` times at `T` with distinct
      per-realisation seeds (default `3`). Compute the realisation
      ccoeffs and report their mean to the search. Pick the
      realisation closest to the mean as the representative edge set.
   3. Update the bracket (secant) or call `study.tell` (BO).
   4. Bookkeep `best_T`, `best_ccoeff`, `best_edges` whenever the
      mean-diff improves.
   5. Stop early on `|cc - target| < diff_tol`,
      `|cc - prev_cc| < step_tol`, or after `max_iters` total
      probes.
6. **Decrement the residual degree budget.** Every PSO edge
   `(u, v)` placed by the chosen realisation does
   `deg[u] -= 1; deg[v] -= 1` so stages 3 and 4 know what budget
   PSO already consumed.
7. **Log the cluster.** Append a record to
   `pso_search_log.json` with `n`, `k`, `m_used`,
   `empirical_mean_intra_deg`, `target_ccoeff`, `best_T`,
   `best_ccoeff`, `n_iters`, and the full `iters` trace
   (`T`, `ccoeff`, `diff`, optional `samples` list when
   `samples_per_T > 1`).
8. **Per-cluster RNG seed.** `cluster_seed = (global_seed *
   9_999_991 + cluster_iid) & 0xFFFFFFFF`. Per-iter seed is
   `(cluster_seed * 7_777_771 + iter_idx) & 0xFFFFFFFF`; per-sample
   seed inside `_eval_T` is `(iter_seed * 1_000_003 + sample_idx) &
   0xFFFFFFFF`. So the same cluster at the same global seed
   reproduces byte-for-byte.

After the loop, the union of all chosen-realisation edges (mapped
back through `node_id2id`) is sorted, written to
`stage/gen_clustered/edge.csv` under the band
`clustered_pso_core` in `sources.json`. Stage 3a (residual SBM) and
stage 4a (match_degree) then run unchanged from v2.

## The PSO call

For each cluster:

1. Sort nodes by descending residual degree (tiebreak iid asc). The
   highest-degree survivor plays PSO's "node 1" role: oldest in the
   growth process, most central radially.
2. Pick `m = min(max(k, --pso-m-floor), n - 1)`. `m >= k` keeps the
   cluster k-edge-connected; raise `--pso-m-floor` above 1 to give
   triangle capacity to clusters whose empirical mincut is degenerate.
3. Sample angular coordinates `theta_i ~ Uniform[0, 2pi)`.
4. Grow the graph node by node from `t = 2 ... n`. Update radial
   coordinates `r_i(t) = beta * 2 ln(i) + (1 - beta) * 2 ln(t)`,
   `beta = 1 / (gamma - 1)`. New node connects to `m` existing nodes,
   sampled without replacement with weight
   `p_i = 1 / (1 + exp((d_i - R_t) / 2T))`, where `d_i` is the
   hyperbolic distance and `R_t` is the curvature-corrected radius
   from the PSO paper. `T = 0` short-circuits to the `m` closest
   neighbors deterministically.

`m >= k` plus the K_{m+1} sub-clique built by the "connect to all
existing" branch when `t - 1 <= m` keeps the per-cluster mincut at
exactly `m` (and therefore at least `k`). The k-edge-connectivity
guarantee inherited from v1 is unchanged.

## The T search

The cluster's target is the empirical global clustering coefficient
on its intra-cluster induced subgraph (`3 * triangles / triplets`,
matching the [`networkit` exactGlobal](https://networkit.github.io/) and
nPSO conventions). The objective is `|ccoeff(T) - target|`. PSO is
stochastic, so two evaluations at the same `T` yield different ccoeff
values; the underlying trend is decreasing in `T` but a single draw
can buck the trend on any given probe. Two strategies ship:

- `--pso-search-strategy secant` (default): bisection + secant bracket
  on the sign of `f(T) = ccoeff(T) - target`. The empirical sweep at
  [`tools/npso_bo_sweep/`](../../tools/npso_bo_sweep/) shows secant
  ties or beats Bayesian opt on every (target, iters) cell at
  N ∈ {50, 200} despite the realisation noise, because the
  monotone-in-expectation trend has S/N ≥ 1.5 even at N=50 and the
  bracket update is the optimal access pattern for that geometry.
- `--pso-search-strategy bayesian` (opt-in): Optuna TPE sampler.
  TPE is noise-tolerant by construction (density estimation rather
  than GP surrogate fit) and shares the same `diff_tol` / `step_tol`
  early-stop as secant. Useful for ablation or for non-monotone
  regimes outside the swept grid. Optuna is an optional dependency;
  selecting `bayesian` without it logs a warning and falls back to
  secant.

`--pso-search-samples-per-T N` averages N independent PSO draws per
probe before reporting to the search; default `3`. Distinct seeds per
realisation, so re-runs are deterministic. Helps secant more than BO
because secant's sign-update sees a cleaner residual.

Each evaluation re-seeds PSO so different `T` values get different
draws and the same `T` re-runs deterministically. Stops on
`|ccoeff - target| < diff_tol`, on
`|ccoeff(t_i) - ccoeff(t_{i-1})| < step_tol`, or after
`pso_search_max_iters` total evaluations.

`stage/gen_clustered/pso_search_log.json` keeps the per-cluster trace
(every `(T, ccoeff, diff)` it tried, plus the final `(best_T,
best_ccoeff, m_used, empirical_mean_intra_deg)`).

## Skip cases

- `n <= m + 1`: PSO reduces to the complete graph. No T search; one
  call at `--pso-initial-t` is logged with the `note: complete_graph`
  marker.
- `k == 0` or `n <= 1`: no edges placed, no log entry beyond the
  `n_iters: 0` skeleton.

## Output guarantees

Inherits everything from v2 plus a per-cluster ccoeff target:

- **N** exact after the outlier transform.
- **k-edge-connectivity at least k(C)** per cluster by construction.
- **Block structure** exact.
- **Degree sequence** targeted; the residual stages spend whatever
  PSO did not consume.
- **Per-cluster clustering coefficient** within `pso_search_diff_tol`
  of empirical, subject to the `m` floor (high-target clusters with
  small `n` and `k=1` saturate around `cc(K_3) = 1`; low-target
  clusters with `n=3` and an empirical zero-cc force `m=1`, which is a
  tree).

On the [dnc](../../examples/input/empirical_networks/networks/dnc/)
fixture (552 clustered nodes, 87 clusters), seed 1, 25 search iters:
mean per-cluster final ccoeff |diff| ≈ 0.025, median 0.0, worst ≈ 0.37.
Zero k-edge-connectivity violations across all 87 shared clusters.

## Determinism

Three RNGs as in v2 (`random`, `numpy`, `graph-tool`), each seeded
per stage with `seed` / `seed+1` / `seed+2`. PSO uses
`np.random.default_rng(seed_iter)` where
`seed_iter = (cluster_seed * 1_000_003 + iter_idx) & 0xFFFFFFFF` and
`cluster_seed = (global_seed * 9_999_991 + cluster_iid) & 0xFFFFFFFF`.
Per-iter re-seeding lets a re-run at the same `T` reproduce its own
output without locking the search into a single draw at `T_0`.

`PYTHONHASHSEED=0` still required for stages 3a + 4a's set-iteration
sites.

## Provenance bands

`stage/gen_clustered/sources.json` holds a single band:

- `clustered_pso_core`: every per-cluster PSO edge, sorted globally.

Stage 3a + 4a bands are inherited from v2 (`outlier_sbm`,
`outlier_rewire`, `match_degree_<algo>`).

## CLI flags

Dispatcher (`run_generator.sh`):

- `--ec-sbm-dir <p>`: path to the ec-sbm submodule. Same as v2.

Pipeline (`./src/ec-sbm/pipeline.sh --version v3 ...`) and standalone
(`./externals/ec-sbm/scripts/run_ecsbm.sh --version v3 ...`):

- `--pso-gamma F`: PSO power-law exponent. Default `2.0`, which makes
  PSO's radial coordinates collapse to `r_i = 2 * log(arrival_rank)`.
  Combined with the descending-empirical-degree sort that decides
  arrival order, this means the highest-degree empirical node sits at
  the centre and the kth-highest at radius `2 log k`. Override only for
  ablation.
- `--pso-m-floor N`: hard lower bound on m. `m = min(max(k, this), n - 1)`.
  Default `1`. Raise to give triangle capacity to clusters whose
  empirical mincut is degenerate (k=1 trees).
- `--pso-search-strategy {bayesian|secant}`: default `secant`.
- `--pso-search-max-iters N`: T-search iter cap (default `30`).
- `--pso-search-initial-points N`: bayesian-only TPE `n_startup_trials`
  before the density model takes over (default `5`).
- `--pso-search-samples-per-T N`: PSO realisations averaged per probe
  (default `3`). Distinct seeds per realisation; the recorded ccoeff
  is the empirical mean.
- `--pso-search-diff-tol F`: stop when `|cc - target| < this`.
- `--pso-search-step-tol F`: stop when successive ccoeffs differ
  by less than this.
- `--pso-search-t-min F`, `--pso-search-t-max F`: search bracket.
- `--pso-initial-t F`: T used for the complete-graph regime
  (default `0.5`).

The other v2 flags (`--scope`, `--gen-outlier-mode`,
`--degree-matcher`) still apply to stages 3a / 4a; v3's preset
bundle copies v2's defaults there. See
[`../advanced-usage.md`](../advanced-usage.md) for the shared
match-degree surface.

## Where to look next

- [Source: `externals/ec-sbm/src/gen_clustered.py`](../../externals/ec-sbm/src/gen_clustered.py) (`--method pso` branch is the per-cluster T-search driver)
- [Source: `externals/ec-sbm/src/gen_pso_core.py`](../../externals/ec-sbm/src/gen_pso_core.py) (Python port of `nPSO_model.m` PSO branch)
- [Source: `externals/ec-sbm/src/gen_outlier.py`](../../externals/ec-sbm/src/gen_outlier.py) (residual SBM, unchanged from v2)
- [Source: `src/match_degree.py`](../../src/match_degree.py)
- [nPSO post](./npso.md) (the multi-cluster cousin; v3 uses the single-cluster degenerate case per ec-sbm cluster)
- [EC-SBM v2 post](./ec-sbm-v2.md)
- [Index of all generators](../algorithms.md)
