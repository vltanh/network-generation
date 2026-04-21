#!/bin/bash
# ABCD+o pipeline — thin wrapper that delegates to src/_common/simple_pipeline.sh.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}/src/abcd+o"
fi
SHARED_DIR="$( cd "${SCRIPT_DIR}/../_common" && pwd )"

TIMEOUT="3d"
SEED=1
N_THREADS=1
KEEP_STATE=0
ABCD_DIR=""
# ABCD+o default outlier policy: singleton + drop OO edges (the Julia outlier
# sampler does not produce outlier-outlier edges; see src/abcd+o/gen.py).
OUTLIER_MODE="singleton"
DROP_OO="--drop-outlier-outlier-edges"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --abcd-dir) ABCD_DIR="$2"; shift ;;
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

if [ -z "${ABCD_DIR}" ]; then
    echo "Error: --abcd-dir is required (path to ABCDGraphGenerator.jl checkout)."
    exit 1
fi

export JULIA_NUM_THREADS="${N_THREADS}"
# Pin PYTHONHASHSEED for stage-1 profile.py determinism (set/dict iteration);
# stage-2 Julia inherits the env but is unaffected by Python's hash seed.
export PYTHONHASHSEED=0

SETUP="${OUTPUT_DIR}/.state/setup"

GEN_NAME="abcd+o"
GEN_SCRIPT_DIR="${SCRIPT_DIR}"
GEN_PROFILE_OUTPUTS=(degree.csv cluster_sizes.csv mixing_parameter.txt n_outliers.txt outlier_mode.txt)
# shellcheck disable=SC2034
GEN_PROFILE_CLI_ARGS=(--outlier-mode "${OUTLIER_MODE}" "${DROP_OO}")
# shellcheck disable=SC2034
GEN_CLI_ARGS=(
    --degree            "${SETUP}/degree.csv"
    --cluster-sizes     "${SETUP}/cluster_sizes.csv"
    --mixing-parameter  "${SETUP}/mixing_parameter.txt"
    --n-outliers        "${SETUP}/n_outliers.txt"
    --outlier-mode      "${SETUP}/outlier_mode.txt"
    --abcd-dir          "${ABCD_DIR}"
)

# shellcheck disable=SC1091
source "${SHARED_DIR}/simple_pipeline.sh"
