# ABCD+o: ABCD that knows about noise

[← back to index](../algorithms.md)

Real networks are messy. Some nodes don't belong to any community — bots, isolated users, sensor noise, one-off visitors. Plain [ABCD](./abcd.md) has no way to represent them: they either get folded into singletons (then dropped from the output clustering) or they distort the stats. ABCD+o is the explicit-outlier variant that models them as a first-class "background" block.

## What changes from ABCD

Exactly three things:

1. **Stage 1 saves an outlier count** in `n_outliers.txt` and puts only *real* clusters in `cluster_sizes.csv`.
2. **Stage 2 prepends the outlier count** as a size entry before the real sizes, and passes `n_outliers` (instead of `0`) as the Julia sampler's last arg.
3. **The sampler treats cluster_id=1 specially**: outlier nodes have zero internal edges by construction and never connect to other outliers.

Everything else — degree sequence, global ξ, configuration-model hybrid, rewiring — is the same.

## Why the outlier count goes in a separate file

`cluster_sizes.csv` is the list of *real* cluster sizes. `n_outliers.txt` is the count of background nodes. Keeping them separate means:

- You can compute cluster statistics without accidentally counting the outlier block.
- The sampler can distinguish "normal cluster of size N" from "outlier block of size N" — these are handled differently inside the Julia code.

The wrapper in [`src/abcd+o/gen.py`](../../src/abcd+o/gen.py) glues them back together only at the moment it shells out:

```python
cs_rows = []
if n_outliers > 0:
    cs_rows.append([n_outliers])
cs_rows.extend([[s] for s in cluster_sizes])
```

After the prepend, **cluster_id=1** in the Julia sampler's world is the outlier block.

## The `drop_outlier_outlier_edges` default

This is the one place ABCD+o deliberately deviates from every other generator: `drop_outlier_outlier_edges` defaults to `True`. The reason is semantic, not philosophical.

The Julia sampler *cannot* emit outlier–outlier edges. Outliers have `d_i^int = 0` — all their stubs are external — and the external configuration model is constrained so outliers only pair with non-outliers. So if we counted OO edges in the stage-1 degree statistics, we'd be giving the sampler degrees it can't fulfil. The ξ target would miss.

Dropping OO edges at profile time makes the stage-1 numbers *achievable* by the stage-2 sampler. That's the whole point.

## The "outliers form a community" warning

At low ξ (say, 0.1 or lower) with a large outlier block, the global external mechanism ends up wiring outliers densely enough that the outlier block has internal cohesion comparable to a real cluster. The Julia sampler notices and emits a warning to stderr:

```
outlier nodes form a community
```

[`src/abcd+o/gen.py:12,76-81`](../../src/abcd+o/gen.py#L76) catches this:

```python
OUTLIER_LIFT_WARNING = "outlier nodes form a community"
# ...
outliers_lifted = bool(re.search(OUTLIER_LIFT_WARNING, proc.stderr, re.IGNORECASE))
# ...
if n_outliers > 0 and not outliers_lifted:
    com_df = com_df[com_df["cluster_id"] != 1]
```

**If the warning fires**: cluster_id=1 stays in `com.csv`. The outlier block is treated as a real community in the output.

**If it doesn't fire**: cluster_id=1 is filtered from `com.csv`. Outlier nodes are still in `edge.csv` (they have edges, after all), but they're unclustered.

This branch is the reason visualizations of ABCD+o output should always show the warning status alongside the clustering — otherwise it's ambiguous whether an outlier node with no cluster_id is "an outlier by design" or "an outlier that got lifted and you forgot to check".

## What you get

Everything [ABCD](./abcd.md) guarantees, plus:

- **Exactly `n_outliers` background nodes** appear in `edge.csv`.
- **Zero outlier–outlier edges** by sampler construction.
- **com.csv contains or excludes cluster_id=1** depending on the warning heuristic.

## Determinism

Identical to ABCD. The warning detection is deterministic given a fixed seed because the Julia sampler's stderr output is deterministic.

## Cost

Same as ABCD — ~6.6 s mean, ~6.8 s cold on the dnc example. Adding outliers doesn't meaningfully change runtime.

## When to use

- **Yes, ABCD+o**: your empirical graph has genuine background / outlier nodes and you want them preserved in the synthetic.
- **Maybe, [ABCD](./abcd.md)**: your graph has no outlier concept (fully-clustered input, or you don't care).
- **Consider [nPSO](./npso.md)**: you want outliers *and* a clustering-coefficient target. nPSO also has an explicit outlier bucket (cluster_id=1 convention, coincidentally).

## Where to look next

- [Source: `src/abcd+o/gen.py`](../../src/abcd+o/gen.py)
- [Source: `src/abcd+o/profile.py`](../../src/abcd+o/profile.py)
- [ABCD (base variant)](./abcd.md)
- [Upstream: ABCDGraphGenerator.jl](https://github.com/bkamins/ABCDGraphGenerator.jl)
- [Index of all generators](../algorithms.md)
