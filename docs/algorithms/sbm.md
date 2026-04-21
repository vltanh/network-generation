# SBM

[← back to index](../algorithms.md)

The SBM generator is the thinnest wrapper in the repo. It hands graph-tool a
degree sequence, a block assignment, and a block-edge-count matrix, asks for a
micro-canonical degree-corrected SBM, and then cleans up the self-loops and
parallel edges that the micro constraint leaves behind. Everything
interesting happens inside `graph_tool.generate_sbm`.

## What the model does

A stochastic block model places each node in a block (community) and samples
edges based on a block-pair rate matrix `e_{rs}`. Degree-corrected variants add
per-node degree as a second constraint, so hubs stay hubs. The *micro*
flavour makes both constraints exact on the sampled multigraph: the
inter-block counts match `e_{rs}` exactly and the degree sequence matches the
input exactly, before any post-hoc simplification.

We pass `micro_degs=True` and `micro_ers=True` to `gt.generate_sbm`. This is
the strictest degree-corrected SBM graph-tool offers.

## Stage 1: the profile

[`src/sbm/profile.py`](../../src/sbm/profile.py) reads your edgelist and
reference clustering, folds outliers into a single `__outliers__` pseudo-cluster
(the default `combined` mode), and writes five CSVs. An outlier here is any
node that is either unclustered or the sole member of a size-1 cluster.

| File | What it is |
| --- | --- |
| `node_id.csv` | Node IDs in degree-descending order (ties by ID asc) |
| `cluster_id.csv` | Cluster IDs in size-descending order |
| `assignment.csv` | Per-node cluster iid (index into `cluster_id.csv`) |
| `degree.csv` | Per-node integer degree |
| `edge_counts.csv` | `(row, col, weight)` triples for the block matrix |

The edge-count matrix is built by walking each edge twice (once per direction).
So off-diagonal `probs[r, s]` equals the count of edges between blocks `r`
and `s`, and the diagonal `probs[k, k]` equals twice the count of intra-block
edges in block `k`. The doubling is graph-tool's undirected convention: each
intra-block edge is two half-edges, both landing in the same block.

## Stage 2: one call, two cleanups

The generation step in [`src/sbm/gen.py`](../../src/sbm/gen.py) is three lines:

```python
g = gt.generate_sbm(
    assignments, probs, out_degs=degrees,
    micro_ers=True, micro_degs=True, directed=False,
)
gt.remove_parallel_edges(g)
gt.remove_self_loops(g)
```

The micro-SBM sampler is allowed to emit multigraphs. It hits the count
constraints as counts, not as distinct edges. If a small block has a high
intra-block count, you get self-loops; if two hubs are the only thing holding
up a large `probs[r, s]`, you get parallel edges. Both are simplified away.

On sparse empirical networks this loss is small. On denser cases it can be
larger: the hard ceiling per cell is "how many distinct pairs actually exist".
The ceiling is the same one in the canonical SBM, just less visible there
because canonical SBM marginals already absorb it.

## What you get on the shipped example

Default run on the [`dnc`](../../examples/input/empirical_networks/networks/dnc/)
input with [`sbm-flat-best+cc`](../../examples/input/reference_clusterings/clusterings/sbm-flat-best+cc/dnc/)
clustering at `--seed 1`:

| Stat | Input | SBM output | Note |
| --- | --- | --- | --- |
| N | 906 | 902 | 4 nodes isolated after dedup |
| Edges | 10429 | 7438 | 29% lost to multi-edge / self-loop removal |
| Mean degree | 23.02 | 16.49 | tracks the edge loss |
| Global clustering coeff. | 0.548 | 0.341 | lower (DC-SBM tends toward tree-like) |
| Mean k-core | 15.99 | 10.49 | |
| Num clusters | 42 | 42 | exact (passthrough of input) |

The big number to read is the 29% edge loss. That is the dedup bill for this
input: a highly clustered graph (global ccoeff 0.548) compressed through a
micro-SBM whose block matrix was filled with duplicate-heavy cells.

## Output guarantees

| Property | Status |
| --- | --- |
| N | exact after the `combined` outlier transform |
| Block structure | exact (each node stays in its input block) |
| Degree sequence | exact pre-dedup; upper bound post-dedup |
| Inter-block counts `e_{rs}` | exact pre-dedup; upper bound post-dedup |
| Clustering coefficient / triangle count | not targeted |

The tree-likeness is the standard DC-SBM caveat. If the input has lots of
triangles, the output looks diffuse by comparison. That is a property of the
DC-SBM as a generative model, not of this implementation.

## Determinism

Three RNGs seeded at the start of stage 2:

```python
np.random.seed(seed)
gt.seed_rng(seed)
gt.openmp_set_num_threads(n_threads)
```

`PYTHONHASHSEED=0` is exported from
[`pipeline.sh`](../../src/sbm/pipeline.sh). This is load-bearing.
`gt.generate_sbm` is sensitive to Python set/dict iteration order in some
code paths, so without a pinned hash seed reruns at the same `--seed` diverge.

`--seed 0` is a trap: graph-tool treats 0 as "use entropy source", which
disables reproducibility. The default everywhere in this repo is `--seed 1`.

## Cost

Measured via [`scripts/benchmark/bench_isolated.sh`](../../scripts/benchmark/bench_isolated.sh)
on an isolated cgroup (4 cores, 16 GiB cap), 10 seeds x 10 kept runs per seed:

- kept mean: 1.65 s
- kept std: 0.03 s

See the [index](../algorithms.md) for the full per-generator table. SBM is the
fastest of the seven at this input size.

## Where to look next

- [Source: `src/sbm/gen.py`](../../src/sbm/gen.py)
- [Source: `src/sbm/profile.py`](../../src/sbm/profile.py)
- [graph-tool's `generate_sbm` docs](https://graph-tool.skewed.de/static/doc/generation.html#graph_tool.generation.generate_sbm)
- [Interactive GUI: sbm steps at default settings](https://vltanh.me/netgen/sbm.html)
- [Index of all generators](../algorithms.md)
