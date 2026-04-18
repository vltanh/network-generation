#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}"
fi
SRC_DIR="$( cd "${SCRIPT_DIR}/../.." && pwd )"
COMMON_DIR="$( cd "${SCRIPT_DIR}/../common" && pwd )"
# v2 scripts import helpers from the local v2/utils.py; the shared src/
# dir is needed for pipeline_common.py and profile.py.
export PYTHONPATH="${SCRIPT_DIR}:${SRC_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

# Default values
TIMEOUT="3d"
SKIP_STAGE_1=0
SKIP_STAGE_2=0
N_THREADS=1

# Parse named arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --outlier-mode) OUTLIER_MODE="$2"; shift ;;
        --edge-correction) EDGE_CORRECTION="$2"; shift ;;
        --algorithm) ALGORITHM="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --existing-clustered) SKIP_STAGE_1=1 ;;
        --existing-outlier) SKIP_STAGE_1=1; SKIP_STAGE_2=1 ;;
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

# ==========================================
# Helper Functions: State Management
# ==========================================

# Check whether a pipeline stage has already been completed and its results
# are still valid.
#
# Returns 0 (true) if:
#   - the done-file exists,
#   - every output file exists and is non-empty, and
#   - sha256sum verifies every hash recorded in the done-file.
#
# Note: the $2 "inputs" argument is parsed but not used directly.  Input
# integrity is verified implicitly because mark_done hashed both the input
# files and the full output directory into the done-file; sha256sum -c
# validates all of them together.
#
# Usage: is_step_done "done_file" "input1 input2..." "output1 output2..."
is_step_done() {
    local done_file="$1"
    read -r -a inputs <<< "$2"
    read -r -a outputs <<< "$3"

    if [ ! -f "${done_file}" ]; then
        return 1 # False: No state ledger exists
    fi

    # 1. Verify outputs physically exist and have data
    for target_file in "${outputs[@]}"; do
        if [ ! -f "${target_file}" ] || [ ! -s "${target_file}" ]; then
            return 1 # False
        fi
    done

    # 2. Cryptographically verify inputs and outputs haven't mutated
    if ! sha256sum --status -c "${done_file}" 2>/dev/null; then
        echo "State change detected. Recomputing..."
        return 1 # False: Hashes mismatch
    fi

    return 0 # True: State is identical
}

# Record that a pipeline stage has completed successfully.
#
# Verifies every output file exists and is non-empty, then writes a done-file
# containing SHA-256 hashes of:
#   1. All explicit input files (the $3 argument).
#   2. Every file in the same directory as outputs[0] (via find -maxdepth 1),
#      excluding the done-file itself.
#      NOTE: this includes run.log and time_and_err.log.  Any change in log
#      content (e.g. different timestamps) will invalidate the done-file and
#      cause the stage to re-run.  This is intentional conservatism.
#
# The write is atomic: hashes are collected into a .tmp.$$ file first, then
# renamed into place so is_step_done never reads a partial done-file.
#
# Exits the whole pipeline if any output is missing or empty.
#
# Usage: mark_done "done_file" "stage_name" "input1 input2..." "output1 output2..."
mark_done() {
    local done_file="$1"
    local stage_name="$2"
    read -r -a inputs <<< "$3"
    read -r -a outputs <<< "$4"

    for target_file in "${outputs[@]}"; do
        if [ ! -f "${target_file}" ]; then
            echo "Error [${stage_name}]: Output file ${target_file} was not created."
            exit 1
        fi
        if [ ! -s "${target_file}" ]; then
            echo "Error [${stage_name}]: Output file ${target_file} is completely empty (0 bytes)."
            exit 1
        fi
        
        local line_count=$(wc -l < "${target_file}")
        echo "Success [${stage_name}]: Verified ${target_file} ($((line_count - 1)) lines)."
    done
    
    local out_dir=$(dirname "${outputs[0]}")
    local tmp_done="${done_file}.tmp.$$"

    sha256sum "${inputs[@]}" > "${tmp_done}"
    find "${out_dir}" -maxdepth 1 -type f ! -name "$(basename "${done_file}")" ! -name "$(basename "${tmp_done}")" -exec sha256sum {} + >> "${tmp_done}"
    
    mv "${tmp_done}" "${done_file}"
    echo "Success [${stage_name}]: I/O hashes recorded atomically. Marked as done."
}

# Define cross-stage directories
STG1_DIR="${OUTPUT_DIR}/clustered"
STG1_CLEAN_DIR="${OUTPUT_DIR}/clustered/clean"
STG2_DIR="${OUTPUT_DIR}/outlier"

