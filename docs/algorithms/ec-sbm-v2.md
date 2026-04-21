# EC-SBM v2: the cleanup

[← back to index](../algorithms.md)

v2 is what you get when you take [EC-SBM v1](./ec-sbm-v1.md), stare at its degree-matching behaviour for a while, and go "hmm, we can do better." The pipeline shape is the same — four stages, same stage names, same K_{k+1} constructive core — but the bookkeeping is cleaner, the outlier handling is unified, and there's a menu of degree-matching algorithms instead of one silent greedy.

## What's new

Three big things change in v2.

**1. Stage 2 is constructive-only.** v1's `gen_clustered` both built the K_{k+1} cores *and* called `gt.generate_sbm` on a mutated probs matrix. v2 skips the SBM call here and writes only the constructive edges. The SBM filling happens later, on a proper residual.

**2. Stage 3 is a residual SBM over all blocks.** v1's `gen_outlier` ran a second, outlier-only SBM. v2's `gen_outlier` is the *main* SBM call — it sees the original block structure plus outliers as extra blocks, subtracts whatever stage 2 already spent, and samples what's left. The file's still called `gen_outlier.py` but the name is now misleading: it fills everything residual, not just outlier-touching edges.

**3. Stage 4 has five matchers.** v1 had one heap greedy that silently dropped stuck stubs. v2 exposes five algorithms via `--algorithm`, most of which log their gridlock count. The default is `hybrid` (rewire + true_greedy fallback).

## The K_{k+1} core (still the star of the show)

Phase 1 and phase 2 of `generate_cluster` are algorithmically identical to v1. Sort cluster nodes by degree descending. Top k+1 form a complete subgraph (the mathematically guaranteed k-edge-connected core). Each remaining node attaches to up to k partners — top-of-processed first, degree-weighted-random as fallback.

The budget-inflation rule from v1 is still here:

```python
def ensure_edge_capacity(u, v):
    if probs[b_u, b_v] == 0 or int_deg[v] == 0:
        int_deg[u] += 1; int_deg[v] += 1
        probs[b_u, b_v] += 1; probs[b_v, b_u] += 1
```

The constructive phase never drops a required edge. It'll inflate degrees and block-pair budgets if necessary.

What v2 skips: the `gt.generate_sbm` overlay. Stage 2 ends by writing the constructive edges to `edge.csv` and stopping.

## The residual SBM (stage 3a)

This is where v2's accounting gets clean. [`gen_outlier.py`'s `prepare_residual_sbm_inputs`](../../src/ec-sbm/v2/gen_outlier.py) computes, per block `k`:

```
D_k  = sum of residual degrees of nodes in k
       (target degree - stage-2 contributions, clamped at 0)

E_inter_k = row-sum of probs for row k
            (number of inter-block half-edges k needs)

diff = D_k − E_inter_k
```

Then it sets `probs[k, k] = diff` — the intra-block edges the SBM still needs to place. If `diff` is negative (stage 2 already pushed more intra-block half-edges than the block should have), we push `|diff|` extra residual stubs to let the SBM place more edges and set `probs[k, k] = 0`. If `diff` is odd, we bump by 1 and add a matching stub to even the parity (graph-tool requires even half-edge totals per block).

