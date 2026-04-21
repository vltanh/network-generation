# SBM: the straight-shooter

[← back to index](../algorithms.md)

If you only remember one thing about the SBM generator, remember this: **it hands graph-tool a degree sequence, a block assignment, and a block-edge-count matrix, says "match these exactly", and then cleans up the inevitable mess**. Almost everything interesting happens inside `graph_tool.generate_sbm`. Our job on both ends is plumbing.

Let's walk through it.

## What the model is, in plain-ish words

Stochastic Block Models are the community-detection world's equivalent of "draw a histogram, then throw darts inside each bar." Each node lives in a *block* (think: community). You say "I want this many edges between block 1 and block 2, and this many inside block 1" and the sampler obliges. The degree-corrected variant adds a second constraint: **every node's degree must come out right too**, not just each block's total.

graph-tool supports two flavours of the degree-corrected SBM:

- **Canonical** (macro): degrees and block counts match *in expectation*. Each sample is a draw from a Poisson-ish distribution.
- **Micro-canonical**: degrees and block counts match *exactly* on the sampled multigraph. No wiggle room.

We use the micro flavour by passing `micro_degs=True` and `micro_ers=True` to `gt.generate_sbm`. This is the strictest degree-corrected SBM you can ask for.

## Stage 1: what we pull out of your graph

[`src/sbm/profile.py`](../../src/sbm/profile.py) reads your edgelist + clustering and emits five CSV files. The default outlier policy is `combined` — every unclustered node (plus the sole member of any size-1 cluster) gets folded into one giant pseudo-cluster named `__outliers__`. This keeps the block matrix square and avoids special cases downstream.

The five files:

| File | What it is |
| --- | --- |
| `node_id.csv` | Original node IDs in degree-descending order (ties broken by ID ascending) |
| `cluster_id.csv` | Cluster IDs in size-descending order |
| `assignment.csv` | Per-node: index into `cluster_id.csv` |
| `degree.csv` | Per-node: integer degree |
| `edge_counts.csv` | Triples `(row, col, weight)` for the inter-block edge matrix |

One subtlety worth flagging: the edge-counts matrix is built by walking every edge **twice** — once as `(u, v)`, once as `(v, u)`. So off-diagonal `probs[r, s]` and `probs[s, r]` are both equal to the edge count between blocks `r` and `s`, and the diagonal `probs[k, k]` equals *twice* the number of intra-k edges. That doubling isn't a bug; it's exactly what graph-tool wants for undirected inputs (each intra-block edge is two half-edges, both landing in the same block).

## Stage 2: one function call, two cleanups

[`src/sbm/gen.py:44-51`](../../src/sbm/gen.py#L44) is the whole generation step:

```python
g = gt.generate_sbm(
    assignments,
    probs,
    out_degs=degrees,
    micro_ers=True,
    micro_degs=True,
    directed=False,
)
```

That's it. graph-tool does the heavy lifting.

Two lines later:

```python
gt.remove_parallel_edges(g)
gt.remove_self_loops(g)
```

This is where you lose a small amount of what the profile asked for. The micro-SBM sampler is allowed to emit multigraphs — it fulfills the count constraints as counts, not as *distinct edges*. If a block is small and its internal count is high, you get self-loops. If two hubs are the only thing holding up a large `probs[r, s]`, you get parallel edges. We kill both.

On sparse empirical networks this loss is under 1%. On dense synthetic cases it can be worse — the limit is "how many distinct pairs actually exist in each cell". We don't currently log how many edges the dedup dropped; that would be a nice addition.

## What you get

| Thing you wanted | Did you get it? |
| --- | --- |
| Same node count | Yes, exact |
| Same block structure | Yes, exact (nodes stay in their blocks) |
| Same per-node degree | Pre-dedup: exact. Post-dedup: upper bound. |
| Same inter-block edge counts | Pre-dedup: exact. Post-dedup: upper bound. |
| Same clustering coefficient / triangle density | No. DC-SBM is nearly tree-like. |
| Same path lengths / mixing time / motif counts | No. |

The tree-likeness is the famous weakness of the whole SBM family. If your real graph has lots of triangles (most social networks), SBM output looks weirdly diffuse in comparison. That's a limitation of the model, not of this implementation.

## Determinism traps

We seed three RNGs:

```python
np.random.seed(seed)
gt.seed_rng(seed)
gt.openmp_set_num_threads(n_threads)
```

And we export `PYTHONHASHSEED=0` from [`pipeline.sh:36`](../../src/sbm/pipeline.sh#L36). That last one matters. `gt.generate_sbm` reads Python set/dict iteration order somewhere inside, and without a pinned hash seed you get different output on reruns even with the same `seed`. The comment in the pipeline file spells this out.

**The footgun**: `--seed 0` does *not* mean "seed with zero". graph-tool treats 0 as "use the entropy source", which silently disables reproducibility. The default everywhere in this repo is `--seed 1`. If you need a "null" seed, use 1 and keep moving.

## Cost

On the dnc example network, single-threaded:

- Kept mean: ~4.0 s
- Cold start: ~21 s

The cold cost is entirely graph-tool's C++ library load + Python-side PRNG setup. Warm up with a throwaway run if you're batching.

## Where to look next

- [Source: `src/sbm/gen.py`](../../src/sbm/gen.py)
- [Source: `src/sbm/profile.py`](../../src/sbm/profile.py)
- [graph-tool's `generate_sbm` docs](https://graph-tool.skewed.de/static/doc/generation.html#graph_tool.generation.generate_sbm)
- [Index of all generators](../algorithms.md)
