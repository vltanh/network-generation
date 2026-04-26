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

The generation step in [`src/sbm/gen.py`](../../src/sbm/gen.py) hands the
profile straight to graph-tool, then runs the project-wide pandas
simplifier:

```python
g = gt.generate_sbm(
    assignments, probs, out_degs=degrees,
    micro_ers=True, micro_degs=True, directed=False,
)
edges = [(node_ids[int(s)], node_ids[int(t)]) for s, t in g.iter_edges()]
edge_df = simplify_edges(pd.DataFrame(edges, columns=["source", "target"]))
```

`pipeline_common.simplify_edges` drops self-loops and collapses parallel
edges. graph-tool ships its own `remove_parallel_edges` / `remove_self_loops`
that would do the same job; we keep one shared simplifier across every
generator (sbm, ec-sbm, abcd, lfr, npso) so the on-disk `edge.csv` contract
is identical regardless of upstream toolchain.

The micro-SBM sampler is allowed to emit multigraphs. It hits the count
constraints as counts, not as distinct edges. If a small block has a high
intra-block count, you get self-loops; if two hubs are the only thing holding
up a large `probs[r, s]`, you get parallel edges. Both are simplified away.

On sparse empirical networks this loss is small. On denser cases it can be
larger: the hard ceiling per cell is "how many distinct pairs actually exist".
The ceiling is the same one in the canonical SBM, just less visible there
because canonical SBM marginals already absorb it.

### What the kernel actually does

Reading
[graph_sbm.hh](../../graph-tool/src/graph/generation/graph_sbm.hh)
verbatim: the kernel builds one urn per block, populated by every node `v`
with `b_v = r` repeated `k_v` times. Stubs in the urn are not typed by
target block. For each `(r, s)` pair with `r <= s` in row-major order on
the input matrix, the kernel pulls `e_{rs}` half-edges out of urn `r` (and
another `e_{rs}` from urn `s` when `r != s`) without replacement, pairing
them up into edges. So the per-pair edge count and the per-node degree
land exactly on the input; what fluctuates is which specific stubs of `v`
end up in which `(r, s)` cell, i.e. v's per-block-degree profile is hit
in expectation only.

The only consistency check is per-pair: `urn_r.size() >= e_{rs}` and the
same on the `s` side. If they fail, graph-tool throws
`"Inconsistent SBM parameters: node degrees do not agree with matrix of
edge counts between groups"`. A consistent profile (sum of degrees in
block r equals the row sum of e_{r,*}) drains every urn to zero.

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
| Mean local CC | 0.494 | 0.216 | same story per node |
| Num clusters | 87 | 87 | exact (passthrough of input, minus singletons) |

The big number to read is the 29% edge loss. That is the dedup bill for this
input: a highly clustered graph (global ccoeff 0.548) compressed through a
micro-SBM whose block matrix was filled with duplicate-heavy cells.

## Outlier mode: a benchmarking trap

The default `--outlier-mode combined` folds every outlier into one
`__outliers__` pseudo-block. Outlier&ndash;cluster edges aggregate at the
pseudo-block level, so the sampler has freedom to reshuffle which specific
outlier connects to which cluster member.

Switch to `--outlier-mode singleton` and every outlier becomes its own
size-1 block, with its own row in the edge count matrix. The urn for that
block contains a single node repeated `k_u` times, so the sampler has no
choice on the outlier side: for each pair of outliers `(u, v)` with an
input edge, the sampler draws u's only-stub and v's only-stub and emits
exactly the edge `(u, v)`; for each pair `(u, c)` with `c` a real cluster,
the sampler keeps `u`'s per-cluster degree exact and only rerolls the
specific cluster-side neighbour.

The motivation for this project is benchmarking community detection: we
hand an algorithm a synthetic graph plus a ground-truth clustering and
ask whether it can recover the truth. Singleton mode breaks that contract
when outliers themselves carry community structure that the reference
clustering missed. The synthetic preserves that structure verbatim while
the ground truth claims those nodes are unclustered. Use the default
`combined` mode for benchmarks. Singleton mode is useful when the goal is
faithful replication, not a clean ground truth.

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

Measured via [`benchmark/bench_isolated.sh`](../../benchmark/bench_isolated.sh)
on an isolated cgroup (4 cores, 16 GiB cap), 10 seeds x 10 kept runs per seed:

- kept mean: 1.65 s
- kept std: 0.03 s

See the [index](../algorithms.md) for the full per-generator table. SBM is the
fastest of the seven at this input size.

## CLI flags

Dispatcher (`run_generator.sh`): no generator-specific flag beyond the
shared set documented in the repo [README.md](../../README.md).

Pipeline (`./src/sbm/pipeline.sh`, direct invocation):

- `--outlier-mode <excluded|singleton|combined>`: default `combined`.
- `--drop-outlier-outlier-edges` / `--keep-outlier-outlier-edges`: default keep.
- `--match-degree` / `--no-match-degree`: optional Stage-4 degree rewire. Default off.
- `--match-degree-algorithm <greedy|true_greedy|random_greedy|rewire|hybrid>`: default `hybrid`; only takes effect when match-degree is on.
- `--remap` / `--no-remap`: default off (SBM reuses reference node IDs).

See [../advanced-usage.md](../advanced-usage.md) for the naming
convention across dispatcher vs. pipeline layers and the cross-generator
default matrix.

## Where to look next

- [Source: `src/sbm/gen.py`](../../src/sbm/gen.py)
- [Source: `src/sbm/profile.py`](../../src/sbm/profile.py)
- [graph-tool's `generate_sbm` docs](https://graph-tool.skewed.de/static/doc/generation.html#graph_tool.generation.generate_sbm)
- [Interactive GUI: sbm steps at default settings](https://vltanh.me/netgen/sbm.html)
- [Index of all generators](../algorithms.md)
