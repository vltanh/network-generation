#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}"
fi
SRC_DIR="$( cd "${SCRIPT_DIR}/.." && pwd )"
export PYTHONPATH="${SRC_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

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

export JULIA_NUM_THREADS="${N_THREADS}"

if [ ! -f "${INPUT_EDGELIST}" ] || [ ! -f "${INPUT_CLUSTERING}" ]; then
    echo "Error: The input network or clustering file does not exist."
    exit 1
fi

if [ -z "${ABCD_DIR}" ]; then
    echo "Error: --abcd-dir is required (path to ABCDGraphGenerator.jl checkout)."
    exit 1
fi

SETUP_DIR="${OUTPUT_DIR}/setup"
mkdir -p "${SETUP_DIR}" "${OUTPUT_DIR}"

{ timeout "${TIMEOUT}" /usr/bin/time -v python "${SRC_DIR}/profile.py" \
    --edgelist "${INPUT_EDGELIST}" \
    --clustering "${INPUT_CLUSTERING}" \
    --output-folder "${SETUP_DIR}" \
    --generator abcd+o; } 2> "${SETUP_DIR}/time_and_err.log"

{ timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/gen.py" \
    --degree "${SETUP_DIR}/degree.csv" \
    --cluster-sizes "${SETUP_DIR}/cluster_sizes.csv" \
    --mixing-parameter "${SETUP_DIR}/mixing_parameter.txt" \
    --n-outliers "${SETUP_DIR}/n_outliers.txt" \
    --abcd-dir "${ABCD_DIR}" \
    --output-folder "${OUTPUT_DIR}" \
    --seed "${SEED}"; } 2> "${OUTPUT_DIR}/time_and_err.log"

if [ ! -f "${OUTPUT_DIR}/edge.csv" ]; then
    echo "Error: ABCD+o generation failed — no edge.csv produced."
    exit 1
fi

echo "=== ABCD+o pipeline completed successfully ==="
