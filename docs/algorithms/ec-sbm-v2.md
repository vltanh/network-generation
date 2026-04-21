# EC-SBM v2

[← back to index](../algorithms.md)

v2 is the cleanup of [EC-SBM v1](./ec-sbm-v1.md). The pipeline shape is the
same (four stages, same names, same K_{k+1} constructive core), but the
bookkeeping is more principled, the outlier handling is unified, and the
degree-matching stage exposes a menu of algorithms instead of one silent
greedy.

## What changes from v1

**1. Stage 2 is constructive-only.** v1's `gen_clustered` built the
K_{k+1} cores *and* called `gt.generate_sbm` on a mutated probs matrix. v2
skips the SBM call here and writes only the constructive edges. The SBM
sampling is deferred to stage 3a.

**2. Stage 3 is a residual SBM over all blocks.** v1's `gen_outlier` ran a
second, outlier-only SBM. v2's `gen_outlier` is the *main* SBM call of the
pipeline: it sees the full block structure plus outliers as extra blocks,
subtracts whatever stage 2 already produced, and samples what's left. The
file is still called `gen_outlier.py`, but it fills all residual edges, not
just outlier-touching ones.

**3. Stage 4 has five matchers.** v1 had one heap-greedy that silently
dropped stuck stubs. v2 exposes five algorithms via `--algorithm`
(`greedy`, `true_greedy`, `random_greedy`, `rewire`, `hybrid`). Most log
gridlock instead of hiding it. The registry default in
[`generators/ec-sbm-v2.sh`](../../generators/ec-sbm-v2.sh) is `true_greedy`,
though `hybrid` is usually the more robust choice.

## The K_{k+1} core

Phase 1 and phase 2 of `generate_cluster` are algorithmically identical to
v1. Sort cluster nodes by degree desc. Top k+1 form a complete subgraph.
Each remaining node attaches to up to k partners, top-of-processed first
and degree-weighted-random as fallback. Same `ensure_edge_capacity` rule:
if the budget would block a required edge, inflate the budget.

The difference is that v2's stage 2 ends by writing the constructive edges
to `edge.csv` and stopping. No SBM overlay, no dedup.

## Residual SBM (stage 3a)

This is where v2's accounting is cleaner. [`prepare_residual_sbm_inputs`](../../src/ec-sbm/v2/gen_outlier.py)
computes, per block k:

```
D_k  = sum of target degrees in block k, minus stage-2 contributions, clamped at 0
E_inter_k = row-sum of probs for row k (inter-block half-edges k owes)
diff = D_k - E_inter_k
```

Then it sets `probs[k, k] = diff` (the intra-block edges the SBM still
needs to place). If `diff` is negative, stage 2 already pushed more
intra-k half-edges than the block should have, so we add `|diff|` back to
`out_degs` and set `probs[k, k] = 0` (the SBM cannot take back what is
already there; it can only add). If `diff` is odd, bump by 1 and add a
matching stub so graph-tool's even-half-edges-per-block constraint holds.

