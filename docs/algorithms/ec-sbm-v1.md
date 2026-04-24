# EC-SBM v1

[← back to index](../algorithms.md)

Plain SBM can produce clusters that are structurally weaker than the input:
the sampler is free to concentrate intra-block edges on a few pairs, leaving
the cluster vulnerable to a small edge cut. For downstream work on robustness
or minimum-cut attacks that matters.

EC-SBM (edge-connected SBM) adds a constraint: the output cluster's
edge-connectivity is at least as high as the input's. v1 is the first
implementation; v2 is the cleanup.

## What "k-edge-connected" means here

For a cluster C, k(C) is the minimum number of edges you would remove from
the *input's* induced subgraph on C to disconnect it. EC-SBM promises that
the output's cluster C has edge-connectivity at least k(C). The "at least"
is because the construction only adds edges, never removes them.

k(C) is measured at profile time by
[`pymincut`](https://github.com/llekha/pymincut)'s Nagamochi-Ibaraki algorithm
(the `"noi"` method with `"bqueue"`). Singleton clusters get k=0 by definition.

## The four stages

Unlike the simple generators (one profile, one gen), EC-SBM v1 runs four
stages with a separate seed offset per stage:

```
Stage 1 (profile)        profile files + mincut.csv + com.csv
Stage 2 (gen_clustered)  clustered edges
Stage 3a (gen_outlier)   outlier-incident edges
Stage 3b (combine)       stage-2 + stage-3a merged, deduped, sources.json
Stage 4a (match_degree)  top-up edges for degree deficit
Stage 4b (combine)       final edge.csv + sources.json
```

Offsets: `seed`, `seed+1`, `seed+2`. The combined `edge.csv` carries a
`sources.json` that maps each stage's label to an inclusive 1-based row range.

v1 also *forces* `--outlier-mode excluded` at profile time. Passing
`combined` or `singleton` errors out. Outliers get a dedicated SBM pass in
stage 3a.

## Stage 2: building the k-edge-connected core

[`externals/ec-sbm/src/gen_kec_core.py`](../../externals/ec-sbm/src/gen_kec_core.py)'s
`generate_cluster` runs per cluster in two phases.

**Phase 1: the K_{k+1} core.** Take the k+1 highest-degree nodes in the
cluster. Wire them into a complete subgraph. That is k(k+1)/2 edges. A
complete graph on k+1 vertices is k-edge-connected, so every cluster that
contains these k+1 nodes is automatically k-edge-connected.

**Phase 2: attach the rest.** Walk remaining nodes in descending-degree order.
For each new node u:

1. Try to wire u to the k highest-degree already-processed nodes, skipping
   any whose residual degree is zero.
2. If fewer than k partners were reachable, fall back to a numpy-weighted
   random choice over the remaining candidates (weights = *original* profile
   degree, not residual).

When an edge is required but the block-pair budget is zero or the partner is
out of residual stubs, `ensure_edge_capacity` fires:

```python
if probs[b_u, b_v] == 0 or int_deg[v] == 0:
    int_deg[u] += 1; int_deg[v] += 1
    probs[b_u, b_v] += 1; probs[b_v, b_u] += 1
```

The constructive phase never drops a required edge. If the budget would
block it, the budget is inflated. That is the knob that makes the output
degree sequence approximate rather than exact.

## Stage 2, continued: the SBM overlay

After the constructive pass, v1's `gen_clustered.py` calls
`gt.generate_sbm` on the *mutated* probs and degree arrays, then overlays
the constructive edges and simplifies:

```python
g = gt.generate_sbm(b, probs.tocsr(), out_degs=deg, ...)
g.add_edge_list(edges)
gt.remove_parallel_edges(g)
gt.remove_self_loops(g)
```

The subtle point: `probs` and `deg` have been decremented (and sometimes
inflated) by the constructive phase. The SBM call samples the *residual*,
not the original profile. The constructive edges are then overlaid. If an
SBM-sampled edge collides with a constructive edge, `remove_parallel_edges`
drops one.

This double-accounting (constructive picks some edges, SBM picks more in the
same cells, dedup drops the collisions) is the main reason v2 exists.

## Stage 3a: outlier-only SBM

[`externals/ec-sbm/src/gen_outlier.py`](../../externals/ec-sbm/src/gen_outlier.py)
re-reads the original edgelist + clustering, identifies outliers (nodes in
the edgelist but not in the clustering), and treats each outlier as its own
size-1 block. It then samples an SBM on just the outlier-incident edges.

Non-outlier-incident edges are not touched: the clustered SBM already handled
those in stage 2.

## Stage 3b: combine clustered + outliers

[`src/combine_edgelists.py`](../../src/combine_edgelists.py) concatenates
stage-2 and stage-3a edgelists, labels each row with provenance (`"clustered"`
or `"outlier"`), undirected-dedups with first-seen wins (so stage 2 takes
priority), emits `edge.csv` plus a `sources.json` with inclusive 1-based row
ranges per provenance band.

## Stage 4a: heap-greedy degree matching

Some nodes are still short of their target degree after stage 3b, because
dedup removed their edges. Stage 4a tops them off using the shared
[`src/match_degree.py`](../../src/match_degree.py) tool. v1 hardcodes
`--match-degree-algorithm greedy` for byte-compat with the original v1
implementation:

1. Build a max-heap of `(-residual_degree, node_id)` for nodes still short.
2. Pop u. Compute u's available non-neighbors (live nodes minus u and its
   current neighbors).
