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

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --seed) SEED="$2"; shift ;;
        --n-threads) N_THREADS="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

export OMP_NUM_THREADS="${N_THREADS}"

if [ ! -f "${INPUT_EDGELIST}" ] || [ ! -f "${INPUT_CLUSTERING}" ]; then
    echo "Error: The input network or clustering file does not exist."
    exit 1
fi

SETUP_DIR="${OUTPUT_DIR}/setup"
mkdir -p "${SETUP_DIR}" "${OUTPUT_DIR}"

{ timeout "${TIMEOUT}" /usr/bin/time -v python "${SRC_DIR}/profile.py" \
    --edgelist "${INPUT_EDGELIST}" \
    --clustering "${INPUT_CLUSTERING}" \
    --output-folder "${SETUP_DIR}" \
    --generator sbm; } 2> "${SETUP_DIR}/time_and_err.log"

{ timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/gen.py" \
    --node-id "${SETUP_DIR}/node_id.csv" \
    --cluster-id "${SETUP_DIR}/cluster_id.csv" \
    --assignment "${SETUP_DIR}/assignment.csv" \
    --degree "${SETUP_DIR}/degree.csv" \
    --edge-counts "${SETUP_DIR}/edge_counts.csv" \
    --output-folder "${OUTPUT_DIR}" \
    --seed "${SEED}" \
    --n-threads "${N_THREADS}"; } 2> "${OUTPUT_DIR}/time_and_err.log"

if [ ! -f "${OUTPUT_DIR}/edge.csv" ]; then
    echo "Error: SBM generation failed — no edge.csv produced."
    exit 1
fi

echo "=== SBM pipeline completed successfully ==="
