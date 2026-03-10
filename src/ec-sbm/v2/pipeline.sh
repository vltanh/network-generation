#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default values
TIMEOUT="3d"
SKIP_STAGE_1=0
SKIP_STAGE_2=0

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
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ ! -f "${INPUT_EDGELIST}" ] || [ ! -f "${INPUT_CLUSTERING}" ]; then
    echo "Error: The input network or clustering file does not exist."
    exit 1
fi

# ==========================================
# Helper Functions: State Management
# ==========================================

# Checks if the done file exists AND all target output files exist and are not empty
is_step_done() {
    local done_file=$1
    shift # The rest of the arguments are the target files

    if [ ! -f "${done_file}" ]; then
        return 1 # False
    fi

    for target_file in "$@"; do
        if [ ! -f "${target_file}" ] || [ ! -s "${target_file}" ]; then
            return 1 # False
        fi
    done

    return 0 # True
}

# Validates all output files and creates the done file
mark_done() {
    local done_file=$1
    local stage_name=$2
    shift 2 # The rest of the arguments are the target files

    for target_file in "$@"; do
        if [ ! -f "${target_file}" ]; then
            echo "Error [${stage_name}]: Output file ${target_file} was not created (possibly timed out)."
            exit 1
        fi

        if [ ! -s "${target_file}" ]; then
            echo "Error [${stage_name}]: Output file ${target_file} is completely empty (0 bytes)."
            exit 1
        fi
        
        local line_count=$(wc -l < "${target_file}")
        echo "Success [${stage_name}]: Verified ${target_file} ($((line_count - 1)) lines)."
    done
    
    touch "${done_file}"
    echo "Success [${stage_name}]: All required files validated. Marked as done."
}

# Define cross-stage directories
STG1_DIR="${OUTPUT_DIR}/clustered"
STG2_DIR="${OUTPUT_DIR}/outlier"