Outliers fold into the block matrix under `--gen-outlier-mode` (default
`combined`, same as profile's). `combined` puts all outliers in one extra
block; `singleton` gives each its own. `excluded` is rejected at entry.

Then:

```python
g = gt.generate_sbm(b, probs, out_degs, micro_ers=True, micro_degs=True)
```

## Block-preserving rewire

graph-tool's micro-SBM still emits self-loops and multi-edges. v1 called
`remove_parallel_edges + remove_self_loops` and accepted the degree loss.
v2's default is `--edge-correction rewire`, which does a 2-opt swap that
keeps each node in its block:

1. Bucket every valid edge by its block-pair `(A, B)`, where
   `A = min(b_u, b_v)`, `B = max(b_u, b_v)`.
2. For each invalid edge (self-loop or duplicate), pick a random valid
   edge from the same block-pair bucket.
3. Swap endpoints so each node stays in its home block. Inter-block
   (A != B) uniquely determines the swap; intra-block (A == B) flips a
   coin between two valid swaps.
4. Accept only if both new edges are valid; otherwise requeue.

Up to 10 retry passes with a stagnation detector that early-breaks when the
queue stops shrinking. Unresolved edges are dropped with a `WARN`.

`--edge-correction drop` skips the rewire and just dedups. Faster, loses
more degrees. Keep `rewire` as default unless you are benchmarking.

## The matcher menu (stage 4a)

After stage 3 combines clustered + residual, some nodes still have
residual stubs. Algorithms:

- **`greedy`**: same as v1's heap-based greedy. Pop max-degree u, connect
  to `min(residual, non-neighbors)` partners via `set.pop()`. Silent
  gridlock.
- **`true_greedy`**: max-heap with dynamic re-push. Pop u, pick
  v = argmax over `current_degrees` among valid targets, push updated
  residuals back. Logs gridlock.
- **`random_greedy`**: weighted-random u (by residual), weighted-random v
  from valid targets. Useful for comparing bias vs deterministic greedy.
- **`rewire`**: configuration-model pairing + 2-opt repair. Build a flat
  stub list (each node repeated `residual` times), shuffle, pair adjacent.
  Invalid pairs queue for the same `run_rewire_attempts` used by
  gen_outlier.
- **`hybrid`**: run `rewire` first, then `true_greedy` on whatever rewire
  could not place. Rewire handles the bulk unbiased, greedy handles the
  stuck tail deterministically.

## Why there is one SBM, not two

If you've read the [v1 post](./ec-sbm-v1.md), v1 runs two separate SBM
samplers: one for the clustered phase, one for outliers. v2 collapses
these into one.

Two reasons.

**Singleton outlier clusters do nothing.** A cluster of size 1 has no
internal edges (no partner), so the constructive phase is a no-op, the
K_{k+1} core is empty, and phase 2 has nothing to process. Assigning each
outlier to its own block is algorithmically equivalent to folding them all
into one block: same subgraph either way. The real design question is
whether we want outliers to have community structure at all, or to be
uniform background.

v2's answer: fold outliers into one combined block (`--gen-outlier-mode
combined`). Clean accounting, one SBM call.

**One SBM is simpler than two.** With outliers in a single combined block,
the residual SBM over all blocks (real clusters + one outlier block)
subsumes what v1 needed two SBM calls to express. Fewer SBM calls means
less double-sampling drift and faster runtime.

## What you get on the shipped example

Default run on dnc + sbm-flat-best+cc at `--seed 1` with the pipeline's
`--edge-correction rewire --algorithm true_greedy`:

| Stat | Input | v2 output | Note |
| --- | --- | --- | --- |
| N | 906 | 906 | exact |
| Edges | 10429 | 10346 | within 0.8% |
| Mean degree | 23.02 | 23.03 | |
| Global clustering coeff. | 0.548 | 0.513 | highest of the SBM family here |
| Mean k-core | 15.99 | 14.74 | |

## Output guarantees

- **N** exact after the outlier transform.
- **k-edge-connectivity at least k(C) per cluster** by construction.
- **Block structure** exact.
- **Degree sequence** targeted; tracks more tightly than v1 because of
  the residual accounting. `hybrid` minimizes drop.
- **Inter-cluster edge counts** closer to the profile than v1 because
  stage 3a builds probs from the original edges minus stage-2
  contributions.
- **Clustering coefficient** not targeted; the K_{k+1} cores push it up
  above plain SBM.

## Determinism

Three RNGs (`random`, `numpy`, `graph-tool`) seeded per stage with
offsets `seed` / `seed+1` / `seed+2`. `PYTHONHASHSEED=0` is load-bearing
for `greedy`'s `set.pop()`, `valid_pool[bp]` iteration in the rewire
loop, and `current_degrees` dict iteration in `true_greedy`'s
`valid_targets` list comprehension. Same `--seed 0` trap as plain SBM.

## Cost

10 seeds x 10 kept runs on 4 cores, 16 GiB cgroup cap:

- kept mean: 2.39 s
- kept std: 0.10 s

Faster than v1 (2.83 s) because v1 runs two `gt.generate_sbm` calls and
v2 runs one.

## Provenance bands

Every `OUTPUT_DIR/edge.csv` carries a `sources.json` that maps three
provenance labels to inclusive 1-based row ranges:

```json
{
  "clustered":    [1, 42],
  "outlier":      [43, 100],
  "match_degree": [101, 120]
}
```

Stage 2 wrote rows 1-42, stage 3a's residual SBM wrote 43-100, stage 4a's
matcher added 101-120. Colour edges by provenance to see what each stage
placed.

## Where to look next

- [Source: `src/ec-sbm/v2/gen_clustered.py`](../../src/ec-sbm/v2/gen_clustered.py)
- [Source: `src/ec-sbm/v2/gen_outlier.py`](../../src/ec-sbm/v2/gen_outlier.py)
- [Source: `src/match_degree.py`](../../src/match_degree.py)
- [Source: `src/ec-sbm/common/profile.py`](../../src/ec-sbm/common/profile.py)
- [Interactive GUI: ec-sbm-v2 steps at default settings](./ec-sbm-v2.html)
- [EC-SBM v1 post](./ec-sbm-v1.md)
- [Plain SBM post](./sbm.md)
- [Index of all generators](../algorithms.md)
