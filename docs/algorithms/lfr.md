# LFR

[← back to index](../algorithms.md)

LFR (Lancichinetti-Fortunato-Radicchi) is the oldest and most-cited
benchmark in community detection. Unlike [ABCD](./abcd.md), LFR does not
take your degree sequence and cluster sizes as-is; it fits power laws to
them and resamples. That makes LFR good for "produce a realistic graph
with these broad stats" and the wrong choice for "reproduce this specific
graph".

Know which question you are asking before you pick LFR.

## What LFR wants

Eight numbers, fed to the C++ binary:

| Param | Meaning |
| --- | --- |
| N | Number of nodes |
| k | Mean degree |
| maxk | Max degree |
| minc | Min cluster size (floored at 3) |
| maxc | Max cluster size |
| μ | Mean per-node mixing fraction |
| t1 | Power-law exponent for degree distribution |
| t2 | Power-law exponent for cluster-size distribution |

Stage 1 extracts three things (degrees, cluster sizes, mean μ) and stage 2
computes the other five by aggregating and fitting.

## Stage 1: three numbers, a subtle mean

[`src/lfr/profile.py`](../../src/lfr/profile.py) writes `degree.csv`,
`cluster_sizes.csv`, and `mixing_parameter.txt`. The mixing parameter is:

```
μ = mean_i [ out_i / (in_i + out_i) ]
```

where the mean is over nodes with non-zero total degree.

This is different from ABCD's global ξ. ABCD (ξ) weights nodes by stub
count: a 1000-degree hub dominates. LFR (μ) weights nodes equally: a
3-degree node counts the same as a 1000-degree hub. Each model expects its
own convention; do not mix them.

## Stage 2: fit, write a seed file, shell out

[`src/lfr/gen.py`](../../src/lfr/gen.py) does three things before invoking
the C++ binary.

**1. Fit two power laws** with the `powerlaw` package:

```python
t1 = float(powerlaw.Fit(degrees, discrete=True).power_law.alpha)
minc = max(int(np.min(cluster_sizes)), 3)     # LFR requires minc >= 3
t2 = float(powerlaw.Fit(cluster_sizes, discrete=True, xmin=minc).power_law.alpha)
```

`t1` is fit across all degrees (no `xmin`; `powerlaw` picks its own via
KS). `t2` uses `xmin=minc` so the fit starts at the LFR-imposed floor of 3.

**2. Write a seed file.** The C++ binary reads its seed from
`./time_seed.dat` in the current working directory, not from a CLI
argument. So the wrapper writes:

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

- `network.dat`: edgelist, each edge written twice (once per direction;
  the C++ binary's convention).
- `community.dat`: `node_id cluster_id` pairs.

It undirected-dedups the edges (via `simplify_edges`), drops singleton
clusters, writes `edge.csv` + `com.csv`, and unlinks the scratch files.

## What the C++ does

Canonical LFR steps (from Lancichinetti, Fortunato, Radicchi 2008):

1. **Sample degree sequence** from a truncated power law with exponent
   t1, mean k, max maxk.
2. **Sample cluster sizes** from a truncated power law with exponent t2,
   bounds `[minc, maxc]`, summing to N.
3. **Assign nodes to clusters** respecting the constraint that a node's
   degree fits within its cluster size.
4. **Split each degree**: `d_i^ext = round(μ · d_i)`,
   `d_i^int = d_i − d_i^ext`.
5. **Internal edges**: configuration model per cluster.
6. **External edges**: configuration model globally.
7. **Rewire** self-loops and duplicates.

Steps 1 and 2 are the key point. The degrees and cluster sizes are
resampled from the fitted power laws, not taken from our input. LFR's
output will not have your graph's degree sequence; it will have a fresh
sample from the power law that approximately matches your graph's shape.

## What you get on the shipped example

Default run on dnc + sbm-flat-best+cc at `--seed 1`:

| Stat | Input | LFR output | Note |
| --- | --- | --- | --- |
| N | 906 | 906 | exact (CLI arg) |
| Edges | 10429 | 10370 | within 0.6% |
| Mean degree | 23.02 | 22.89 | tracks k argument |
| Global clustering coeff. | 0.548 | 0.252 | not targeted |
| Local clustering coeff. | 0.494 | 0.732 | |
| Num clusters | 42 | 51 | resampled from the fitted cluster-size power law |

Notice the cluster count difference: LFR resampled the cluster-size
distribution, so the number of clusters is not preserved. The input has
42 clusters, the output has 51.

## Output guarantees

- **N** exact (CLI arg).
- **Mean degree at k, max degree at maxk** (power-law truncation).
- **Cluster sizes in `[minc, maxc]`**.
- **Degree distribution at power-law(t1)** in expectation.
- **Cluster-size distribution at power-law(t2)** in expectation.
- **Mean per-node μ at target** in expectation.

## What you do not get

- **Exact degree sequence.** If the input's degrees have a non-power-law
  tail (common in real graphs), the output tail is different.
- **Block structure.** LFR generates a fresh clustering from its own
  power law. `com.csv` is a generator output, not a passthrough of the
  input. Node IDs in the output do not correspond to the node IDs in
  the input's clustering.

The second point is the big one. If someone hands you the LFR-synthesized
graph and asks "is this the same community as the input's cluster 7?",
the answer is "no, because LFR does not know what cluster 7 is".

## Determinism

- Single seed source: C++ binary reads `./time_seed.dat`.
- `PYTHONHASHSEED=0` exported by
  [`pipeline.sh`](../../src/lfr/pipeline.sh). Defensive; affects only
  the Python profile stage's set/dict iteration.
- `N_THREADS=1` forced at the pipeline level; the binary is not threaded.

## Cost

10 seeds x 10 kept runs on 4 cores, 16 GiB cgroup cap:

- kept mean: 1.77 s
- kept std: 0.05 s

Cold cost (not shown in the steady-state) is slightly higher because
`powerlaw.Fit` imports scipy on first call.

## When to use

- **Yes**: you want a benchmark-style synthetic with parametric control
  over degree and cluster-size distributions and you are reporting
  results comparable to other LFR-using papers.
- **Maybe**: [ABCD](./abcd.md) or [ABCD+o](./abcd+o.md) if you want
  degree/size sequences preserved exactly (and you are OK with breaking
  from LFR-tradition comparison).
- **No**: [SBM](./sbm.md) or [EC-SBM v2](./ec-sbm-v2.md) if you need the
  input's block structure and inter-block edge counts preserved.
- **No**: [nPSO](./npso.md) if you need high clustering coefficient.

## Where to look next

- [Source: `src/lfr/gen.py`](../../src/lfr/gen.py)
- [Source: `src/lfr/profile.py`](../../src/lfr/profile.py)
- [Upstream: Lancichinetti, Fortunato, Radicchi 2008](https://arxiv.org/abs/0805.4770)
- [Interactive GUI: lfr steps at default settings](./lfr.html)
- [ABCD (empirical-sequence variant)](./abcd.md)
- [Index of all generators](../algorithms.md)
