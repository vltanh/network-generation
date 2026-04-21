# EC-SBM v1: the SBM that actually stays connected

[← back to index](../algorithms.md)

If plain SBM has a weakness beyond "tree-like", it's that clusters can come out *weaker* than the input in a structural sense: the sampler is free to concentrate intra-block edges on a few pairs, leaving the cluster vulnerable to a small edge cut. For some downstream analyses (robustness, minimum-cut attacks, densest-subgraph searches) that's a dealbreaker.

EC-SBM — edge-connected SBM — fixes it with a hybrid: **hand-build a provably k-edge-connected core per cluster, then let graph-tool fill the rest of the budget**. v1 is the first implementation; v2 (covered separately) is a later cleanup.

## What "k-edge-connected" means here

For a cluster C, we measure k(C) = the minimum number of edges you'd have to remove from the *input's* induced subgraph on C to disconnect it. Then we promise: **the output cluster has at least that same k-edge-connectivity**. "At least" because our construction can only add edges, never remove.

The min-cut is measured at profile time by `pymincut` using the Nagamochi–Ibaraki algorithm (the `"noi"` method with the `"bqueue"` heap variant). Singleton clusters get k=0 by definition.

## The four-stage pipeline

Unlike the simple generators (one profile step, one gen step), EC-SBM v1 has four stages with seed offsets for each:

```
Stage 1 (profile)             → profile files + mincut.csv + com.csv
Stage 2 (gen_clustered)       → clustered edges
Stage 3a (gen_outlier)        → outlier-incident edges
Stage 3b (combine)            → stage-2 + stage-3a merged, deduped
Stage 4a (match_degree)       → top-up edges for degree deficit
Stage 4b (combine)            → final merge with provenance
```

Each stage uses a distinct seed (`seed`, `seed+1`, `seed+2`) so its sampler trajectory is independent. The combined result carries a `sources.json` alongside `edge.csv` telling you which row range came from which stage — useful for visualization.

A note before we continue: v1 *forces* `--outlier-mode excluded` at profile time. You'll get an error if you try `combined` or `singleton`. Outliers are handled in stage 3a through a completely separate mechanism.

## Stage 2: building a k-edge-connected core

This is where EC-SBM earns its name. [`gen_clustered.py`'s `generate_cluster`](../../src/ec-sbm/v1/gen_clustered.py) runs per-cluster with two phases:

**Phase 1 — the K_{k+1} core.** Take the k+1 highest-degree nodes in the cluster. Make them a complete subgraph. That's k(k+1)/2 edges. A complete graph on k+1 vertices is, by elementary graph theory, exactly k-edge-connected. So if your cluster is any superset of these k+1 nodes, edge-connectivity ≥ k.

**Phase 2 — attach the rest.** Walk the remaining nodes in degree-descending order. For each new node `u`:

1. Try to wire `u` to the k highest-degree already-placed nodes, skipping any whose residual budget is exhausted.
2. If fewer than k were reachable that way, fall back to weighted-random choice over remaining candidates (weights = original profile degree).

Whenever we'd like to add an edge but the block-pair budget is zero or the partner is out of residual stubs, we call `ensure_edge_capacity`:

```python
if probs[b_u, b_v] == 0 or int_deg[v] == 0:
    int_deg[u] += 1
    int_deg[v] += 1
    probs[b_u, b_v] += 1
    probs[b_v, b_u] += 1
```

That is: **we never drop a required edge**. If the budget can't absorb it, we inflate the budget. This is the knob that makes v1's degree sequence an approximate, not exact, match.

## Stage 2, continued: the SBM overlay

After the constructive pass, `synthesize_sbm_network`:

```python
g = gt.generate_sbm(b, probs.tocsr(), out_degs=deg, ...)
g.add_edge_list(edges)   # overlay constructive on top
gt.remove_parallel_edges(g)
gt.remove_self_loops(g)
```

A subtle point: by this time, `probs` and `deg` have been *mutated* by the constructive phase — decremented each time we applied an edge, sometimes inflated. The SBM call is effectively sampling the *residual*, not the original profile. Then we overlay the constructive edges, and the dedup handles any (rare) collisions.

The clustered SBM doesn't see outliers at all because profile is forced `excluded`.

## Stage 3: outliers, handled separately

[`gen_outlier.py`](../../src/ec-sbm/v1/gen_outlier.py) re-reads the original edgelist and clustering, figures out which nodes are outliers (node IDs in the edgelist but not in the clustering), and treats **each outlier as its own block**. Then it samples an SBM on just the outlier-incident edges — edges where at least one endpoint is an outlier.

This is independent of stage 2. The clustered SBM filled in the between- and within-cluster edges; this stage covers everything that touches the outliers.

A separate combine step then concatenates stage-2 and stage-3a edgelists, dedups them undirected (keeping the first-seen row so stage-2 takes priority), and writes a `sources.json` remembering which rows came from which phase.

## Stage 4: degree matching

After all the constructive + SBM + dedup, some nodes will still be short of their target degree (from the original edgelist). [`match_degree.py`](../../src/ec-sbm/v1/match_degree.py) runs a heap-based max-degree greedy to top them off:

1. Start with a max-heap of `(−residual_degree, node_id)` for every node still missing stubs.
2. Pop the node with the highest residual, `u`. Look at its *available non-neighbors* — nodes that (a) aren't already connected to `u`, (b) aren't `u` itself, (c) still have residual.
3. Add an edge from `u` to each of up to `min(residual[u], |non-neighbors|)` partners, picked by `set.pop()` (arbitrary order — this is where `PYTHONHASHSEED=0` earns its keep).
4. Decrement partner residuals; remove from heap if zero. Remove `u` from the heap unconditionally.

If `u` runs out of valid partners before residual hits zero, the remaining stubs are silently dropped. v1 doesn't log this — v2 does (it's one of the main reasons v2 exists).

