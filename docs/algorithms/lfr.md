# LFR: the benchmark that replaces your data with power laws

[← back to index](../algorithms.md)

LFR — Lancichinetti–Fortunato–Radicchi — is the oldest and still most-cited benchmark in community detection. If you've read a community-detection paper this century, you've seen it. Unlike [ABCD](./abcd.md), LFR doesn't take your degree sequence and cluster sizes as-is; it **fits power laws** to them and **resamples**. This makes LFR ideal for "give me a realistic graph with these broad stats" and terrible for "reproduce this specific graph."

Know which question you're asking before you pick LFR.

## What LFR wants

Eight numbers:

| Param | Meaning |
| --- | --- |
| N | Number of nodes |
| k | Mean degree |
| maxk | Max degree |
| minc | Min cluster size |
| maxc | Max cluster size |
| μ | Mean per-node mixing fraction |
| t1 | Power-law exponent for degree distribution |
| t2 | Power-law exponent for cluster-size distribution |

Stage 1 extracts three things from your input graph (degrees, cluster sizes, mean μ), and stage 2 computes the other five by aggregating and fitting.

## Stage 1: three numbers, a subtle mean

[`src/lfr/profile.py`](../../src/lfr/profile.py) emits `degree.csv`, `cluster_sizes.csv`, and `mixing_parameter.txt`. The mixing parameter is:

```
μ = mean_i [ out_i / (in_i + out_i) ]
```

where the mean is over nodes with nonzero total degree. This is *different* from ABCD's global ξ:

- **ABCD (ξ)**: weights nodes by stub count. A 1000-degree hub dominates.
- **LFR (μ)**: weights nodes equally. A 3-degree node and a 1000-degree hub count the same.

Each model has its own convention because each model *expects* its own convention. Don't mix them.

## Stage 2: fit, write a seed file, shell out

[`src/lfr/gen.py`](../../src/lfr/gen.py) does three interesting things before invoking the C++ binary.

**1. Fit two power laws with the `powerlaw` package:**

```python
t1 = float(powerlaw.Fit(degrees, discrete=True).power_law.alpha)
minc = max(int(np.min(cluster_sizes)), 3)     # LFR refuses minc < 3
t2 = float(powerlaw.Fit(cluster_sizes, discrete=True, xmin=minc).power_law.alpha)
```

`t1` is fit across all degrees. `t2` uses `xmin=minc` to anchor the fit to the LFR-imposed floor.

**2. Write a seed file** — `./time_seed.dat` in the output directory. The C++ binary was designed to read its seed from this file, not from a CLI arg:

```python
(output_dir / "time_seed.dat").write_text(f"{seed}\n")
```

**3. Invoke the binary with `cwd=output_dir`** so `time_seed.dat` is found:

```python
subprocess.run(
    [lfr_exec, "-N", str(N), "-k", str(k), ..., "-t2", str(t2)],
    cwd=output_dir,
)
```

After the C++ finishes, the wrapper reads back two files:

- `network.dat`: edge list, **each edge written twice** (once each direction — it's the C++ convention).
- `community.dat`: `node_id cluster_id` pairs.

It undirected-dedups the edges, drops singleton clusters, emits `edge.csv` + `com.csv`, unlinks the scratch files.

## What the C++ does

(Summarised from Lancichinetti et al. 2008.)

1. **Sample degree sequence** from a truncated power law with exponent `t1`, mean `k`, max `maxk`.
2. **Sample cluster sizes** from a truncated power law with exponent `t2`, bounds `[minc, maxc]`, summing to `N`.
3. **Assign nodes to clusters** respecting the constraint that a node's degree fits within its cluster size.
4. **Split each degree**: `d_i^ext = round(μ · d_i)`, `d_i^int = d_i − d_i^ext`.
5. **Internal edges**: configuration model per cluster.
6. **External edges**: configuration model globally.
7. **Rewire** self-loops and duplicates.

Notice step 1 and 2: the degrees and cluster sizes are **resampled** from the fitted power laws, not taken from our input. So LFR's output will *not* have your graph's degree sequence; it will have a fresh sample from the power law that approximately matches your graph's shape.

## What you get

- **N exact** (CLI arg).
- **Mean degree ≈ k, max degree ≈ maxk** (power-law truncation).
- **Cluster sizes ∈ [minc, maxc]**.
- **Degree distribution ~ power-law(t1)** in expectation.
- **Cluster-size distribution ~ power-law(t2)** in expectation.
- **Mean per-node μ ≈ target** in expectation.

## What you specifically don't get

- **Exact degree sequence.** If your input graph's degrees have a non-power-law tail (common in real graphs), the output tail is different.
- **Block structure.** The input clustering is not passed through. LFR generates a fresh clustering from its own power law. **`com.csv` is a generator output, not a passthrough of your input.** Node IDs in the output don't correspond to the node IDs in your input's clustering.

That last point is the single most important caveat when using LFR. If someone hands you the LFR-synthesized graph and asks "is this the same community as the input's cluster 7?", the answer is "no, because LFR doesn't know what cluster 7 is."

## Determinism

- Single seed source: C++ binary reads `./time_seed.dat`.
- `PYTHONHASHSEED=0` exported from `pipeline.sh:42` — defensive, for the Python profile stage.
- `N_THREADS=1` forced (binary isn't threaded).

## Cost

On the dnc example:

- Kept mean: ~4.0 s
- Cold: ~7.3 s

Cold premium comes from `powerlaw.Fit` which imports scipy on first call. Once warm, the C++ binary is the fastest stage-2 sampler here.

## When to use

- **Yes, LFR**: you want a benchmark-style synthetic graph with parametric control over degree and cluster-size distributions; you're reporting results that should be comparable to other LFR-using papers.
- **Maybe, [ABCD](./abcd.md) / [ABCD+o](./abcd+o.md)**: same goals but you want degree/size sequences preserved exactly (and you're OK with breaking from the LFR-tradition comparison).
- **No, [SBM](./sbm.md) / [EC-SBM v2](./ec-sbm-v2.md)**: you need your input's block structure and inter-block edge counts preserved. LFR won't do that.
- **No, [nPSO](./npso.md)**: you need high clustering coefficient.

## Where to look next

- [Source: `src/lfr/gen.py`](../../src/lfr/gen.py)
- [Source: `src/lfr/profile.py`](../../src/lfr/profile.py)
- [Upstream: Lancichinetti, Fortunato, Radicchi 2008](https://arxiv.org/abs/0805.4770)
- [ABCD (empirical-sequence variant)](./abcd.md)
- [Index of all generators](../algorithms.md)
