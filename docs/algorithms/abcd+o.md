# ABCD+o

[← back to index](../algorithms.md)

## What changes from ABCD

Three things:

1. Stage 1 writes `n_outliers.txt` and puts only *real* clusters in
   `cluster_sizes.csv`.
2. Stage 2 prepends the outlier count as a size entry before the real
   sizes, and passes `n_outliers` (instead of `0`) as the Julia sampler's
   last positional argument.
3. The sampler treats cluster_id=1 specially: outlier nodes have zero
   internal edges by construction and never connect to other outliers.

Everything else (degree sequence, global ξ, configuration-model hybrid,
rewiring) is the same.

## The `drop_outlier_outlier_edges` default

ABCD+o is the one generator where `drop_outlier_outlier_edges` defaults to
`True`. The reason is semantic, not philosophical.

The Julia sampler cannot emit outlier-outlier edges. Outliers have
`d_i^int = 0` (all their stubs are external), and the external
configuration model is constrained so outliers only pair with
non-outliers. So if we counted OO edges in the stage-1 degree statistics,
we would be giving the sampler degrees it cannot fulfil. The ξ target
would drift.

Dropping OO edges at profile time makes the stage-1 numbers achievable by
the stage-2 sampler.

## Output guarantees

Everything [ABCD](./abcd.md) guarantees, plus:

- Outlier endpoints in `edge.csv` ≤ `n_outliers`. Strict equality is
  aspirational: the Julia sampler rewires self-loops + multi-edges, and
  isolated outliers can end up with zero stubs and drop out of the
  materialised edge list.
- Zero outlier-outlier edges by sampler construction.
- `com.csv` contains or excludes cluster_id=1 depending on the warning
  heuristic.

## Determinism

Identical to ABCD. The warning detection is deterministic given a fixed
seed because the Julia sampler's stderr is deterministic.

## Cost

Adding outliers does not meaningfully change runtime vs plain ABCD.
Concrete numbers live in `examples/benchmark/summary.csv`, refreshed by
[`tools/benchmark/bench_isolated.sh`](../../tools/benchmark/bench_isolated.sh).

## CLI flags

Dispatcher (`run_generator.sh`):

- `--abcd-dir <p>`: path to `ABCDGraphGenerator.jl` checkout. Default `externals/abcd`.

Pipeline (`./src/abcd+o/pipeline.sh`):

- `--package-dir <p>`: same role, short form at pipeline layer.
- `--outlier-mode <excluded|singleton|combined>`: default `singleton`.
- `--drop-outlier-outlier-edges` / `--keep-outlier-outlier-edges`: **default drop** (Julia sampler cannot produce OO edges).
- `--match-degree` / `--no-match-degree`: default off.
- `--match-degree-algorithm <a>`: default `true_greedy`. Any key from `src/match_degree.py:ALGO_TABLE` (the global five plus the `cluster_preserving_*` five); see [`../advanced-usage.md`](../advanced-usage.md).
- `--match-degree-mode <global|cluster_preserving>`: default `global`.
- `--remap` / `--no-remap`: default on.

See [../advanced-usage.md](../advanced-usage.md).

## Where to look next

- [Source: `src/abcd+o/gen.py`](../../src/abcd+o/gen.py)
- [Source: `src/abcd+o/profile.py`](../../src/abcd+o/profile.py)
- [ABCD (base variant)](./abcd.md)
- [Upstream: ABCDGraphGenerator.jl](https://github.com/bkamins/ABCDGraphGenerator.jl)
- [Interactive GUI: abcd+o steps at default settings](https://vltanh.me/netgen/abcd+o.html)
- [Index of all generators](../algorithms.md)
