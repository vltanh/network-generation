#!/bin/bash
# SBM pipeline — thin wrapper that delegates to src/_common/simple_pipeline.sh.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}/src/sbm"
fi
SHARED_DIR="$( cd "${SCRIPT_DIR}/../_common" && pwd )"

TIMEOUT="3d"
SEED=1
N_THREADS=1
KEEP_STATE=0
# SBM default outlier policy: fold all outliers into one mega-cluster so every
# edge (including outlier-outlier and clustered-outlier) routes through the
# same block structure; keep OO edges.
OUTLIER_MODE="combined"
DROP_OO_BOOL="false"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --seed) SEED="$2"; shift ;;
        --n-threads) N_THREADS="$2"; shift ;;
        --keep-state) KEEP_STATE=1 ;;
        --outlier-mode) OUTLIER_MODE="$2"; shift ;;
        --drop-outlier-outlier-edges) DROP_OO_BOOL="true" ;;
        --keep-outlier-outlier-edges) DROP_OO_BOOL="false" ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

export OMP_NUM_THREADS="${N_THREADS}"
# gt.generate_sbm is hash-seed-sensitive even with gt.seed_rng + single OMP
# thread; pin PYTHONHASHSEED so final edge.csv is byte-stable.
export PYTHONHASHSEED=0

# Must match STG1_SETUP_DIR in _common/simple_pipeline.sh.
SETUP="${OUTPUT_DIR}/.state/setup"
STG1_PARAMS_PATH="${SETUP}/params.txt"

GEN_NAME="sbm"
GEN_SCRIPT_DIR="${SCRIPT_DIR}"
GEN_PROFILE_OUTPUTS=(node_id.csv cluster_id.csv assignment.csv degree.csv edge_counts.csv)
# Pipeline writes ${STG1_PARAMS_PATH} before profile.py runs; profile reads it
# (CLI flags would override, but we pass none so the file is authoritative).
# shellcheck disable=SC2034
GEN_PROFILE_CLI_ARGS=(--params-file "${STG1_PARAMS_PATH}")
# shellcheck disable=SC2034
GEN_CLI_ARGS=(
    --node-id          "${SETUP}/node_id.csv"
    --cluster-id       "${SETUP}/cluster_id.csv"
    --assignment       "${SETUP}/assignment.csv"
    --degree           "${SETUP}/degree.csv"
    --edge-counts      "${SETUP}/edge_counts.csv"
    --input-clustering "${INPUT_CLUSTERING}"
    --n-threads        "${N_THREADS}"
)

# Per-stage params.txt contents — fingerprints output-affecting knobs so the
# cache invalidates when they change. See _common/state.sh:write_params_file.
# shellcheck disable=SC2034
GEN_TOPLEVEL_PARAMS=(
    "seed=${SEED}"
    "n_threads=${N_THREADS}"
    "outlier_mode=${OUTLIER_MODE}"
    "drop_outlier_outlier_edges=${DROP_OO_BOOL}"
)
# shellcheck disable=SC2034
GEN_PROFILE_PARAMS=(
    "outlier_mode=${OUTLIER_MODE}"
    "drop_outlier_outlier_edges=${DROP_OO_BOOL}"
)
# shellcheck disable=SC2034
GEN_STAGE2_PARAMS=(
    "seed=${SEED}"
    "n_threads=${N_THREADS}"
)

# shellcheck disable=SC1091
source "${SHARED_DIR}/simple_pipeline.sh"
