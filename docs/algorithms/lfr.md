# LFR

[← back to index](../algorithms.md)

Know which question you are asking before you pick LFR.

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
| Num clusters | 87 | 10 | resampled from the fitted cluster-size power law |

Notice the cluster count difference: LFR resampled the cluster-size
distribution, so the number of clusters is not preserved. The input has
87 clusters, the output has 10.

## Output guarantees

- **N** exact (CLI arg).
- **Mean degree at k, max degree at maxk** (power-law truncation).
- **Cluster sizes ≥ `minc`**. The `maxc` upper bound is a soft target:
  on inputs whose `cs` distribution has a poor power-law fit (few unique
  cluster sizes), the C++ binary's internal `-maxc` enforcement drifts.
  Empirically on dnc, `-maxc 52` produced output cluster sizes spanning
  67-116.
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

Cold cost is slightly higher than steady-state because `powerlaw.Fit`
imports scipy on first call. Concrete numbers live in
`examples/benchmark/summary.csv`, refreshed by
[`tools/benchmark/bench_isolated.sh`](../../tools/benchmark/bench_isolated.sh).

## CLI flags

Dispatcher (`run_generator.sh`):

- `--lfr-binary <p>`: path to compiled LFR benchmark executable. Default `externals/lfr/unweighted_undirected/benchmark`.

LFR is single-threaded; `--n-threads` is ignored.

Pipeline (`./src/lfr/pipeline.sh`):

- `--binary <p>`: same role, short form at pipeline layer.
- `--outlier-mode <excluded|singleton|combined>`: default `singleton`.
- `--drop-outlier-outlier-edges` / `--keep-outlier-outlier-edges`: default keep.
- `--match-degree` / `--no-match-degree`: default off.
- `--match-degree-algorithm <greedy|true_greedy|random_greedy|rewire|hybrid>`: default `hybrid`.
- `--remap` / `--no-remap`: default on (C++ binary emits fresh 1..N IDs).

See [../advanced-usage.md](../advanced-usage.md).

## Where to look next

- [Source: `src/lfr/gen.py`](../../src/lfr/gen.py)
- [Source: `src/lfr/profile.py`](../../src/lfr/profile.py)
- [Upstream: Lancichinetti, Fortunato, Radicchi 2008](https://arxiv.org/abs/0805.4770)
- [Interactive GUI: lfr steps at default settings](https://vltanh.me/netgen/lfr.html)
- [ABCD (empirical-sequence variant)](./abcd.md)
- [Index of all generators](../algorithms.md)
