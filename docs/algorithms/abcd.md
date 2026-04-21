# ABCD

[← back to index](../algorithms.md)

ABCD (Artificial Benchmark for Community Detection) is the current
community-detection benchmark of choice in a few labs. It accepts the
empirical degree sequence and cluster sizes as-is and enforces a global
mixing fraction ξ. If you care about "synthesize a graph whose degrees and
cluster sizes match this specific input", ABCD is a reasonable first choice.
If you care about block-edge-count matrices or triangle density, pick
something else.

## The model

A configuration-model hybrid:

1. Inside each cluster, run a configuration model on the internal
   half-edges.
2. Globally across clusters, run a configuration model on the external
   half-edges.
3. Rewire any self-loops or multi-edges.

The split between internal and external half-edges is controlled by one
scalar, ξ (xi), the fraction of each node's stubs that reach outside its
cluster. At ξ = 0, every edge is intra-cluster; at ξ = 1, every edge is
inter-cluster.

## Stage 1: three numbers

[`src/abcd/profile.py`](../../src/abcd/profile.py) extracts:

1. `degree.csv`: per-node integer degree.
2. `cluster_sizes.csv`: per-cluster integer size.
3. `mixing_parameter.txt`: the global ξ = Σ_out / Σ_total.

The global ξ is a stub-weighted average. A 1000-degree hub that spends 100
stubs crossing boundaries contributes the same amount to ξ as 100
low-degree nodes each crossing one boundary. This is ABCD's convention. It
is *different* from [LFR](./lfr.md)'s μ, which is the mean of per-node
ratios. On skewed graphs the two numbers can differ noticeably; use each
model's own convention, not the other's.

No edge-count matrix, no block assignment. ABCD's whole summary is three
small files.

## Stage 2: shell out to Julia

[`src/abcd/gen.py`](../../src/abcd/gen.py) does almost no algorithmic work.
It writes the profile CSVs to TSVs, then invokes:

```
julia <abcd_dir>/utils/graph_sampler.jl  \
    edge.tsv com.tsv deg.tsv cs.tsv      \
    xi <xi_value> false false <seed> 0
```

The two `false`s select ABCD variants we do not use:

- `is_CL = false`: use the plain config model, not the Chung-Lu variant.
- `is_local = false`: use the global ξ, not per-cluster μ_i.

The final `0` is `n_outliers`. [ABCD+o](./abcd+o.md) passes the actual
outlier count here.

After the Julia run, the wrapper reads back `edge.tsv` + `com.tsv`, drops
singleton clusters, and writes the standard `edge.csv` + `com.csv`.

## What the Julia sampler does

Canonical ABCD steps (from Kamiński, Prałat, Théberge 2020 and the sampler
source):

1. **Fix d and s** from the input TSVs. ABCD does not resample them.
2. **Assign nodes to clusters** respecting the constraint
   `d_i ≤ s_{C(i)} − 1`: a node cannot have more neighbours than its
   cluster has other members.
3. **Split each degree**: `d_i^ext = round(ξ · d_i)`,
   `d_i^int = d_i − d_i^ext`. Per-node.
4. **Internal edges**: configuration model per cluster on internal
   half-edges.
5. **External edges**: configuration model globally on external half-edges.
6. **Rewire collisions**: self-loops and duplicates are fixed by swapping
   endpoints.

Step 3 is the point. By forcing each node to have exactly round(ξ · d_i)
external stubs, ABCD makes the global ξ a property of the generative
process. The output's ξ cannot drift far unless rewiring shuffles things
significantly.

## What you get on the shipped example

Default run on dnc + sbm-flat-best+cc at `--seed 1`:

| Stat | Input | ABCD output | Note |
| --- | --- | --- | --- |
| N | 906 | 906 | exact |
| Edges | 10429 | 10150 | within 2.7% |
| Mean degree | 23.02 | 22.41 | very close |
| Global clustering coeff. | 0.548 | 0.307 | not targeted |
| Mean k-core | 15.99 | 13.44 | |
| Num clusters | 42 | 42 | exact |

The degrees and cluster sizes are exact by construction (before rewiring);
the small edge-count drift is the rewiring pass. The global ξ tracks the
target tightly.

## Output guarantees

| Property | Status |
| --- | --- |
| N | exact |
| Cluster sizes | exact (from `cs.tsv`) |
| Degree sequence | exact if no rewires fired; very close otherwise |
| Global ξ | in expectation, concentrates fast in N |
| Inter-cluster edge-count matrix | not targeted (only scalar ξ) |
| Clustering coefficient / triangle count | not targeted, empirically low |
| Outliers | none; see [ABCD+o](./abcd+o.md) |

If you need a specific inter-cluster pattern (e.g. cluster A connects more
to B than to C), use the [SBM family](./sbm.md). If you need triangle
density, [nPSO](./npso.md) is the only generator here that targets it.

## Determinism

Single source: `Random.seed!(parse(Int, ARGS[9]))` inside the Julia
sampler. Julia's `Dict` iteration order is insertion-ordered per the
language spec, so no Julia-side equivalent of `PYTHONHASHSEED` is needed.
We still export `PYTHONHASHSEED=0` because the Python profile stage
iterates sets and dicts.

No `seed=0` footgun here; Julia's PRNG handles 0 sensibly. The repo-wide
default is `--seed 1` regardless.

## Cost

10 seeds x 10 kept runs on 4 cores, 16 GiB cgroup cap:

- kept mean: 3.75 s
- kept std: 0.10 s

The Julia interpreter boots in 2-3 s every run. Batching many seeds in one
shell does not amortise startup the way it does with graph-tool.

## When to use

- **Yes**: you want a benchmark-style synthetic that uses your empirical
  degrees and cluster sizes directly, and you care about global ξ.
- **Maybe**: [ABCD+o](./abcd+o.md) if your graph has genuine outliers.
- **No**: [LFR](./lfr.md) if you want a power-law parametrisation rather
  than the raw empirical sequences.
- **No**: [SBM](./sbm.md) or [EC-SBM v2](./ec-sbm-v2.md) if you need the
  block-pair edge-count matrix preserved.

## Where to look next

- [Source: `src/abcd/gen.py`](../../src/abcd/gen.py)
- [Source: `src/abcd/profile.py`](../../src/abcd/profile.py)
- [Upstream: ABCDGraphGenerator.jl](https://github.com/bkamins/ABCDGraphGenerator.jl)
- [Interactive GUI: abcd steps at default settings](https://vltanh.me/netgen/abcd.html)
- [ABCD+o (outlier-enabled variant)](./abcd+o.md)
- [LFR (the incumbent benchmark)](./lfr.md)
- [Index of all generators](../algorithms.md)