3. Pop partners via `set.pop()` until u's residual is zero or candidates run
   out. Each pair becomes an edge; decrement both residuals.
4. If u runs out of valid partners before hitting zero, the remaining stubs
   are dropped silently. v1 does not log this.

`set.pop()` picks an arbitrary element, so `PYTHONHASHSEED=0` is load-bearing
here.

## Stage 4b: final combine

Merge stage-3b's `edge.csv` with stage-4a's `degree_matching_edge.csv` using
`combine_edgelists.py`. Pass stage-3b's `sources.json` as `--json-1` so all
three provenance bands (`clustered`, `outlier`, `match_degree`) land in the
final `sources.json`.

## What you get on the shipped example

Default run on dnc + sbm-flat-best+cc at `--seed 1`:

| Stat | Input | v1 output | Note |
| --- | --- | --- | --- |
| N | 906 | 906 | exact |
| Edges | 10429 | 10422 | within 0.07% (match-degree fills most of the dedup loss) |
| Mean degree | 23.02 | 23.01 | tracks the edge count |
| Global clustering coeff. | 0.548 | 0.424 | higher than plain SBM (0.341) thanks to K_{k+1} cores |
| Mean k-core | 15.99 | 13.84 | |

`com.csv` is a stage-1 passthrough with singleton clusters dropped, so the
block structure matches the input exactly.

## Output guarantees

- **N** exact after the `excluded` outlier transform.
- **k-edge-connectivity at least k(C) per cluster** by K_{k+1} construction.
- **Block structure** exact.
- **Degree sequence** approximate: inflation pushes up, dedup and gridlock
  push down.
- **Inter-cluster edge counts** approximate: overlay + dedup perturb them.
- **Clustering coefficient** not targeted, but higher than plain SBM because
  the K_{k+1} cores are dense.

## Determinism

Three RNGs seeded per stage (`random`, `numpy`, `graph-tool`) with offsets
`seed` / `seed+1` / `seed+2`. `PYTHONHASHSEED=0` is load-bearing for
`set.pop()` in match_degree and for the candidate-set iteration in
`gen_clustered`'s phase-2 fallback. Same `--seed 0` trap as plain SBM.

## Cost

10 seeds x 10 kept runs on 4 cores, 16 GiB cgroup cap:

- kept mean: 2.83 s
- kept std: 0.05 s

## v1 vs v2

Short answer: prefer v2. v2 has cleaner residual accounting (one SBM call on
the residual instead of an overlay on mutated probs) and a choice of five
matcher algorithms, most of which log gridlock rather than dropping stubs
silently. v1 stays in the repo for comparison. See
[ec-sbm-v2](./ec-sbm-v2.md) for the details.

## CLI flags

Dispatcher (`run_generator.sh`):

- `--ec-sbm-dir <p>`: path to the ec-sbm submodule (default `externals/ec-sbm`). Forwarded to the pipeline wrapper as `--package-dir`.

Pipeline (`./src/ec-sbm/pipeline.sh`):

- `--package-dir <p>`: required; path to the ec-sbm submodule that contains the algorithm Python modules.
- `--outlier-mode`: only `excluded` is accepted; any other value errors. Outliers are synthesized by Stage 3.
- Stage 4 match-degree always runs with algorithm fixed to `greedy` (not user-toggleable).

See [../advanced-usage.md](../advanced-usage.md).

## Where to look next

- [Source: `externals/ec-sbm/src/gen_clustered.py`](../../externals/ec-sbm/src/gen_clustered.py) (unified; v1 is the `--sbm-overlay` preset)
- [Source: `externals/ec-sbm/src/gen_outlier.py`](../../externals/ec-sbm/src/gen_outlier.py) (unified; v1 is `--scope outlier-incident --outlier-mode singleton --edge-correction none`)
- [Source: `src/match_degree.py`](../../src/match_degree.py)
- [Source: `externals/ec-sbm/src/profile.py`](../../externals/ec-sbm/src/profile.py)
- [Interactive GUI: ec-sbm-v1 steps at default settings](https://vltanh.me/netgen/ec-sbm-v1.html)
- [EC-SBM v2 post](./ec-sbm-v2.md)
- [Plain SBM post](./sbm.md)
- [Index of all generators](../algorithms.md)