## What you get

- **N exact** after the `excluded` outlier transform.
- **k-edge-connectivity ≥ k(C) per cluster**: mathematically from the K_{k+1} core.
- **Block structure exact**: nodes stay in their input clusters.
- **Degree sequence approximate**: inflation pushes up, dedup and gridlock push down.
- **Inter-cluster counts approximate**: overlay and dedup perturb them.
- **Clustering coefficient**: not targeted (but tends higher than plain SBM because of the K_{k+1} cores, which are dense).

## Determinism

Three RNGs (`random`, `numpy`, `graph-tool`), seeded per stage with offsets `seed` / `seed+1` / `seed+2`. `PYTHONHASHSEED=0` is exported by `pipeline.sh` and is load-bearing — without it, `set.pop()` in `match_degree` picks different elements on different runs.

Same `--seed 0` footgun as plain SBM (graph-tool treats 0 as "use entropy"). Default is `--seed 1`.

## Cost

On the dnc example, single-threaded:

- Kept mean: ~9.8 s
- Cold: ~10.7 s

Slower than SBM because of the per-cluster sort loops and the extra stages.

## When to use v1 vs v2

Short answer: prefer v2. It's the architectural successor. v1 stays in the repo for comparison and because its outputs are byte-different at equal seeds (v2 isn't a bug-for-bug clone).

Long answer: v1's overlay-on-mutated-probs design makes degree accounting hard to reason about. v2 does residual accounting properly and has five matcher algorithms for the top-up stage. See the [v2 blog post](./ec-sbm-v2.md) for the difference.

## Where to look next

- [Source: `src/ec-sbm/v1/gen_clustered.py`](../../src/ec-sbm/v1/gen_clustered.py)
- [Source: `src/ec-sbm/v1/gen_outlier.py`](../../src/ec-sbm/v1/gen_outlier.py)
- [Source: `src/ec-sbm/v1/match_degree.py`](../../src/ec-sbm/v1/match_degree.py)
- [Source: `src/ec-sbm/common/profile.py`](../../src/ec-sbm/common/profile.py)
- [EC-SBM v2 post](./ec-sbm-v2.md)
- [Plain SBM post](./sbm.md)
- [Index of all generators](../algorithms.md)
