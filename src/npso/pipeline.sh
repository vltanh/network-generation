#!/bin/bash
# nPSO pipeline — thin wrapper that delegates to src/_common/simple_pipeline.sh.

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
# nPSO default outlier policy: each outlier becomes its own size-1 cluster.
OUTLIER_MODE="singleton"
DROP_OO=""

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
        --drop-outlier-outlier-edges) DROP_OO="--drop-outlier-outlier-edges" ;;
        --keep-outlier-outlier-edges) DROP_OO="--keep-outlier-outlier-edges" ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "${NPSO_DIR}" ]; then
    echo "Error: --npso-dir is required (path to nPSO_model checkout)."
    exit 1
fi

# Pin PYTHONHASHSEED for stage-1 profile.py determinism (set/dict iteration);
# stage-2 MATLAB inherits the env but is unaffected — run_npso.m calls rng(seed).
export PYTHONHASHSEED=0

SETUP="${OUTPUT_DIR}/.state/setup"

GEN_NAME="npso"
GEN_SCRIPT_DIR="${SCRIPT_DIR}"
GEN_PROFILE_OUTPUTS=(degree.csv cluster_sizes.csv params.txt)
# npso also reads the original edgelist at stage 2 (for the clustering-coeff
# computation); declare it so stage-2 cache tracks that input.
GEN_EXTRA_STAGE2_INPUTS="${INPUT_EDGELIST}"
# shellcheck disable=SC2034
GEN_PROFILE_CLI_ARGS=(--outlier-mode "${OUTLIER_MODE}")
if [ -n "${DROP_OO}" ]; then
    GEN_PROFILE_CLI_ARGS+=("${DROP_OO}")
fi
# shellcheck disable=SC2034
GEN_CLI_ARGS=(
    --input-edgelist   "${INPUT_EDGELIST}"
    --degree           "${SETUP}/degree.csv"
    --cluster-sizes    "${SETUP}/cluster_sizes.csv"
    --npso-dir         "${NPSO_DIR}"
    --n-threads        "${N_THREADS}"
)

# shellcheck disable=SC1091
source "${SHARED_DIR}/simple_pipeline.sh"
