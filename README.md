# Synthetic Network Generator

This script generates a synthetic network based on an empirical network and a reference clustering. It also supports computing the corresponding network and cluster statistics and comparing them against the original distributions.

## 1. Custom Mode (Standard Usage)

Use this mode to provide explicit file paths for your own datasets.

**Usage:**

```bash
./run_generator.sh --generator <gen> --run-id <id> --input-edgelist <path> --input-clustering <path> --output-dir <dir> [OPTIONS]
```

### Required Arguments

| Argument | Description |
| --- | --- |
| `--generator <gen>` | Generator to use. One of: `ec-sbm-v2`, `ec-sbm-v1`, `sbm`, `abcd`, `abcd+o`, `lfr`, `npso`. |
| `--run-id <id>` | Numerical run identifier. |
| `--input-edgelist <p>` | Path to the empirical edge list CSV (header `source,target`). |
| `--input-clustering <p>` | Path to the reference clustering CSV (header `node_id,cluster_id`). |
| `--output-dir <dir>` | Root directory for all generated outputs. |

### Optional Arguments & Flags

| Argument | Description |
| --- | --- |
| `--network <id>` | Network identifier; used to sub-group outputs under `<generator>/<clustering-id>/<network>/`. |
| `--clustering-id <id>` | Clustering identifier; used to sub-group outputs under `<generator>/<clustering-id>/`. |
| `--input-network-stats <p>` | Path to empirical network stats directory (for `--run-comp`). |
| `--input-cluster-stats <p>` | Path to reference cluster stats directory (for `--run-comp`). |
| `--run-stats` | Enables computation of synthetic network and cluster statistics. |
| `--run-comp` | Enables statistical comparison. **Requires** `--input-network-stats` and `--input-cluster-stats`. |
| `--keep-state` | Preserve the per-stage `.state/` directory in the output instead of wiping it at setup. Useful for debugging a failed run or resuming partially. |
| `--seed <n>` | Seed forwarded to every generator (default `1`). See note below. |
| `--n-threads <n>` | Thread count for parallelizable backends (default `1`). See note below. |
| `--timeout <dur>` | Wall-clock timeout for the generation step, passed through to each generator's `pipeline.sh` (default `3d`). Accepts any `timeout(1)`-compatible duration (e.g. `30m`, `2h`, `3d`). |
| `--abcd-dir <p>` | Override for `abcd` / `abcd+o`. Defaults to `externals/abcd`. Path to an `ABCDGraphGenerator.jl` checkout (exposes `utils/graph_sampler.jl`). |
| `--lfr-binary <p>` | Override for `lfr`. Defaults to `externals/lfr/unweighted_undirected/benchmark`. Path to the compiled LFR benchmark executable. |
| `--npso-dir <p>` | Override for `npso`. Defaults to `externals/npso`. Path to the `nPSO_model` checkout; requires `matlab` on PATH. |

**Note on `--seed`:** **Do not pass `0`** to graph-tool-backed generators (`sbm`, `ec-sbm-v1`, `ec-sbm-v2`): `gt.seed_rng(0)` is interpreted as "use the system entropy source" and silently disables byte-reproducibility.

**Note on `--n-threads`:** Applies to `sbm`/`ec-sbm-*` (via `OMP_NUM_THREADS` for graph-tool), `abcd`/`abcd+o` (via `JULIA_NUM_THREADS`), and `npso` (via MATLAB `maxNumCompThreads`). `lfr` is single-threaded and ignores this flag. Values greater than `1` are untested and may break determinism or produce unknown behavior; leave at `1` unless you have a reason to change it.

### Directory Structure

**Inputs (Manually Provided):**

* Reference edgelist: `<input-edgelist>`
* Reference clustering: `<input-clustering>`
* Stats (required if `--run-comp`):
    * `<input-network-stats>` (reference edgelist statistics)
    * `<input-cluster-stats>` (reference clustering statistics)

**Outputs (Dynamically Routed):**

* Synthetic edgelist: `<output-dir>/networks/<generator>[/<clustering-id>][/<network>]/<run-id>/edge.csv`
* Stats: `<output-dir>/stats/<generator>[/<clustering-id>][/<network>]/<run-id>/`
    * `cluster/`       (Cluster-dependent metrics)
    * `network/`       (Network-only metrics)
    * `comparison.csv` (Generated if `--run-comp` is enabled)

