# Synthetic Network Generator

This script generates a synthetic network based on an empirical network and a reference clustering. It also supports computing the corresponding network and cluster statistics and comparing them against the original distributions.

## Supported Generators

| Generator | Source | Notes |
| --- | --- | --- |
| `ec-sbm-v2` | `src/ec-sbm/v2/` | True-greedy degree matching. |
| `ec-sbm-v1` | `src/ec-sbm/v1/` | Legacy pipeline. |
| `sbm` | `src/sbm/` | Flat SBM from `graph-tool` (micro-canonical, degree-corrected). |
| `abcd` | `src/abcd/` | ABCD benchmark (no outliers). Requires Julia + `ABCDGraphGenerator.jl`. |
| `abcd+o` | `src/abcd+o/` | ABCD with native outlier bucket. Requires Julia + `ABCDGraphGenerator.jl`. |
| `lfr` | `src/lfr/` | LFR benchmark. Requires the compiled `unweighted_undirected/benchmark`. |
| `npso` | `src/npso/` | nPSO (hyperbolic geometry). Requires MATLAB + `nPSO_model`; performs a bisection search on temperature `T` to match the empirical global clustering coefficient. |

All generators share the same profiling step at `src/profile.py`, which emits the setup files (`node_id.csv`, `cluster_id.csv`, `assignment.csv`, `degree.csv`, and — depending on the generator — `edge_counts.csv`, `cluster_sizes.csv`, `mincut.csv`, `mixing_parameter.txt`). Common helpers live in `src/utils.py`.

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
| `--input-edgelist <p>` | Path to the input empirical edge list CSV. |
| `--input-clustering <p>` | Path to the reference clustering `com.csv`. |
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
| `--seed <n>` | Seed forwarded to `sbm`, `abcd`, `abcd+o`, `lfr`, `npso` generators (default `0`). |
| `--abcd-dir <p>` | **Required** for `abcd` / `abcd+o`. Path to an `ABCDGraphGenerator.jl` checkout (exposes `utils/graph_sampler.jl`). |
| `--lfr-binary <p>` | **Required** for `lfr`. Path to the compiled LFR benchmark executable (`unweighted_undirected/benchmark`). |
| `--npso-dir <p>` | **Required** for `npso`. Path to the `nPSO_model` checkout (containing `run_npso.m`); requires `matlab` on PATH. |

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

The two examples below are equivalent invocations for the same network and clustering. Custom mode writes to the specified directory; macro mode writes to its pre-configured `data/` paths.

```bash
./run_generator.sh \
    --generator ec-sbm-v2 --run-id 0 \
    --input-edgelist examples/input/empirical_networks/networks/dnc/dnc.csv \
    --input-clustering "examples/input/reference_clusterings/clusterings/sbm-flat-best+cc/dnc/com.csv" \
    --input-network-stats examples/input/empirical_networks/stats/dnc \
    --input-cluster-stats "examples/input/reference_clusterings/stats/sbm-flat-best+cc/dnc" \
    --output-dir examples/output/synthetic_networks/ \
    --network dnc --clustering-id "sbm-flat-best+cc"  \
    --run-stats --run-comp
```

```bash
./run_generator.sh \
    --generator ec-sbm-v2 --run-id 0 \
    --macro \
    --network dnc --clustering-id "sbm-flat-best+cc"  \
    --run-stats --run-comp
```