# ==========================================
# STAGE 1: Core Clustered Generation
# ==========================================
if [ "${SKIP_STAGE_1}" -eq 0 ]; then
    echo "=== Starting Stage 1: Core Clustered Generation ==="
    STG1_SETUP_DIR="${OUTPUT_DIR}/clustered/setup"
    mkdir -p "${STG1_CLEAN_DIR}" "${STG1_SETUP_DIR}" "${STG1_DIR}"

    # 1a. Clean Outliers
    IN_1A="${INPUT_EDGELIST} ${INPUT_CLUSTERING}"
    OUT_1A="${STG1_CLEAN_DIR}/edge.csv ${STG1_CLEAN_DIR}/com.csv"
    
    if ! is_step_done "${STG1_CLEAN_DIR}/done" "${IN_1A}" "${OUT_1A}"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${COMMON_DIR}/clean_outlier.py" \
            --edgelist "${INPUT_EDGELIST}" \
            --clustering "${INPUT_CLUSTERING}" \
            --output-folder "${STG1_CLEAN_DIR}"; } 2> "${STG1_CLEAN_DIR}/time_and_err.log"
        mark_done "${STG1_CLEAN_DIR}/done" "Stage 1a (Clean)" "${IN_1A}" "${OUT_1A}"
    else
        echo "Skipping Stage 1a: Valid state found."
    fi

    # 1b. Setup Profiling
    IN_1B="${OUT_1A}"
    OUT_1B="${STG1_SETUP_DIR}/node_id.csv ${STG1_SETUP_DIR}/cluster_id.csv ${STG1_SETUP_DIR}/assignment.csv ${STG1_SETUP_DIR}/degree.csv ${STG1_SETUP_DIR}/mincut.csv ${STG1_SETUP_DIR}/edge_counts.csv"
    
    if ! is_step_done "${STG1_SETUP_DIR}/done" "${IN_1B}" "${OUT_1B}"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${SRC_DIR}/profile.py" \
            --edgelist "${STG1_CLEAN_DIR}/edge.csv" \
            --clustering "${STG1_CLEAN_DIR}/com.csv" \
            --output-folder "${STG1_SETUP_DIR}" \
            --generator ecsbm; } 2> "${STG1_SETUP_DIR}/time_and_err.log"
        mark_done "${STG1_SETUP_DIR}/done" "Stage 1b (Setup)" "${IN_1B}" "${OUT_1B}"
    else
        echo "Skipping Stage 1b: Valid state found."
    fi

    # 1c. Generate Clustered
    IN_1C="${OUT_1B}"
    OUT_1C="${STG1_DIR}/edge.csv"
    
    if ! is_step_done "${STG1_DIR}/done" "${IN_1C}" "${OUT_1C}"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/gen_clustered.py" \
            --node-id "${STG1_SETUP_DIR}/node_id.csv" \
            --cluster-id "${STG1_SETUP_DIR}/cluster_id.csv" \
            --assignment "${STG1_SETUP_DIR}/assignment.csv" \
            --degree "${STG1_SETUP_DIR}/degree.csv" \
            --mincut "${STG1_SETUP_DIR}/mincut.csv" \
            --edge-counts "${STG1_SETUP_DIR}/edge_counts.csv" \
            --output-folder "${STG1_DIR}"; } 2> "${STG1_DIR}/time_and_err.log"
        mark_done "${STG1_DIR}/done" "Stage 1c (Gen Clustered)" "${IN_1C}" "${OUT_1C}"
    else
        echo "Skipping Stage 1c: Valid state found."
    fi
else
    echo "=== Skipping Stage 1 (--existing-clustered/outlier flag detected) ==="
    if [ ! -f "${STG1_DIR}/edge.csv" ]; then
        echo "Error: Cannot skip Stage 1. ${STG1_DIR}/edge.csv not found."
        exit 1
    fi
fi

