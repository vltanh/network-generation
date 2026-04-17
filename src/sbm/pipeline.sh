#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SRC_DIR="$( cd "${SCRIPT_DIR}/.." && pwd )"
# Share src/ so `from utils import ...` resolves to src/utils.py.
export PYTHONPATH="${SRC_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

TIMEOUT="3d"
SEED=0

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --seed) SEED="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ ! -f "${INPUT_EDGELIST}" ] || [ ! -f "${INPUT_CLUSTERING}" ]; then
    echo "Error: The input network or clustering file does not exist."
    exit 1
fi

mkdir -p "${OUTPUT_DIR}"

{ timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/gen.py" \
    --edgelist "${INPUT_EDGELIST}" \
    --clustering "${INPUT_CLUSTERING}" \
    --output-folder "${OUTPUT_DIR}" \
    --seed "${SEED}"; } 2> "${OUTPUT_DIR}/time_and_err.log"

if [ ! -f "${OUTPUT_DIR}/edge.csv" ]; then
    echo "Error: SBM generation failed — no edge.csv produced."
    exit 1
fi

echo "=== SBM pipeline completed successfully ==="
