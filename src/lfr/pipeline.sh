#!/bin/bash
# LFR pipeline — thin wrapper that delegates to src/_common/simple_pipeline.sh.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}/src/lfr"
fi
SHARED_DIR="$( cd "${SCRIPT_DIR}/../_common" && pwd )"

TIMEOUT="3d"
SEED=0
KEEP_STATE=0
LFR_BINARY=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --lfr-binary) LFR_BINARY="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --seed) SEED="$2"; shift ;;
        --keep-state) KEEP_STATE=1 ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "${LFR_BINARY}" ]; then
    echo "Error: --lfr-binary is required (path to LFR benchmark executable)."
    exit 1
fi

N_THREADS=1

SETUP="${OUTPUT_DIR}/.state/setup"

GEN_NAME="lfr"
GEN_SCRIPT_DIR="${SCRIPT_DIR}"
GEN_PROFILE_OUTPUTS=(degree.csv cluster_sizes.csv mixing_parameter.txt)
# shellcheck disable=SC2034
GEN_CLI_ARGS=(
    --degree            "${SETUP}/degree.csv"
    --cluster-sizes     "${SETUP}/cluster_sizes.csv"
    --mixing-parameter  "${SETUP}/mixing_parameter.txt"
    --lfr-binary        "${LFR_BINARY}"
)

# shellcheck disable=SC1091
source "${SHARED_DIR}/simple_pipeline.sh"
