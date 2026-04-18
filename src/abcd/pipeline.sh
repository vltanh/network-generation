#!/bin/bash
# ABCD pipeline — thin wrapper that delegates to src/_common/simple_pipeline.sh.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}/src/abcd"
fi
SHARED_DIR="$( cd "${SCRIPT_DIR}/../_common" && pwd )"

TIMEOUT="3d"
SEED=0
N_THREADS=1
ABCD_DIR=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --abcd-dir) ABCD_DIR="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --seed) SEED="$2"; shift ;;
        --n-threads) N_THREADS="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "${ABCD_DIR}" ]; then
    echo "Error: --abcd-dir is required (path to ABCDGraphGenerator.jl checkout)."
    exit 1
fi

export JULIA_NUM_THREADS="${N_THREADS}"

SETUP="${OUTPUT_DIR}/.state/setup"

GEN_NAME="abcd"
GEN_SCRIPT_DIR="${SCRIPT_DIR}"
GEN_PROFILE_OUTPUTS=(degree.csv cluster_sizes.csv mixing_parameter.txt)
# shellcheck disable=SC2034
GEN_CLI_ARGS=(
    --degree            "${SETUP}/degree.csv"
    --cluster-sizes     "${SETUP}/cluster_sizes.csv"
    --mixing-parameter  "${SETUP}/mixing_parameter.txt"
    --abcd-dir          "${ABCD_DIR}"
)

# shellcheck disable=SC1091
source "${SHARED_DIR}/simple_pipeline.sh"
