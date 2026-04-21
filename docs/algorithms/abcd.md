# ABCD: the benchmark that respects your data

[← back to index](../algorithms.md)

ABCD — Artificial Benchmark for Community Detection — is the community-detection field's current favourite benchmark, largely because it does something LFR doesn't: **it accepts your degree sequence and cluster sizes as-is, no power-law fit required**. If you care about "synthesize something like this specific graph", ABCD is a reasonable first choice. If you care about "reproduce every statistic exactly", keep reading and pick differently.

## The two-sentence model

ABCD is a configuration-model hybrid. **Inside each cluster, run a configuration model on internal half-edges. Globally across clusters, run a configuration model on external half-edges. Then rewire any self-loops or multi-edges.** The split between internal and external half-edges is controlled by one scalar, ξ (xi), which is the fraction of each node's stubs that reach outside its cluster.

## Stage 1: pull three numbers out of your graph

[`src/abcd/profile.py`](../../src/abcd/profile.py) extracts:

1. `degree.csv` — per-node integer degree.
2. `cluster_sizes.csv` — per-cluster integer size.
3. `mixing_parameter.txt` — the global ξ = Σ_out / Σ_total.

That's it. No edge-count matrix, no block assignment — just three small pieces of summary statistics.

The global ξ is computed as the ratio of total out-edges (edges crossing cluster boundaries) to total edges, counted over half-edges. Every node's half-edges count equally in both numerator and denominator, which means hubs dominate — a 1000-degree node crossing 100 boundaries contributes the same to ξ as 100 low-degree nodes each crossing 1.

(This is the main numerical difference vs [LFR](./lfr.md), which uses the *mean* of per-node μ_i. On skewed graphs the two numbers can differ noticeably. The two models have different conventions, so each uses its own.)

## Stage 2: shell out to Julia

The Python wrapper in [`src/abcd/gen.py`](../../src/abcd/gen.py) does almost no algorithmic work. It writes the profile CSVs to TSVs, then invokes:

```
julia <abcd_dir>/utils/graph_sampler.jl  \
    edge.tsv com.tsv deg.tsv cs.tsv     \
    xi <xi_value> false false <seed> 0
```

The two `false`s are flags for two variants we don't use:

- `is_CL = false`: don't use the Chung-Lu variant, use plain config model.
- `is_local = false`: don't use per-cluster μ_i, use the single global ξ.

The final `0` is `n_outliers`. Base ABCD has no outlier concept; [ABCD+o](./abcd+o.md) passes the actual outlier count here.

After the Julia run, the wrapper reads back `edge.tsv` + `com.tsv`, strips singleton clusters, and writes the standard `edge.csv` + `com.csv`.

## What the Julia sampler actually does

(Summarised from the paper and the sampler's source.)

1. **Fix d and s.** The degree sequence and cluster-size list come in via TSV. ABCD does *not* resample them — these are exact, by construction.
2. **Assign nodes to clusters** respecting the constraint `d_i ≤ s_{C(i)} − 1`: a node can't have more neighbours than its cluster has other members, if all its edges were internal.
3. **Split each degree**: `d_i^ext = round(ξ · d_i)` and `d_i^int = d_i − d_i^ext`. Per-node. The global external fraction is therefore ξ in expectation.
4. **Internal edges**: configuration model *per cluster* on internal half-edges.
5. **External edges**: configuration model *globally* on external half-edges.
6. **Rewire collisions**: self-loops and duplicates are fixed by swapping endpoints.

Step 3 is worth dwelling on. By forcing each node to have *exactly* `round(ξ · d_i)` external edges, ABCD makes ξ a property of the generative process, not just a target. You can't drift far from ξ unless rewiring happens to shuffle things significantly.

## What you get

| Thing | Status |
| --- | --- |
| Node count N | Exact |
| Cluster sizes | Exact (from cs.tsv) |
| Degree sequence | Exact if no rewires fired; otherwise close |
| Global ξ | In expectation, concentrates fast in N |
| Inter-cluster edge-count matrix | Not targeted — only scalar ξ |
| Clustering coefficient / triangles | Not targeted (empirically low) |
| Outliers | None — see [ABCD+o](./abcd+o.md) |

The "not targeted" rows are ABCD's known weaknesses. If you need triangle density, [nPSO](./npso.md) is the only generator here that targets it. If you need a specific inter-cluster pattern (e.g., "cluster A connects more to B than to C"), use the [SBM family](./sbm.md).

## Determinism

Single-source: `Random.seed!(parse(Int, ARGS[9]))` inside the Julia sampler. Julia's `Dict` iteration order is insertion-ordered per the language spec, so no equivalent of `PYTHONHASHSEED` is needed Julia-side. We still export `PYTHONHASHSEED=0` because the Python profile stage iterates sets and dicts.

No `seed=0` footgun here — Julia's PRNG handles 0 sensibly. But the repo-wide default is still `--seed 1`.

## Cost

On the dnc example:

- Kept mean: ~6.5 s
- Cold: ~6.4 s

No cold premium worth worrying about; `julia` boots in 2-3 s every run. Batching many seeds in one shell doesn't amortise startup the way it does with graph-tool.

## When to use

- **Yes, ABCD**: you want a community-detection benchmark that uses your empirical degrees and cluster sizes directly, and you care about global ξ.
- **Maybe, [ABCD+o](./abcd+o.md)**: same, but your graph has genuine outliers.
- **No, [LFR](./lfr.md)**: you specifically want a power-law parametrisation (alpha-exponents, not the raw sequence).
- **No, [SBM](./sbm.md) / [EC-SBM](./ec-sbm-v2.md)**: you need the block-pair edge-count matrix preserved, not just aggregate mixing.

## Where to look next

- [Source: `src/abcd/gen.py`](../../src/abcd/gen.py)
- [Source: `src/abcd/profile.py`](../../src/abcd/profile.py)
- [Upstream: ABCDGraphGenerator.jl](https://github.com/bkamins/ABCDGraphGenerator.jl)
- [ABCD+o (outlier-enabled variant)](./abcd+o.md)
- [LFR (the incumbent benchmark)](./lfr.md)
- [Index of all generators](../algorithms.md)
