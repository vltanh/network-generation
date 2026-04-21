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
# gt.generate_sbm is hash-seed-sensitive even under gt.seed_rng.
export PYTHONHASHSEED=0

SETUP="${OUTPUT_DIR}/.state/setup"
STG1_PARAMS_PATH="${SETUP}/params.txt"

GEN_NAME="sbm"
GEN_SCRIPT_DIR="${SCRIPT_DIR}"
GEN_PROFILE_OUTPUTS=(node_id.csv cluster_id.csv assignment.csv degree.csv edge_counts.csv)
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

# Per-stage params.txt fingerprints (see _common/state.sh:write_params_file).
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