# ==========================================
# STAGE 1: Core Clustered Generation
# ==========================================
if [ "${SKIP_STAGE_1}" -eq 0 ]; then
    echo "=== Starting Stage 1: Core Clustered Generation ==="
    STG1_CLEAN_DIR="${OUTPUT_DIR}/clustered/clean"
    STG1_SETUP_DIR="${OUTPUT_DIR}/clustered/setup"
    mkdir -p "${STG1_CLEAN_DIR}" "${STG1_SETUP_DIR}" "${STG1_DIR}"

    # 1a. Clean Outliers
    if ! is_step_done "${STG1_CLEAN_DIR}/done" "${STG1_CLEAN_DIR}/edge.csv" "${STG1_CLEAN_DIR}/com.csv"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/clean_outlier.py" \
            --edgelist "${INPUT_EDGELIST}" \
            --clustering "${INPUT_CLUSTERING}" \
            --output-folder "${STG1_CLEAN_DIR}"; } 2> "${STG1_CLEAN_DIR}/time_and_err.log"
        mark_done "${STG1_CLEAN_DIR}/done" "Stage 1a (Clean)" "${STG1_CLEAN_DIR}/edge.csv" "${STG1_CLEAN_DIR}/com.csv"
    else
        echo "Skipping Stage 1a: Already done."
    fi

    # 1b. Setup Profiling
    if ! is_step_done "${STG1_SETUP_DIR}/done" "${STG1_SETUP_DIR}/node_id.csv" "${STG1_SETUP_DIR}/cluster_id.csv" "${STG1_SETUP_DIR}/assignment.csv" "${STG1_SETUP_DIR}/degree.csv" "${STG1_SETUP_DIR}/mincut.csv" "${STG1_SETUP_DIR}/edge_counts.csv"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/setup.py" \
            --edgelist "${STG1_CLEAN_DIR}/edge.csv" \
            --clustering "${STG1_CLEAN_DIR}/com.csv" \
            --output-folder "${STG1_SETUP_DIR}" \
            --generator ecsbm; } 2> "${STG1_SETUP_DIR}/time_and_err.log"
        mark_done "${STG1_SETUP_DIR}/done" "Stage 1b (Setup)" "${STG1_SETUP_DIR}/node_id.csv" "${STG1_SETUP_DIR}/cluster_id.csv" "${STG1_SETUP_DIR}/assignment.csv" "${STG1_SETUP_DIR}/degree.csv" "${STG1_SETUP_DIR}/mincut.csv" "${STG1_SETUP_DIR}/edge_counts.csv"
    else
        echo "Skipping Stage 1b: Already done."
    fi

    # 1c. Generate Clustered
    if ! is_step_done "${STG1_DIR}/done" "${STG1_DIR}/edge.csv"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/gen_clustered.py" \
            --node-id "${STG1_SETUP_DIR}/node_id.csv" \
            --cluster-id "${STG1_SETUP_DIR}/cluster_id.csv" \
            --assignment "${STG1_SETUP_DIR}/assignment.csv" \
            --degree "${STG1_SETUP_DIR}/degree.csv" \
            --mincut "${STG1_SETUP_DIR}/mincut.csv" \
            --edge-counts "${STG1_SETUP_DIR}/edge_counts.csv" \
            --output-folder "${STG1_DIR}"; } 2> "${STG1_DIR}/time_and_err.log"
        mark_done "${STG1_DIR}/done" "Stage 1c (Gen Clustered)" "${STG1_DIR}/edge.csv"
    else
        echo "Skipping Stage 1c: Already done."
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
    if ! is_step_done "${STG2_OUTLIER_DIR}/done" "${STG2_OUTLIER_DIR}/edge_outlier.csv"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/gen_outlier.py" \
            --orig-edgelist "${INPUT_EDGELIST}" \
            --orig-clustering "${INPUT_CLUSTERING}" \
            --exist-edgelist "${STG1_DIR}/edge.csv" \
            --outlier-mode "${OUTLIER_MODE}" \
            --edge-correction "${EDGE_CORRECTION}" \
            --output-folder "${STG2_OUTLIER_DIR}"; } 2> "${STG2_OUTLIER_DIR}/time_and_err.log"
        mark_done "${STG2_OUTLIER_DIR}/done" "Stage 2a (Outlier Gen)" "${STG2_OUTLIER_DIR}/edge_outlier.csv"
    else
        echo "Skipping Stage 2a: Already done."
    fi

    # 2b. Combine Clustered + Outliers
    if ! is_step_done "${STG2_DIR}/done" "${STG2_DIR}/edge.csv" "${STG2_DIR}/sources.json"; then
        { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/combine_edgelists.py" \
            --edgelist-1 "${STG1_DIR}/edge.csv" \
            --name-1 "clustered" \
            --edgelist-2 "${STG2_OUTLIER_DIR}/edge_outlier.csv" \
            --name-2 "outlier" \
            --output-folder "${STG2_DIR}" \
            --output-filename "edge.csv"; } 2> "${STG2_DIR}/time_and_err.log"
        mark_done "${STG2_DIR}/done" "Stage 2b (First Combine)" "${STG2_DIR}/edge.csv" "${STG2_DIR}/sources.json"
    else
        echo "Skipping Stage 2b: Already done."
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
if ! is_step_done "${STG3_MATCH_DIR}/done" "${STG3_MATCH_DIR}/degree_matching_edge.csv"; then
    { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/match_degree.py" \
        --input-edgelist "${STG2_DIR}/edge.csv" \
        --ref-edgelist "${INPUT_EDGELIST}" \
        --ref-clustering "${INPUT_CLUSTERING}" \
        --algorithm "${ALGORITHM}" \
        --output-folder "${STG3_MATCH_DIR}"; } 2> "${STG3_MATCH_DIR}/time_and_err.log"
    mark_done "${STG3_MATCH_DIR}/done" "Stage 3a (Degree Match)" "${STG3_MATCH_DIR}/degree_matching_edge.csv"
else
    echo "Skipping Stage 3a: Already done."
fi

# 3b. Final Combination
if ! is_step_done "${STG3_DIR}/done" "${STG3_DIR}/edge.csv" "${STG3_DIR}/sources.json"; then
    { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/combine_edgelists.py" \
        --edgelist-1 "${STG2_DIR}/edge.csv" \
        --json-1 "${STG2_DIR}/sources.json" \
        --edgelist-2 "${STG3_MATCH_DIR}/degree_matching_edge.csv" \
        --name-2 "match_degree" \
        --output-folder "${STG3_DIR}" \
        --output-filename "edge.csv"; } 2> "${STG3_DIR}/time_and_err.log"
    mark_done "${STG3_DIR}/done" "Stage 3b (Final Combine)" "${STG3_DIR}/edge.csv" "${STG3_DIR}/sources.json"
else
    echo "Skipping Stage 3b: Already done."
fi

echo "=== Pipeline execution completed successfully! ==="
echo "Final Network: ${STG3_DIR}/edge.csv"