# ABCD+o

[← back to index](../algorithms.md)

Real networks often include nodes that do not belong to any community:
bots, isolated users, sensor noise, one-off visitors. [Plain ABCD](./abcd.md)
folds them into singletons which get filtered out. ABCD+o is the
outlier-aware variant that models them as a first-class background block.

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

## Why the outlier count is a separate file

`cluster_sizes.csv` is the list of *real* cluster sizes.
`n_outliers.txt` is the count of background nodes. Keeping them separate
means:

- Cluster statistics can be computed without accidentally counting the
  outlier block.
- The sampler distinguishes "normal cluster of size N" from "outlier
  block of size N": it applies different constraints internally.

The wrapper in [`src/abcd+o/gen.py`](../../src/abcd+o/gen.py) rejoins them
only at the moment it shells out:

```python
cs_rows = []
if n_outliers > 0:
    cs_rows.append([n_outliers])
cs_rows.extend([[s] for s in cluster_sizes])
```

After the prepend, cluster_id=1 in the Julia sampler's world is the
outlier block.

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

## The "outliers form a community" warning

At low ξ (say 0.1 or below) with a large outlier block, the global
external mechanism ends up wiring outliers densely enough that the
outlier block has internal cohesion comparable to a real cluster. The
Julia sampler notices and emits a warning to stderr:

```
outlier nodes form a community
```

The wrapper catches this:

```python
OUTLIER_LIFT_WARNING = "outlier nodes form a community"
outliers_lifted = bool(re.search(OUTLIER_LIFT_WARNING, proc.stderr, re.IGNORECASE))
if n_outliers > 0 and not outliers_lifted:
    com_df = com_df[com_df["cluster_id"] != 1]
```

Behaviour depends on whether the warning fires:

- **Warning fires**: cluster_id=1 stays in `com.csv`. The outlier block is
  treated as a real community in the output.
- **Warning silent**: cluster_id=1 is filtered from `com.csv`. Outlier
  nodes are still in `edge.csv` (they have edges, after all), but
  unclustered.

Visualizations of ABCD+o output should show the warning status: otherwise
it is ambiguous whether an outlier node with no cluster_id is "an outlier
by design" or "an outlier that got lifted and you forgot to check".

## What you get on the shipped example

Default run on dnc + sbm-flat-best+cc at `--seed 1`. The dnc input has 355
outliers at the `singleton + drop_oo=true` setting:

| Stat | Input | ABCD+o output | Note |
| --- | --- | --- | --- |
| N | 906 | 673 | 355 outliers stripped from com.csv (warning did not fire) |
| Edges | 10429 | 10070 | within 3.4% |
| Mean degree | 23.02 | 29.93 | higher because the denominator is non-outlier nodes |
| Global clustering coeff. | 0.548 | 0.307 | not targeted |
| Num clusters | 42 | 42 | exact |

The output `edge.csv` still contains all 906 nodes (the 355 outliers have
their incident edges); it is only `com.csv` that drops the outlier block
in the no-warning branch.

## Output guarantees

Everything [ABCD](./abcd.md) guarantees, plus:

- Exactly `n_outliers` background nodes appear in `edge.csv`.
- Zero outlier-outlier edges by sampler construction.
- `com.csv` contains or excludes cluster_id=1 depending on the warning
  heuristic.

## Determinism

Identical to ABCD. The warning detection is deterministic given a fixed
seed because the Julia sampler's stderr is deterministic.

## Cost

10 seeds x 10 kept runs on 4 cores, 16 GiB cgroup cap:

- kept mean: 3.85 s
- kept std: 0.05 s

Adding outliers does not meaningfully change runtime vs plain ABCD.

## When to use

- **Yes**: your empirical graph has genuine background nodes and you want
  them preserved.
- **Maybe**: [ABCD](./abcd.md) if your graph has no outlier concept.
- **Consider**: [nPSO](./npso.md) if you want a clustering-coefficient
  target.

## CLI flags

Dispatcher (`run_generator.sh`):

- `--abcd-dir <p>`: path to `ABCDGraphGenerator.jl` checkout. Default `externals/abcd`.

Pipeline (`./src/abcd+o/pipeline.sh`):

- `--package-dir <p>`: same role, short form at pipeline layer.
- `--outlier-mode <excluded|singleton|combined>`: default `singleton`.
- `--drop-outlier-outlier-edges` / `--keep-outlier-outlier-edges`: **default drop** (Julia sampler cannot produce OO edges).
- `--match-degree` / `--no-match-degree`: default off.
- `--match-degree-algorithm <greedy|true_greedy|random_greedy|rewire|hybrid>`: default `hybrid`.
- `--remap` / `--no-remap`: default on.

See [../advanced-usage.md](../advanced-usage.md).

## Where to look next

- [Source: `src/abcd+o/gen.py`](../../src/abcd+o/gen.py)
- [Source: `src/abcd+o/profile.py`](../../src/abcd+o/profile.py)
- [ABCD (base variant)](./abcd.md)
- [Upstream: ABCDGraphGenerator.jl](https://github.com/bkamins/ABCDGraphGenerator.jl)
- [Interactive GUI: abcd+o steps at default settings](https://vltanh.me/netgen/abcd+o.html)
- [Index of all generators](../algorithms.md)