## 2. Macro Mode

Use this mode to automatically map inputs and outputs to the standard `data/` directory structure.

**Usage:**

```bash
./run_generator.sh --macro --generator <gen> --run-id <id> --network <id> --clustering-id <id> [OPTIONS]
```

### Required Arguments

| Argument | Description |
| --- | --- |
| `--macro` | Enable macro mode; auto-resolves all paths from `data/`. |
| `--generator <gen>` | Generator to use. One of: `ec-sbm-v2`, `ec-sbm-v1`, `sbm`, `abcd`, `abcd+o`, `lfr`, `npso`. |
| `--run-id <id>` | Numerical run identifier. |
| `--network <id>` | Network identifier used to locate empirical data. |
| `--clustering-id <id>` | Ground-truth clustering identifier used to locate reference data. |

### Optional Arguments & Flags

| Argument | Description |
| --- | --- |
| `--run-stats` | Enables computation of synthetic network and cluster statistics. |
| `--run-comp` | Enables statistical comparison. |

### Directory Structure

**Inputs (Auto-Resolved):**

* Reference edgelist: `data/empirical_networks/networks/<network>/<network>.csv`
* Reference clustering: `data/reference_clusterings/clusterings/<clustering-id>/<network>/com.csv`
* Stats (required if `--run-comp`):
    * `data/empirical_networks/stats/<network>/` (reference edgelist statistics)
    * `data/reference_clusterings/stats/<clustering-id>/<network>/` (reference clustering statistics)

**Outputs (Auto-Routed):**

* Synthetic edgelist: `data/synthetic_networks/networks/<generator>/<clustering-id>/<network>/<run-id>/edge.csv`
* Stats: `data/synthetic_networks/stats/<generator>/<clustering-id>/<network>/<run-id>/`
    * `cluster/`       (Cluster-dependent metrics)
    * `network/`       (Network-only metrics)
    * `comparison.csv` (Generated if `--run-comp` is enabled)

## Pipeline Execution Steps

Regardless of the mode used, the script executes the following steps (Steps 2 and 3 are skipped unless their flags are provided):

### Step 1: Generation Pipeline

Generates the synthetic edge list based on the provided empirical bounds.

* **Outputs:** `<output-dir>/edge.csv`

### Step 2: Statistics Computation (`--run-stats`)

Calculates structural and community-dependent metrics for the generated network.

* **Outputs:** `<stats-dir>/cluster/`, `<stats-dir>/network/`

### Step 3: Statistics Comparison (`--run-comp`)

Compares the synthetic statistics against the empirical baseline distributions.

* **Outputs:** `<stats-dir>/comparison.csv`

## Examples

The invocation below reproduces the committed `examples/output/synthetic_networks/` tree for `ec-sbm-v2`, using the inputs committed under `examples/input/`. It runs the full pipeline (generation, synthetic stats, comparison) and preserves the per-stage `.state/` directories that are also committed.

```bash
./run_generator.sh \
    --generator ec-sbm-v2 --run-id 0 --seed 1 \
    --network dnc --clustering-id sbm-flat-best+cc \
    --input-edgelist      examples/input/empirical_networks/networks/dnc/dnc.csv \
    --input-clustering    examples/input/reference_clusterings/clusterings/sbm-flat-best+cc/dnc/com.csv \
    --input-network-stats examples/input/empirical_networks/stats/dnc \
    --input-cluster-stats examples/input/reference_clusterings/stats/sbm-flat-best+cc/dnc \
    --output-dir          examples/output/synthetic_networks \
    --run-stats --run-comp --keep-state
```

The macro-mode equivalent reads from and writes to the standard `data/` tree rather than `examples/`:

```bash
./run_generator.sh \
    --generator ec-sbm-v2 --run-id 0 --seed 1 \
    --macro \
    --network dnc --clustering-id sbm-flat-best+cc \
    --run-stats --run-comp --keep-state
```

## Installation

Generators are independent, so install only the ones you plan to use. See [INSTALL.md](INSTALL.md) for per-generator steps.