Outliers get folded into the block matrix too, with behaviour controlled by `--gen-outlier-mode` (independent of profile's `--outlier-mode`):

- `combined` (default): all outliers → one extra block.
- `singleton`: each outlier → own block.

Then:

```python
g = gt.generate_sbm(b, probs, out_degs, micro_ers=True, micro_degs=True)
```

Seeds are `seed + 1` (stage 3 offset from the top-level `--seed`).

## Block-preserving rewire

graph-tool's micro-SBM can emit self-loops and parallel edges. v1 just called `remove_parallel_edges + remove_self_loops` and called it a day — which silently drops degree. v2 has a more careful default: `--edge-correction rewire`.

[`rewire_invalid_edges`](../../src/ec-sbm/v2/gen_outlier.py) is a 2-opt swap that preserves the block structure:

1. Bucket every valid edge by its block-pair `(A, B)` where `A = min(b_u, b_v)`, `B = max(b_u, b_v)`.
2. For each invalid edge (self-loop or duplicate), pick a random valid edge from the same block-pair bucket.
3. Swap endpoints so **each node stays in its home block**:
   - If `A ≠ B` (inter-block): the swap is determined — pair each block-A node with the other edge's block-B node.
   - If `A = B` (intra-block): there are two valid swaps; flip a coin.
4. Accept only if both new edges are valid. Otherwise requeue.

Up to 10 retry passes with a stagnation detector that early-breaks when queue size stops shrinking. Unresolved edges are dropped with a WARN.

The point: rewired edges don't change any node's block membership, and they don't (much) change the block-pair edge counts — so the SBM's constraints are preserved while the multi-edges go away.

`--edge-correction drop` skips the rewire and just dedups. Faster, loses more degrees. Keep `rewire` as default unless benchmarking.

## The matcher menu (stage 4a)

After stage 3 combines clustered + residual, some nodes still have residual stubs. The five algorithms, in rough order of sophistication:

**`greedy`** — identical to v1's heap-based greedy. Pop max-degree `u`, connect to `min(residual, |non-neighbors|)` partners via `set.pop()`. Stuck stubs dropped silently.

**`true_greedy`** — max-heap with dynamic re-push. Pop `u`, pick `v = argmax(current_degrees)` over valid targets, push updated residuals back. Gridlock is logged, not silent.

**`random_greedy`** — weighted-random `u` (by residual), weighted-random `v` from valid targets. Useful for inspecting bias differences against `true_greedy`.

**`rewire`** — configuration model + 2-opt repair. Build a stub list (each node repeated `residual` times), shuffle, pair adjacent. Invalid pairs (self-loops, duplicates, already-adjacent) queue for 2-opt rewire against the valid pool. Same retry utility as the gen_outlier rewire.

**`hybrid`** (v2 default) — run `rewire` first, then `true_greedy` on whatever rewire couldn't place. Most robust choice in practice: rewire handles the bulk unbiased, greedy handles the stuck tail deterministically.

## Why there's one SBM, not two

If you've read the [v1 post](./ec-sbm-v1.md), you know it runs two separate SBM samplers: one for the clustered phase, one for outliers. v2 collapses these into one. The reasoning is worth spelling out.

**Singleton-outlier clusters don't do anything useful.** A cluster of size 1 has no internal edges (there's no one to connect to), so the constructive phase is a no-op, the K_{k+1} core is empty, and phase-2 has nothing to process. Assigning each outlier to its own block is algorithmically equivalent to folding them all into a single block — same subgraph either way. The real design question is: do we want outliers to *have* community structure, or not?

v2's answer: **fold outliers into one combined block** (the `--gen-outlier-mode combined` default). This strips them of any meaningful community membership and lets them participate in the global connectivity pattern through the SBM's inter-block edges. One block, one SBM call, clean accounting.

## What you get

- **N** exact after the outlier transform.
- **Per-cluster edge-connectivity ≥ k(C)** by construction.
- **Block structure** exact.
- **Degree sequence** targeted; much tighter than v1 because of residual accounting. `hybrid` matcher minimizes drop.
- **Inter-cluster edge counts** track the profile closely, modulo rewire's small perturbations.
- **Clustering coefficient**: not targeted, but the K_{k+1} cores push it up somewhat vs plain SBM.

## Determinism

Three RNGs seeded per stage with offsets `seed` / `seed+1` / `seed+2`. `PYTHONHASHSEED=0` exported by `pipeline.sh:45`. Load-bearing for:

- `greedy`'s `set.pop()` (as in v1).
- `valid_pool[bp]` iteration inside the rewire loop.
- `current_degrees` dict iteration in `true_greedy`'s `valid_targets` list comprehension.

Same `--seed 0` footgun as elsewhere; default is `--seed 1`.

## Cost

On the dnc example, single-threaded, hybrid + rewire:

- Kept mean: ~8.1 s
- Cold: ~8.5 s
- Std: ~1 s (dominated by NFS noise on our shared host — per-seed medians fall in a 7.5-8.8 s band)

Faster than v1 on average because v1 does *two* `gt.generate_sbm` calls (stage 2 overlay + stage 3 outlier) while v2 does only one (stage 3 residual).

## Provenance bands

Every `OUTPUT_DIR/edge.csv` comes with a `sources.json` that maps three provenance labels to inclusive 1-based row ranges:

```json
{
  "clustered":    [1, 42],
  "outlier":      [43, 100],
  "match_degree": [101, 120]
}
```

Stage 2 wrote rows 1-42; stage 3a's residual SBM wrote 43-100; stage 4a's matcher added 101-120. This is the main hook for visualization: color edges by provenance to show *which stage placed each edge*.

## Where to look next

- [Source: `src/ec-sbm/v2/gen_clustered.py`](../../src/ec-sbm/v2/gen_clustered.py)
- [Source: `src/ec-sbm/v2/gen_outlier.py`](../../src/ec-sbm/v2/gen_outlier.py)
- [Source: `src/ec-sbm/v2/match_degree.py`](../../src/ec-sbm/v2/match_degree.py)
- [Source: `src/ec-sbm/v2/utils.py`](../../src/ec-sbm/v2/utils.py)
- [Source: `src/ec-sbm/common/profile.py`](../../src/ec-sbm/common/profile.py)
- [EC-SBM v1 post (the predecessor)](./ec-sbm-v1.md)
- [Plain SBM post](./sbm.md)
- [Index of all generators](../algorithms.md)
