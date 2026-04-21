# Network generators: what they preserve, what they don't

This repo wraps seven synthetic-network generators under a single two-stage
pipeline. Stage 1 (`profile.py`) reads a real network plus a reference
clustering and extracts a small statistical profile. Stage 2 (`gen.py`)
consumes that profile and samples a synthetic network from a parametric
model. The two stages are intentionally decoupled: the same profile can be
consumed by different generators, and the same generator can be pointed at
different profiles.

The question this post answers: **given an empirical network G, what does
each generator's output G' guarantee to look like?** We use "guarantee"
loosely. Some are exact matches (number of nodes, block structure), some
are distributional (degree distribution in expectation), and some are only
*targeted* (clustering coefficient via a bisection search that may stop
short of the target). The table at the end summarises which statistics each
generator preserves, in what sense, and what it does not.

## The seven generators at a glance

| Generator   | Model family                                      | Stage-2 sampler              |
| ----------- | ------------------------------------------------- | ---------------------------- |
| `sbm`       | Degree-corrected stochastic block model           | `graph_tool.generate_sbm`    |
| `ec-sbm-v1` | SBM + edge-connectivity guarantee + outlier SBM   | Constructive overlay on full-SBM + separate outlier SBM + heap-greedy degree-match |
| `ec-sbm-v2` | SBM + edge-connectivity guarantee + residual-SBM outliers | Constructive + residual-SBM outlier stage with block-preserving rewire + five-way configurable matcher |
| `abcd`      | Artificial benchmark for community detection      | `ABCDGraphGenerator.jl`      |
| `abcd+o`    | ABCD with explicit outliers                       | `ABCDGraphGenerator.jl` (n_outliers > 0) |
| `lfr`       | Lancichinetti–Fortunato–Radicchi benchmark        | `unweighted_undirected/benchmark` (C++) |
| `npso`      | Non-uniform popularity-similarity optimisation    | `nPSO_model` (MATLAB), wrapped in a secant search over temperature |

All seven consume the **same** inputs at the repo boundary: an undirected
edge list and a reference clustering (node_id → cluster_id, not necessarily
a partition). They diverge at stage 1 on *what summary they extract* and at
stage 2 on *how they sample*.

## Stage 1: what is extracted from G

The profile pipeline is shared through [src/profile_common.py](../src/profile_common.py).
Every generator's `profile.py` reads:

1. The reference clustering into `(nodes, node2com, cluster_counts)`.
2. The edge list into a bidirectional adjacency `neighbors[u] ⊆ V`.
3. An outlier transform controlled by `--outlier-mode` and
   `--drop-outlier-outlier-edges`.

