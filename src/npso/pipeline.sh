#!/bin/bash
# nPSO pipeline: thin wrapper that delegates to src/_common/simple_pipeline.sh.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}/src/npso"
fi
SHARED_DIR="$( cd "${SCRIPT_DIR}/../_common" && pwd )"

TIMEOUT="3d"
SEED=1
N_THREADS=1
KEEP_STATE=0
NPSO_DIR=""
OUTLIER_MODE="singleton"
DROP_OO_BOOL="false"
MODEL="nPSO2"
# nPSO's MATLAB sampler emits integer node IDs 1..N with fresh clusters;
# when match_degree runs, --remap pairs by descending-degree rank.
REMAP_ENABLE=1
MATCH_DEGREE_ENABLE=0
MATCH_DEGREE_ALGORITHM="hybrid"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --npso-dir) NPSO_DIR="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --seed) SEED="$2"; shift ;;
        --n-threads) N_THREADS="$2"; shift ;;
        --keep-state) KEEP_STATE=1 ;;
        --outlier-mode) OUTLIER_MODE="$2"; shift ;;
        --drop-outlier-outlier-edges) DROP_OO_BOOL="true" ;;
        --keep-outlier-outlier-edges) DROP_OO_BOOL="false" ;;
        --model) MODEL="$2"; shift ;;
        --remap) REMAP_ENABLE=1 ;;
        --no-remap) REMAP_ENABLE=0 ;;
        --match-degree) MATCH_DEGREE_ENABLE=1 ;;
        --no-match-degree) MATCH_DEGREE_ENABLE=0 ;;
        --match-degree-algorithm) MATCH_DEGREE_ALGORITHM="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "${NPSO_DIR}" ]; then
    echo "Error: --npso-dir is required (path to nPSO_model checkout)."
    exit 1
fi

case "${MODEL}" in
    nPSO1|nPSO2|nPSO3) ;;
    *)
        echo "Error: --model must be one of nPSO1|nPSO2|nPSO3 (got '${MODEL}')." >&2
        exit 1
        ;;
esac

# Stage-1 profile.py needs pinned hash seed; stage-2 MATLAB is unaffected.
export PYTHONHASHSEED=0

SETUP="${OUTPUT_DIR}/.state/setup"
STG1_PARAMS_PATH="${SETUP}/params.txt"

GEN_NAME="npso"
GEN_SCRIPT_DIR="${SCRIPT_DIR}"
GEN_PROFILE_OUTPUTS=(degree.csv cluster_sizes.csv derived.txt)
# shellcheck disable=SC2034
GEN_PROFILE_CLI_ARGS=(--params-file "${STG1_PARAMS_PATH}")
# Stage-2 CLI is built from derived.json by gen_post_stage1 below. Empty
# placeholder so simple_pipeline.sh's state tracking is happy.
# shellcheck disable=SC2034
GEN_CLI_ARGS=()

GEN_MATCH_DEGREE_ENABLE="${MATCH_DEGREE_ENABLE}"
GEN_MATCH_DEGREE_ALGORITHM="${MATCH_DEGREE_ALGORITHM}"
GEN_MATCH_DEGREE_USE_REMAP="${REMAP_ENABLE}"

# shellcheck disable=SC2034
GEN_TOPLEVEL_PARAMS=(
    "seed=${SEED}"
    "n_threads=${N_THREADS}"
    "model=${MODEL}"
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
    "n_threads=${N_THREADS}"
    "model=${MODEL}"
)

# Post-stage-1 hook: unpack derived.txt into scalar CLI flags. Keeps
# gen.py decoupled from profile's on-disk format so users can invoke
# gen.py standalone with scalars they got from somewhere else. The file
# uses the params.txt `key=value` shape so bash reads it directly.
gen_post_stage1() {
    local setup_dir="$1"
    local derived="${setup_dir}/derived.txt"
    if [ ! -f "${derived}" ]; then
        echo "Error [npso]: derived.txt missing at ${derived}" >&2
        exit 2
    fi
    declare -A D
    local k v
    while IFS='=' read -r k v; do
        [ -z "${k}" ] && continue
        D[$k]="$v"
    done < "${derived}"

    GEN_CLI_ARGS=(
        --N                   "${D[N]}"
        --m                   "${D[m]}"
        --gamma               "${D[gamma]}"
        --c                   "${D[c]}"
        --target-ccoeff       "${D[target_ccoeff]}"
        --mixing-proportions  "${D[mixing_proportions]}"
        --npso-dir            "${NPSO_DIR}"
        --n-threads           "${N_THREADS}"
        --model               "${MODEL}"
    )
}

# shellcheck disable=SC1091
source "${SHARED_DIR}/simple_pipeline.sh"
