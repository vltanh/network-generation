#!/bin/bash
# LFR pipeline: thin wrapper that delegates to src/_common/simple_pipeline.sh.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}/src/lfr"
fi
SHARED_DIR="$( cd "${SCRIPT_DIR}/../_common" && pwd )"

TIMEOUT="3d"
SEED=1
KEEP_STATE=0
LFR_BINARY=""
OUTLIER_MODE="singleton"
DROP_OO_BOOL="false"
# LFR's C++ binary emits integer node IDs 1..N and regenerates clusters;
# when match_degree runs, --remap pairs output nodes to ref nodes by
# descending-degree rank.
REMAP_ENABLE=1
MATCH_DEGREE_ENABLE=0
MATCH_DEGREE_ALGORITHM="hybrid"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --lfr-binary) LFR_BINARY="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --seed) SEED="$2"; shift ;;
        --keep-state) KEEP_STATE=1 ;;
        --outlier-mode) OUTLIER_MODE="$2"; shift ;;
        --drop-outlier-outlier-edges) DROP_OO_BOOL="true" ;;
        --keep-outlier-outlier-edges) DROP_OO_BOOL="false" ;;
        --remap) REMAP_ENABLE=1 ;;
        --no-remap) REMAP_ENABLE=0 ;;
        --match-degree) MATCH_DEGREE_ENABLE=1 ;;
        --no-match-degree) MATCH_DEGREE_ENABLE=0 ;;
        --match-degree-algorithm) MATCH_DEGREE_ALGORITHM="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "${LFR_BINARY}" ]; then
    echo "Error: --lfr-binary is required (path to LFR benchmark executable)."
    exit 1
fi

N_THREADS=1

# Stage-1 profile.py needs pinned hash seed; LFR C++ reads its seed from time_seed.dat.
export PYTHONHASHSEED=0

SETUP="${OUTPUT_DIR}/.state/setup"
STG1_PARAMS_PATH="${SETUP}/params.txt"

GEN_NAME="lfr"
GEN_SCRIPT_DIR="${SCRIPT_DIR}"
GEN_PROFILE_OUTPUTS=(degree.csv cluster_sizes.csv mixing_parameter.txt)
# shellcheck disable=SC2034
GEN_PROFILE_CLI_ARGS=(--params-file "${STG1_PARAMS_PATH}")
# shellcheck disable=SC2034
GEN_CLI_ARGS=(
    --degree            "${SETUP}/degree.csv"
    --cluster-sizes     "${SETUP}/cluster_sizes.csv"
    --mixing-parameter  "${SETUP}/mixing_parameter.txt"
    --lfr-binary        "${LFR_BINARY}"
)

GEN_MATCH_DEGREE_ENABLE="${MATCH_DEGREE_ENABLE}"
GEN_MATCH_DEGREE_ALGORITHM="${MATCH_DEGREE_ALGORITHM}"
GEN_MATCH_DEGREE_USE_REMAP="${REMAP_ENABLE}"

# shellcheck disable=SC2034
GEN_TOPLEVEL_PARAMS=(
    "seed=${SEED}"
    "n_threads=${N_THREADS}"
    "outlier_mode=${OUTLIER_MODE}"
    "drop_outlier_outlier_edges=${DROP_OO_BOOL}"
    "match_degree_enable=${MATCH_DEGREE_ENABLE}"
    "match_degree_algorithm=${MATCH_DEGREE_ALGORITHM}"
    "match_degree_use_remap=${REMAP_ENABLE}"
)
# shellcheck disable=SC2034
GEN_PROFILE_PARAMS=(
    "outlier_mode=${OUTLIER_MODE}"
    "drop_outlier_outlier_edges=${DROP_OO_BOOL}"
)
# shellcheck disable=SC2034
GEN_STAGE2_PARAMS=(
    "seed=${SEED}"
)

# shellcheck disable=SC1091
source "${SHARED_DIR}/simple_pipeline.sh"