# ==========================================
# STAGE 2: Outlier Generation & Merge
# ==========================================
if [ "${SKIP_STAGE_2}" -eq 0 ]; then
    echo "=== Starting Stage 2: Outlier Generation & Merge ==="
    STG2_OUTLIER_DIR="${OUTPUT_DIR}/outlier/edges"
    mkdir -p "${STG2_OUTLIER_DIR}" "${STG2_DIR}"

    # 2a. Generate Outliers
    IN_2A="${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${STG1_DIR}/edge.csv"
    OUT_2A="${STG2_OUTLIER_DIR}/edge_outlier.csv"
    
    if ! is_step_done "${STG2_OUTLIER_DIR}/done" "${IN_2A}" "${OUT_2A}"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/gen_outlier.py" \
            --orig-edgelist "${INPUT_EDGELIST}" \
            --orig-clustering "${INPUT_CLUSTERING}" \
            --exist-edgelist "${STG1_DIR}/edge.csv" \
            --outlier-mode "${OUTLIER_MODE}" \
            --edge-correction "${EDGE_CORRECTION}" \
            --output-folder "${STG2_OUTLIER_DIR}"; } 2> "${STG2_OUTLIER_DIR}/time_and_err.log"
        mark_done "${STG2_OUTLIER_DIR}/done" "Stage 2a (Outlier Gen)" "${IN_2A}" "${OUT_2A}"
    else
        echo "Skipping Stage 2a: Valid state found."
    fi

    # 2b. Combine Clustered + Outliers
    IN_2B="${STG1_DIR}/edge.csv ${STG2_OUTLIER_DIR}/edge_outlier.csv"
    OUT_2B="${STG2_DIR}/edge.csv ${STG2_DIR}/sources.json"
    
    if ! is_step_done "${STG2_DIR}/done" "${IN_2B}" "${OUT_2B}"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${COMMON_DIR}/combine_edgelists.py" \
            --edgelist-1 "${STG1_DIR}/edge.csv" \
            --name-1 "clustered" \
            --edgelist-2 "${STG2_OUTLIER_DIR}/edge_outlier.csv" \
            --name-2 "outlier" \
            --output-folder "${STG2_DIR}" \
            --output-filename "edge.csv"; } 2> "${STG2_DIR}/time_and_err.log"
        mark_done "${STG2_DIR}/done" "Stage 2b (First Combine)" "${IN_2B}" "${OUT_2B}"
    else
        echo "Skipping Stage 2b: Valid state found."
    fi
else
    echo "=== Skipping Stage 2 (--existing-outlier flag detected) ==="
    if [ ! -f "${STG2_DIR}/edge.csv" ] || [ ! -f "${STG2_DIR}/sources.json" ]; then
        echo "Error: Cannot skip Stage 2. Required files in ${STG2_DIR} not found."
        exit 1
    fi
fi

# ==========================================
# STAGE 3: Degree Matching & Final Merge
# ==========================================
echo "=== Starting Stage 3: Degree Matching & Final Merge ==="
STG3_MATCH_DIR="${OUTPUT_DIR}/match_degree"
STG3_DIR="${OUTPUT_DIR}"
mkdir -p "${STG3_MATCH_DIR}"

# 3a. Match Degrees
IN_3A="${STG2_DIR}/edge.csv ${INPUT_EDGELIST} ${INPUT_CLUSTERING}"
OUT_3A="${STG3_MATCH_DIR}/degree_matching_edge.csv"

if ! is_step_done "${STG3_MATCH_DIR}/done" "${IN_3A}" "${OUT_3A}"; then
    { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/match_degree.py" \
        --input-edgelist "${STG2_DIR}/edge.csv" \
        --ref-edgelist "${INPUT_EDGELIST}" \
        --ref-clustering "${INPUT_CLUSTERING}" \
        --algorithm "${ALGORITHM}" \
        --output-folder "${STG3_MATCH_DIR}"; } 2> "${STG3_MATCH_DIR}/time_and_err.log"
    mark_done "${STG3_MATCH_DIR}/done" "Stage 3a (Degree Match)" "${IN_3A}" "${OUT_3A}"
else
    echo "Skipping Stage 3a: Valid state found."
fi

# 3b. Final Combination
IN_3B="${STG2_DIR}/edge.csv ${STG2_DIR}/sources.json ${STG3_MATCH_DIR}/degree_matching_edge.csv ${STG1_CLEAN_DIR}/com.csv"
OUT_3B="${STG3_DIR}/edge.csv ${STG3_DIR}/sources.json ${STG3_DIR}/com.csv"

if ! is_step_done "${STG3_DIR}/done" "${IN_3B}" "${OUT_3B}"; then
    { timeout "${TIMEOUT}" /usr/bin/time -v python "${COMMON_DIR}/combine_edgelists.py" \
        --edgelist-1 "${STG2_DIR}/edge.csv" \
        --json-1 "${STG2_DIR}/sources.json" \
        --edgelist-2 "${STG3_MATCH_DIR}/degree_matching_edge.csv" \
        --name-2 "match_degree" \
        --output-folder "${STG3_DIR}" \
        --output-filename "edge.csv"; } 2> "${STG3_DIR}/time_and_err.log"
    cp "${STG1_CLEAN_DIR}/com.csv" "${STG3_DIR}/com.csv"
    mark_done "${STG3_DIR}/done" "Stage 3b (Final Combine)" "${IN_3B}" "${OUT_3B}"
else
    echo "Skipping Stage 3b: Valid state found."
fi

echo "=== Pipeline execution completed successfully! ==="
echo "Final Network: ${STG3_DIR}/edge.csv"