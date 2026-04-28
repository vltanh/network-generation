# SBM

[← back to index](../algorithms.md)

## CLI flags

Dispatcher (`run_generator.sh`): no generator-specific flag beyond the
shared set documented in the repo [README.md](../../README.md).

Pipeline ([`src/sbm/pipeline.sh`](../../src/sbm/pipeline.sh), direct
invocation):

| Flag | Default | Effect |
| --- | --- | --- |
| `--outlier-mode <excluded\|singleton\|combined>` | `combined` | how `profile.py` folds outliers (drop / one cluster each / single `__outliers__` block) |
| `--drop-outlier-outlier-edges` / `--keep-outlier-outlier-edges` | keep | strip OO edges from the input edgelist before profiling |
| `--match-degree` / `--no-match-degree` | off | optional Stage-4 degree rewire pass |
| `--match-degree-algorithm <greedy\|true_greedy\|random_greedy\|rewire\|hybrid>` | `hybrid` | only effective with `--match-degree` |
| `--remap` / `--no-remap` | off | remap output node IDs (SBM passes input IDs through by default) |

See [../advanced-usage.md](../advanced-usage.md) for the
dispatcher-namespaced vs pipeline-short flag naming convention and the
cross-generator default matrix.

## Stage 1: the profile

Entrypoint: `setup_inputs(edgelist_path, clustering_path, output_dir, …)`
at [`src/sbm/profile.py:33`](../../src/sbm/profile.py#L33). Reads the
edgelist + reference clustering, folds outliers per `--outlier-mode`
(default `combined` ⇒ one `__outliers__` pseudo-cluster), and writes
five CSVs to `output_dir`.

| File | What it is |
| --- | --- |
| `node_id.csv` | Node IDs in degree-descending order (ties by ID asc) |
| `cluster_id.csv` | Cluster IDs in size-descending order |
| `assignment.csv` | Per-node cluster idx (index into `cluster_id.csv`) |
| `degree.csv` | Per-node integer degree |
| `edge_counts.csv` | `(row, col, weight)` triples for the block matrix |

Edge-count matrix construction walks each edge twice (once per
direction). Off-diagonal `probs[r, s]` = count of edges between blocks
`r` and `s`; diagonal `probs[k, k]` = twice the count of intra-block
edges in block `k`. The doubling is graph-tool's undirected convention
(each intra-block edge is two half-edges, both landing in block `k`).

## Stage 2: one call, two cleanups

Entrypoint: `run_sbm_generation(...)` at
[`src/sbm/gen.py:28`](../../src/sbm/gen.py#L28). Body:

```python
np.random.seed(seed)
gt.seed_rng(seed)
gt.openmp_set_num_threads(n_threads)

g = gt.generate_sbm(
    assignments, probs, out_degs=degrees,
    micro_ers=True, micro_degs=True, directed=False,
)
edges = [(node_ids[int(s)], node_ids[int(t)]) for s, t in g.iter_edges()]
edge_df = simplify_edges(pd.DataFrame(edges, columns=["source", "target"]))
```

`pipeline_common.simplify_edges` drops self-loops and collapses parallel
edges. graph-tool ships its own `remove_parallel_edges` /
`remove_self_loops` that would do the same job; one shared simplifier
across every generator (`sbm`, `ec-sbm`, `abcd`, `lfr`, `npso`) keeps
the on-disk `edge.csv` contract identical regardless of upstream
toolchain.

The micro-SBM sampler is allowed to emit multigraphs. It hits the count
constraints as counts, not as distinct edges. Small block + high
intra-block count ⇒ self-loops. Two hubs holding up a large
`probs[r, s]` ⇒ parallel edges. Both are simplified away.

### What the kernel actually does

[`graph-tool/src/graph/generation/graph_sbm.hh`](../../graph-tool/src/graph/generation/graph_sbm.hh)
implements the micro-canonical sampler. The kernel builds one urn per
block, populated by every node `v` with `b_v = r` repeated `k_v` times.
Stubs in the urn are not typed by target block. For each `(r, s)` pair
with `r <= s` in row-major order, the kernel pulls `e_{rs}` half-edges
out of urn `r` (and another `e_{rs}` from urn `s` when `r != s`)
without replacement, pairing them up into edges. So the per-pair edge
count and the per-node degree land exactly on the input; what
fluctuates is which specific stubs of `v` end up in which `(r, s)`
cell, i.e. v's per-block-degree profile is hit in expectation only.

The only consistency check is per-pair: `urn_r.size() >= e_{rs}` and
the same on the `s` side. If they fail, graph-tool throws
`"Inconsistent SBM parameters: node degrees do not agree with matrix
of edge counts between groups"`. A consistent profile (sum of degrees
in block `r` = row sum of `e_{r,*}`) drains every urn to zero.

## What you get on the shipped example

Default run on the
[`dnc`](../../examples/input/empirical_networks/networks/dnc/) input
with [`sbm-flat-best+cc`](../../examples/input/reference_clusterings/clusterings/sbm-flat-best+cc/dnc/)
clustering at `--seed 1`:

| Stat | Input | SBM output | Note |
| --- | --- | --- | --- |
| N | 906 | 902 | 4 nodes isolated after dedup |
| Edges | 10429 | 7438 | 29% lost to multi-edge / self-loop removal |
| Mean degree | 23.02 | 16.49 | tracks the edge loss |
| Global clustering coeff. | 0.548 | 0.341 | DC-SBM tends tree-like |
| Mean local CC | 0.494 | 0.216 | same per-node |
| Num clusters | 87 | 87 | exact (passthrough minus singletons) |

29% edge loss = the dedup bill for this input: a highly clustered graph
(global ccoeff 0.548) compressed through a micro-SBM whose block matrix
was filled with duplicate-heavy cells.

## Output guarantees

| Property | Status |
| --- | --- |
| N | exact after the `combined` outlier transform |
| Block structure | exact (each node stays in its input block) |
| Degree sequence | exact pre-dedup; upper bound post-dedup |
| Inter-block counts `e_{rs}` | exact pre-dedup; upper bound post-dedup |
| Clustering coefficient / triangle count | not targeted |

## Determinism

Three RNGs seeded at the start of stage 2 (see
[`src/sbm/gen.py:33-35`](../../src/sbm/gen.py#L33-L35)):

```python
np.random.seed(seed)
gt.seed_rng(seed)
gt.openmp_set_num_threads(n_threads)
```

`PYTHONHASHSEED=0` is exported from
[`src/sbm/pipeline.sh`](../../src/sbm/pipeline.sh): load-bearing.
`gt.generate_sbm` is sensitive to Python set/dict iteration order in
some code paths; without a pinned hash seed, reruns at the same
`--seed` diverge.

`--seed 0` is a trap: graph-tool treats 0 as "use entropy source",
which disables reproducibility. Default everywhere in this repo is
`--seed 1`.

## Cost

SBM is the fastest of the seven generators at this input size. Concrete
numbers live in `examples/benchmark/summary.csv`, refreshed by
[`tools/benchmark/bench_isolated.sh`](../../tools/benchmark/bench_isolated.sh).

## Where to look next

- [`src/sbm/gen.py`](../../src/sbm/gen.py): generation entrypoint
- [`src/sbm/profile.py`](../../src/sbm/profile.py): profile entrypoint
- [`src/sbm/pipeline.sh`](../../src/sbm/pipeline.sh): dispatcher script
- [graph-tool's `generate_sbm` docs](https://graph-tool.skewed.de/static/doc/generation.html#graph_tool.generation.generate_sbm)
- [Interactive walkthrough: vltanh.me/netgen/sbm.html](https://vltanh.me/netgen/sbm.html)
- [Index of all generators](../algorithms.md)