**Outlier definition (unified across all gens).** A node is an outlier iff
it is *unclustered* (appears in the edge list but not in the clustering)
OR it is the sole member of a size-1 cluster (size-1 clusters are
promoted to outliers; see
[identify_outliers](../src/profile_common.py#L60)). The outlier mode then
decides what to do with them:

- `excluded`: drop outliers and every incident edge.
- `singleton`: promote each outlier to its own 1-member cluster.
- `combined`: fold all outliers into one mega-cluster.

This is a *profile-stage* transform: once applied, downstream primitives
see a clustered graph with no "unclustered" nodes.

**Per-generator profile outputs:**

| Generator       | Extracts from G                                              | Default outlier policy |
| --------------- | ------------------------------------------------------------ | ---------------------- |
| `sbm`           | N, cluster sizes, per-node degrees, inter-cluster edge-count matrix | `combined`, keep OO |
| `ec-sbm-v1`     | sbm's outputs + per-cluster min edge cut + `com.csv`         | `excluded` (pipeline-forced) |
| `ec-sbm-v2`     | same as v1                                                   | `excluded`, keep OO |
| `abcd`          | N, cluster sizes, per-node degrees, mixing parameter ξ (global) | `singleton`, keep OO |
| `abcd+o`        | abcd's outputs + n_outliers                                  | `singleton`, **drop OO** (sampler can't emit OO edges) |
| `lfr`           | N, cluster sizes, per-node degrees, mixing parameter μ (mean) | `singleton`, keep OO |
| `npso`          | N, cluster sizes, per-node degrees (ccoeff is measured at stage 2) | `singleton`, keep OO |

Two mixing parameters appear:

- **Global ξ** (ABCD family) = Σ out-edges / Σ total half-edges. The fraction
  of half-edges that cross cluster boundaries.
- **Mean μ** (LFR) = mean over nodes of per-node (out / total), skipping
  zero-degree nodes. Weights small-degree nodes the same as hubs.

Both are computed by
[compute_mixing_parameter](../src/profile_common.py#L229); the choice of
reduction is per-generator because the downstream samplers interpret the
parameter differently.

## Stage 2: what each sampler guarantees

Below, "G" is the input network and "G'" is the sampled output. We list
what is preserved *exactly* (deterministic function of the profile), *in
expectation*, and *by construction but not pointwise*. "Not guaranteed"
covers things the sampler does not target at all.

---

### 1. `sbm`, degree-corrected SBM

[src/sbm/gen.py](../src/sbm/gen.py) calls [`graph_tool.generate_sbm`](https://graph-tool.skewed.de/static/doc/generation.html#graph_tool.generation.generate_sbm)
with `micro_degs=True` and `micro_ers=True`. Those two flags switch
graph-tool's sampler from its canonical (macro) ensemble to the micro
ensemble: sampled graphs have the **exact** out-degree sequence from the
profile AND the **exact** inter-block edge count matrix.

**Guarantees:**
- **N exactly**: number of input nodes (post outlier transform).
- **Block structure exactly**: each node stays in its input-assigned
  block.
- **Degree sequence and inter-block counts approximately**: the micro-SBM
  sampler produces exact degrees and `e_{r,s}` on the multigraph, but
  [gen.py:56-57](../src/sbm/gen.py#L56) calls `remove_parallel_edges` and
  `remove_self_loops`, so the simple-graph `edge.csv` drops however many
  loops/multi-edges the sampler emitted on the block matrix diagonal.

**Not guaranteed:**
- Clustering coefficient. Degree-corrected SBMs are known to produce
  substantially lower triangle counts than real networks.
- Path lengths, mixing times, motif counts beyond edges.

**Determinism.** `gt.generate_sbm` is reproducible given
`np.random.seed`, `gt.seed_rng`, `gt.openmp_set_num_threads(n_threads)`,
and `PYTHONHASHSEED=0`. The first three are set in
[run_sbm_generation](../src/sbm/gen.py#L27); `PYTHONHASHSEED=0` is
exported by [pipeline.sh](../src/sbm/pipeline.sh#L36). `seed=0` silently
disables graph-tool's PRNG (treats 0 as "use entropy"); the default is
`--seed 1` and `0` is flagged as a determinism bug in the memory notes.

---

### 2–3. `ec-sbm-v1` / `ec-sbm-v2`, edge-connected SBM

Both versions produce an SBM-like graph where each cluster is
*k-edge-connected* (k = min edge cut of the cluster's induced subgraph in
G, computed by pymincut during profiling). They share stage 1
([common/profile.py](../src/ec-sbm/common/profile.py)) and the
`combine_edgelists.py` utility, and both pipelines run four stages:
profile, gen_clustered, gen_outlier (+combine), match_degree (+combine).
Stage 2 is where they diverge.

**Stage 2a, clustered construction** (same algorithm in both versions;
[v1](../src/ec-sbm/v1/gen_clustered.py),
[v2](../src/ec-sbm/v2/gen_clustered.py)). Within each cluster C of size n
with mincut k:

1. Order the cluster's nodes by profile degree (descending).
2. The top k+1 nodes form a complete subgraph K_{k+1}. This alone
   guarantees every cluster has edge connectivity ≥ k.
3. Each remaining node attaches to the k highest-degree already-placed
   nodes; if that walk runs out of valid partners, the remainder is
   sampled weighted by profile degree. Whenever a pick would exceed the
   sampling budget, `ensure_edge_capacity` inflates both endpoints'
   residual degree and the block pair's count by 1 rather than dropping
   the edge.

**Stage 2b, outlier/residual wiring.** The two versions split here:

- **v2** ([gen_outlier.py](../src/ec-sbm/v2/gen_outlier.py)) subtracts the
  stage-2a edges (`--exist-edgelist`) from the per-node degree budget and
  rebuilds the inter-block counts by iterating the original edgelist
  (numerically equal to the profile's but recomputed here). Intra-block
  `probs[k,k]` is set to the gap `D_k − E_inter_k`; if the gap is negative
  the deficit is added back to `out_degs` and `probs[k,k]=0`; odd gaps are
  bumped to even with one extra stub on `nodes_in_k[0]`. `gt.generate_sbm`
  then samples on that residual probs matrix with an assignment that
  extends the profile's clusters with outlier blocks (one mega-block under
  the default `--gen-outlier-mode combined`, one singleton per outlier
  under `singleton`; independent of the profile-stage mode).
  `--edge-correction rewire` (default) applies a block-preserving 2-opt
  rewire to self-loops and multi-edges before the final
  `remove_parallel_edges` sweep; `drop` skips the rewire.
- **v1** ([gen_outlier.py](../src/ec-sbm/v1/gen_outlier.py)) instead calls
  `gt.generate_sbm` directly inside `gen_clustered.py` on the *full*
  profile probs matrix, overlays the constructive edges, then drops
  parallel edges and self-loops. v1's separate `gen_outlier.py` then
  re-reads the original edgelist+clustering and samples an outlier-only
  SBM (each outlier its own block) for the outlier-incident edges. v1's
  profile is forced to `excluded`, so the clustered SBM never sees
  outliers.

Both versions merge stage-2a's `edge.csv` with `edge_outlier.csv` via
[combine_edgelists.py](../src/ec-sbm/common/combine_edgelists.py).

**Stage 2c, degree matching**
([v1](../src/ec-sbm/v1/match_degree.py),
[v2](../src/ec-sbm/v2/match_degree.py)). Constructive inflation and
parallel/self-loop removal leave some nodes with residual missing stubs.
Stage 2c tops them up.

- **v1** has one algorithm, no CLI knob: a heap-based max-degree greedy
  (pop the highest-degree u, connect it to every currently-available
  non-neighbor up to its residual). Shape-identical to v2's `greedy`.
- **v2** exposes `--algorithm` with five choices:
  - `greedy`, same as v1.
  - `true_greedy` (v2 default), heap-based dynamic: pop highest-degree u,
    match to the highest-degree valid v, push updated degrees back.
  - `random_greedy`, sample u and v proportional to residual degree.
  - `rewire`, configuration-model pairing of residual stubs with 2-opt
    repair for self-loops and duplicates.
  - `hybrid`, `rewire` first, then `true_greedy` on whatever remains
    invalid.

v1 and v2 are kept separate because v2 diverged architecturally (residual
SBM, block-preserving rewire, matcher menu); the small shared surface
lives under [common/](../src/ec-sbm/common/), and v2's extra helpers are
in [utils.py](../src/ec-sbm/v2/utils.py).

**Guarantees (both versions).**
- **N exactly.**
- **Per-cluster edge connectivity ≥ k** by construction.
- **Block structure exactly** (input partition after the outlier transform).
- **Degree sequence targeted, not exact**: constructive inflation can raise
  degrees above profile; dedup and rewire gridlock can leave stubs unmatched.
- **Inter-cluster edge counts approximately**: v1 samples full probs and
  overlays, so counts are perturbed by overlay + dedup; v2 targets the
  residual, so counts track the profile up to rewire and top-up slack.

**Failure mode.** Stage 2c can gridlock when a node has residual stubs but
no valid partner (all non-neighbors exhausted). Gridlocked stubs are
dropped: v2's `random_greedy`, `true_greedy`, and `rewire` emit a WARN; v1
and v2 `greedy` drop silently. v2's `hybrid` matcher reduces the residual
because its rewire phase is order-insensitive and `true_greedy` handles
whatever rewire couldn't place.

**Not guaranteed.** Exact degree sequence (see above); clustering
coefficient (emergent, not targeted); path lengths and motif counts.

**Determinism.** Three RNGs (`random`, `numpy`, `graph-tool`) seeded per
stage with offsets `seed`/`seed+1`/`seed+2`, plus `PYTHONHASHSEED=0`.
Output `edge.csv` is byte-reproducible at a fixed seed and matcher choice.

---

### 4. `abcd`, ABCD community benchmark

Wraps [ABCDGraphGenerator.jl](https://github.com/bkamins/ABCDGraphGenerator.jl),
a Julia implementation of Kamiński, Prałat, & Théberge's ABCD model. Stage
1 extracts degrees, cluster sizes, and the *global* mixing parameter ξ.
Stage 2 ([gen.py](../src/abcd/gen.py#L11)) shells out to
`externals/abcd/utils/graph_sampler.jl` with those three inputs.

ABCD fixes degree sequence `d` and cluster sizes `s` from the profile,
splits each node's degree into internal/external stubs at ratio ξ (as
`d_i^ext = ⌊ξ · d_i⌉`), runs a configuration model inside each cluster
on the internal stubs and another one globally on the external stubs,
and rewires collisions.

**Guarantees:**
- **N exactly.**
- **Per-cluster sizes exactly.**
- **Degree sequence in expectation**: exact if no rewiring is triggered;
  slightly perturbed otherwise (rewiring changes incident endpoints).
- **Mixing ξ in expectation**: each node's external fraction is targeted
  to ξ independently, so the global fraction concentrates fast.

**Not guaranteed:**
- Clustering coefficient (not a model parameter; empirically low).
- Specific inter-cluster edge-count matrix (only the aggregate ξ is
  targeted).
- Outlier structure (no explicit outlier nodes; for that, use `abcd+o`).

**Determinism.** Julia's `Random.seed!(parse(Int, ARGS[9]))` is the only
RNG; Julia's `Dict` iteration is insertion-ordered so no hash-seed knob
is needed Julia-side. `PYTHONHASHSEED=0` is still pinned upstream in
[pipeline.sh](../src/abcd/pipeline.sh#L43) as a defensive default for
stage-1.

---

### 5. `abcd+o`, ABCD with outliers

Same sampler as `abcd` but stage 1 additionally counts outliers
(`n_outliers.txt`) and stage 2 prepends a synthetic "outlier" mega-cluster
of size n_outliers to the cluster-size list. ABCD then treats cluster_id=1
as a background block whose nodes connect only through the external
mechanism (no internal edges). An outlier-outlier edge cannot exist in the
sampler; this is a *model constraint*, not a post-hoc filter.

At low ξ, ABCD's Julia sampler may warn `outlier nodes form a community`
(see [OUTLIER_LIFT_WARNING](../src/abcd+o/gen.py#L12)), at which point
the "outlier" block has enough internal cohesion to read as a real
community. In that case stage 2 keeps cluster_id=1 in `com.csv`; otherwise
it strips them (outlier nodes stay in `edge.csv` but are unclustered in
the output).

**Guarantees:** same as abcd, plus:

- **Exact number of outlier nodes**: n_outliers rows appear in `edge.csv`
  as unclustered or cluster_id=1 nodes.
- **Zero outlier-outlier edges** (by the sampler's construction; the
  `abcd+o` profile therefore *defaults* `drop_outlier_outlier_edges=True`
  so stage 1's degree counts match what the sampler can actually produce).

**Not guaranteed:** any of abcd's non-guarantees.

---

### 6. `lfr`, LFR community benchmark

Wraps Lancichinetti, Fortunato, and Radicchi's original C++ benchmark
([externals/lfr/unweighted_undirected/benchmark](../externals/lfr/unweighted_undirected/)).
Unlike ABCD, LFR parametrises its power-laws (not the degree sequence
itself), so stage 2 ([gen.py](../src/lfr/gen.py#L14)) fits two power laws
from the profile's `degree.csv` and `cluster_sizes.csv`:

- `t1`: exponent of the degree distribution (from `powerlaw.Fit` on
  the profile's degrees).
- `t2`: exponent of the cluster-size distribution (from `powerlaw.Fit`
  on cluster_sizes, with `xmin = max(min(cluster_sizes), 3)`).

It also computes mean degree `k`, max degree `maxk`, and min/max cluster
sizes from those same profile arrays, and reads mixing parameter μ (mean
reduction) from `mixing_parameter.txt`. It writes `time_seed.dat` (the
C++ binary reads this) and invokes `./benchmark` with those eight flags
(`-N -k -maxk -minc -maxc -mu -t1 -t2`).

LFR samples degrees and cluster sizes from the two power-laws, assigns
nodes to clusters respecting both, splits each node's degree at ratio
(1−μ) / μ into internal/external stubs, runs a configuration model per
cluster then globally on the externals, and rewires duplicates and
loops.

**Guarantees:**
- **N exactly** (CLI arg).
- **Mean degree ≈ k, max degree ≈ maxk** (truncated power-law).
- **Min cluster size ≥ minc, max ≤ maxc.**
- **Degree distribution ~ power-law with exponent t1**, in expectation.
- **Cluster-size distribution ~ power-law with exponent t2**, in
  expectation.
- **Mean mixing μ**, in expectation per-node.

**Not guaranteed:**
- **Exact degree sequence**: LFR resamples from its power-law. If G's
  actual degrees are highly non-power-law, LFR's output will look
  different in the tails.
- **Block structure**: clusters are assigned fresh, not read from the
  profile. `com.csv` is a *generator output*, not a passthrough.
- Clustering coefficient.

**Determinism.** The C++ binary reads the integer seed from
`./time_seed.dat` in the process's working directory; stage 2 writes that
file before invoking the benchmark.

---

### 7. `npso`, non-uniform PSO

[npso/gen.py](../src/npso/gen.py) wraps
[nPSO_model](https://github.com/biomedical-cybernetics/nPSO_model), a
MATLAB implementation of the non-uniform Popularity-Similarity
Optimisation model by Muscoloni & Cannistraci. nPSO embeds nodes in a
hyperbolic disk under a temperature T that controls clustering, draws
edges by hyperbolic distance, then assigns clusters by angular sector.

Four model knobs come straight from the profile: N, average degree m =
`round(⟨k⟩/2)` (integer), γ (power-law exponent, floor 2.0), c (number
of clusters). The temperature T is *not* in the profile; stage 2
searches it to match the **global clustering coefficient** of G:

1. Compute the exact global clustering coefficient of the input edge list
   via networkit (`ClusteringCoefficient.exactGlobal`).
2. Run nPSO at T ∈ (0, 1). Clustering coefficient decreases monotonically
   in T over (0,1) so a root of f(T) = ccoeff(T) − target exists.
3. Secant when both endpoint residuals are known with opposite signs,
   otherwise midpoint; a 5%-of-bracket margin guard ([_next_T](../src/npso/gen.py#L406))
   forces midpoint when secant would land too close to either endpoint.
   Up to 100 iters; stop when best |f(T)| < 0.005 or the between-iter
   change in ccoeff is < 0.0001.
4. The best-seen (T, edges, com) is kept and written out.

The search is resumable: each iteration's (T, ccoeff) is appended to
`search_log.json` (atomic write via os.replace), keyed by a sha256 of
the four model knobs + target + seed. On rerun with the same knobs, the
log replays until divergence or convergence, then re-runs `best_T` once
to restore the in-memory edge/com matrices.

**Guarantees:**
- **N exactly.**
- **Number of clusters exactly**: c (angular sectors).
- **Global clustering coefficient ≈ target**, within the 0.005 tolerance
  or the last iter's progress, whichever comes first. Not tight: the
  search may exhaust iters without converging, in which case the best-seen
  T is used.
- **Degree distribution asymptotic to γ**, in expectation.

**Not guaranteed:**
- Exact degree sequence.
- Cluster sizes: nPSO's angular-sector assignment does not preserve the
  profile's cluster-size distribution.
- Block structure: cluster_ids are generator-output, not profile
  passthrough. Cluster_id=1 in nPSO is the outlier bucket and is stripped
  from `com.csv` ([gen.py:353](../src/npso/gen.py#L353)).

**Determinism.** The MATLAB wrapper at [src/npso/matlab/run_npso.m](../src/npso/matlab/run_npso.m#L3)
calls `rng(seed)` before `nPSO_model`; MATLAB is pinned single-threaded
three ways (pipeline default `N_THREADS=1`, `-singleCompThread`,
`maxNumCompThreads(n_threads)`). Over 100 iters of the secant loop the
seed is reused, so the trajectory is deterministic given the target
global clustering coefficient.

---

## Summary: who guarantees what

| Property                                | sbm  | ec-sbm-v1 | ec-sbm-v2 | abcd | abcd+o | lfr  | npso |
| --------------------------------------- | ---- | --------- | --------- | ---- | ------ | ---- | ---- |
| *Size*                                  |      |           |           |      |        |      |      |
| Number of nodes                         | ✓    | ✓         | ✓         | ✓    | ✓      | ✓    | ✓    |
| Cluster sizes                           | ✓    | ✓         | ✓         | ✓    | ✓      | —    | —    |
| *Degrees*                               |      |           |           |      |        |      |      |
| Degree sequence                         | ≈    | ≈         | ≈         | ≈    | ≈      | —    | —    |
| Degree dist. ~ fitted power-law         | —    | —         | —         | —    | —      | ✓    | ✓    |
| *Clustering and blocks*                 |      |           |           |      |        |      |      |
| Block structure (input partition)       | ✓    | ✓         | ✓         | —    | —      | —    | —    |
| Inter-cluster edge counts               | ≈    | ≈         | ≈         | —    | —      | —    | —    |
| Per-cluster edge connectivity ≥ k       | —    | ✓         | ✓         | —    | —      | —    | —    |
| *Mixing and topology*                   |      |           |           |      |        |      |      |
| Global mixing parameter ξ               | —    | —         | —         | ≈    | ≈      | —    | —    |
| Mean per-node mixing parameter μ        | —    | —         | —         | —    | —      | ≈    | —    |
| Global clustering coefficient           | —    | —         | —         | —    | —      | —    | ≈    |
| *Outliers*                              |      |           |           |      |        |      |      |
| Outlier count from G preserved          | ✓    | —         | —         | ✓    | ✓      | ✓    | ✓    |
| Outliers are identifiable in output     | —    | —         | —         | —    | ✓      | —    | ✓    |
| *Reproducibility*                       |      |           |           |      |        |      |      |
| Byte-reproducible from `--seed`         | ✓    | ✓         | ✓         | ✓    | ✓      | ✓    | ✓    |

✓ = preserved exactly (deterministic function of the profile).
≈ = targeted but perturbed by internal rewiring, post-hoc dedup of
self-loops / parallel edges, degree-matching residual, or search
tolerance. — = not a model parameter. Rows reflect each generator's
*default* flags; `ec-sbm` defaults to `excluded`, which drops outliers.
`abcd+o`'s cluster-size list includes a prepended outlier block of size
n_outliers.

**Self-loops and parallel edges.** All seven generators emit simple
graphs. `sbm`, `ec-sbm-v1`, and `ec-sbm-v2` call `remove_parallel_edges`
+ `remove_self_loops` after `gt.generate_sbm`; the ec-sbm pipelines
additionally `drop_duplicates` in the combine stage. `abcd` / `abcd+o`
/ `lfr` resolve loops and duplicates inside their external samplers via
rewiring; `lfr`'s Python wrapper additionally dedups the C++ binary's
undirected double-listing. `npso` reads edges from a MATLAB {0,1}
adjacency via `triu(adj, 1)` / `find(adj==1)`, so by construction no
self-loops or parallels reach `edge.csv`.

## Which generator should I use?

The answer is almost always "it depends on which statistic you must
preserve." But three rules of thumb:

- **If you need the exact empirical block structure and degrees**:
  `sbm`. That's what the degree-corrected micro-SBM is for.
- **If you additionally need each cluster to be edge-connected** (e.g.,
  because downstream evaluation cares about robustness within clusters):
  `ec-sbm-v2`. Use `--algorithm hybrid` for the most stable
  degree-matching behaviour.
- **If you need a benchmark-style synthetic network where only the
  aggregate mixing matters** (community-detection studies, scaling
  experiments, ground-truth sweeps): `abcd`, `abcd+o`, or `lfr`. The
  former two converge faster and have cleaner outlier semantics; LFR has
  more tuning knobs and is the incumbent benchmark in the field.
- **If you need high clustering coefficient** (social-network-like
  triangle density): `npso` is the only one here that targets it.

No generator guarantees *both* exact degree sequence *and* high
triangle count. That's a known tension: the degree-corrected SBM family
produces nearly-tree-like graphs even when the input is highly clustered.
Bridging the two is active research territory; `ec-sbm`'s constructive
first stage is one attempt.

## Reproducibility notes

All seven generators are byte-reproducible end-to-end under a fixed
`--seed`. The canonical sha256 prefixes at `--seed 1` on
`dnc + sbm-flat-best+cc` (the shipped example):

| Gen        | `edge.csv` (first 12 chars) | `com.csv` (first 12 chars) |
| ---------- | --------------------------- | -------------------------- |
| sbm        | `a55621c176bf`              | `e240ae23f3ea`             |
| ec-sbm-v1  | `e2b5a6914b12`              | `e240ae23f3ea`             |
| ec-sbm-v2  | `f0b255d97b90`              | `e240ae23f3ea`             |
| abcd       | `057a8ef26ebc`              | `55c19725f859`             |
| abcd+o     | `be419667a464`              | `1151780594fc`             |
| lfr        | `ea9b42120eb3`              | `2db4f5ab80be`             |
| npso       | `90e5f99dc8b7`              | `b5854b5f88c7`             |

The first three share `com.csv` because SBM-family gens pass through the
input clustering after dropping singletons; the last four emit their own
cluster assignment each run.

Trap: `--seed 0` silently disables graph-tool's PRNG (documented as
"entropy source") and breaks byte-reproducibility for `sbm` and `ec-sbm`.
The default is `--seed 1` everywhere; if you need `0`-equivalent
behaviour, use `1` and live with it.

### Runtime (dnc network, seeds 1-10)

Measured via [scripts/benchmark/bench_gens.sh](../scripts/benchmark/bench_gens.sh):
per generator, 2 warmup + 10 kept runs per seed across seeds 1-10, all
inside a single shell per gen so interpreter, graph-tool / MATLAB engine,
and NFS caches are amortised. All 7 gens produce byte-identical
`edge.csv` within each (gen, seed).

| Generator   | kept mean (s) | kept std (s) | cold (seed 1, run 1) |
| ----------- | ------------: | -----------: | -------------------: |
| sbm         |      3.97     |     0.24     |      21.11 s         |
| lfr         |      4.04     |     0.17     |       7.31 s         |
| abcd        |      6.48     |     0.26     |       6.43 s         |
| abcd+o      |      6.59     |     0.17     |       6.82 s         |
| ec-sbm-v2   |      8.11     |     1.02     |       8.50 s         |
| ec-sbm-v1   |      9.75     |     0.42     |      10.68 s         |
| npso        |     13.17     |     0.48     |      69.26 s         |

**Host:** AMD EPYC-Genoa (32 cores, single-thread pinned for the
benchmark), 125 GiB RAM, RHEL 9.6, NFS-mounted workspace, Python 3.14,
graph-tool 2.98, Julia 1.11.6, MATLAB R2024a. Absolute wall-clock has
noise from a shared login host; ordering and byte-identity are the
load-independent signals.

Takeaways: `sbm` and `npso` pay a large cold cost (graph-tool import,
MATLAB engine start) that disappears after the first run in a shell.
`ec-sbm-v2`'s std is dominated by one NFS-contention outlier across the
100 kept runs (14.98 s max vs. 7.42 s min on seed=4); the median of
every seed's kept runs falls in the 7.5-8.8 s band. ABCD/ABCD+o/LFR are
external-process bound so their cold/warm gap is small. nPSO's warm cost
is the 10-iter temperature secant search itself, not startup.
