#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}"
fi
SRC_DIR="$( cd "${SCRIPT_DIR}/../.." && pwd )"
COMMON_DIR="$( cd "${SCRIPT_DIR}/../common" && pwd )"
SHARED_DIR="$( cd "${SRC_DIR}/_common" && pwd )"
# Expose the shared src/ directory so scripts can `from pipeline_common import ...`
# and the pipeline can invoke ${SRC_DIR}/profile.py.
export PYTHONPATH="${SRC_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

# Default values
TIMEOUT="3d"
N_THREADS=1
KEEP_STATE=0

# Parse named arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --n-threads) N_THREADS="$2"; shift ;;
        --keep-state) KEEP_STATE=1 ;;
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
source "${SHARED_DIR}/state.sh"

# ==========================================
# Top-level short-circuit
# ==========================================
FINAL_DONE="${OUTPUT_DIR}/done"
FINAL_IN="${INPUT_EDGELIST} ${INPUT_CLUSTERING}"
FINAL_OUT="${OUTPUT_DIR}/edge.csv ${OUTPUT_DIR}/com.csv ${OUTPUT_DIR}/sources.json"

if is_step_done "${FINAL_DONE}" "${FINAL_IN}" "${FINAL_OUT}"; then
    echo "Skipping entire pipeline: valid top-level done-file found."
    # The top-level done is authoritative; any surviving .state/ is unneeded
    # (and, if inherited from an earlier run with inconsistent stage dones,
    # potentially misleading).  Remove it so the output tree is clean.
    rm -rf "${OUTPUT_DIR}/.state"
    echo "=== Pipeline execution completed successfully! ==="
    echo "Final Network: ${OUTPUT_DIR}/edge.csv"
    exit 0
fi

# All intermediate artifacts live under .state/ so the user-facing output
# directory contains only final outputs.  .state/ is cleaned up on success.
STATE_DIR="${OUTPUT_DIR}/.state"
STG1_CLEAN_DIR="${STATE_DIR}/clustered/clean"
STG1_SETUP_DIR="${STATE_DIR}/clustered/setup"
STG1_DIR="${STATE_DIR}/clustered"
STG2_OUTLIER_DIR="${STATE_DIR}/outlier/edges"
STG2_DIR="${STATE_DIR}/outlier"
STG3_MATCH_DIR="${STATE_DIR}/match_degree"

mkdir -p "${OUTPUT_DIR}" "${STG1_CLEAN_DIR}" "${STG1_SETUP_DIR}" \
         "${STG1_DIR}" "${STG2_OUTLIER_DIR}" "${STG2_DIR}" "${STG3_MATCH_DIR}"

# ==========================================
# STAGE 1: Clustered Generation
# ==========================================
echo "=== Starting Stage 1: Core Clustered Generation ==="

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


# ==========================================
# STAGE 2: Outlier Generation & First Merge
# ==========================================
echo "=== Starting Stage 2: Outlier Generation & Merge ==="

# 2a. Generate Outliers
IN_2A="${INPUT_EDGELIST} ${INPUT_CLUSTERING}"
OUT_2A="${STG2_OUTLIER_DIR}/edge_outlier.csv"

if ! is_step_done "${STG2_OUTLIER_DIR}/done" "${IN_2A}" "${OUT_2A}"; then
    { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/gen_outlier.py" \
        --edgelist "${INPUT_EDGELIST}" \
        --clustering "${INPUT_CLUSTERING}" \
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

# ==========================================
# STAGE 3: Degree Matching & Final Merge
# ==========================================
echo "=== Starting Stage 3: Degree Matching & Final Merge ==="

# 3a. Match Degrees
IN_3A="${STG2_DIR}/edge.csv ${INPUT_EDGELIST} ${INPUT_CLUSTERING}"
OUT_3A="${STG3_MATCH_DIR}/degree_matching_edge.csv"

if ! is_step_done "${STG3_MATCH_DIR}/done" "${IN_3A}" "${OUT_3A}"; then
    { timeout "${TIMEOUT}" /usr/bin/time -v python "${SCRIPT_DIR}/match_degree.py" \
        --input-edgelist "${STG2_DIR}/edge.csv" \
        --ref-edgelist "${INPUT_EDGELIST}" \
        --ref-clustering "${INPUT_CLUSTERING}" \
        --output-folder "${STG3_MATCH_DIR}"; } 2> "${STG3_MATCH_DIR}/time_and_err.log"
    mark_done "${STG3_MATCH_DIR}/done" "Stage 3a (Degree Match)" "${IN_3A}" "${OUT_3A}"
else
    echo "Skipping Stage 3a: Valid state found."
fi

# 3b. Final Combination — writes to top-level OUTPUT_DIR.
# com.csv is a passthrough from Stage 1a (not read by combine_edgelists), so
# it's moved directly rather than consumed here.  It's excluded from IN_3B/
# OUT_3B and handled just below so stage 3b's hashes stay tied to what the
# combine step actually reads and writes.
IN_3B="${STG2_DIR}/edge.csv ${STG2_DIR}/sources.json ${STG3_MATCH_DIR}/degree_matching_edge.csv"
OUT_3B="${OUTPUT_DIR}/edge.csv ${OUTPUT_DIR}/sources.json"

STG3_FINAL_DIR="${STATE_DIR}/final"
mkdir -p "${STG3_FINAL_DIR}"

if ! is_step_done "${STATE_DIR}/final.done" "${IN_3B}" "${OUT_3B}"; then
    { timeout "${TIMEOUT}" /usr/bin/time -v python "${COMMON_DIR}/combine_edgelists.py" \
        --edgelist-1 "${STG2_DIR}/edge.csv" \
        --json-1 "${STG2_DIR}/sources.json" \
        --edgelist-2 "${STG3_MATCH_DIR}/degree_matching_edge.csv" \
        --name-2 "match_degree" \
        --output-folder "${STG3_FINAL_DIR}" \
        --output-filename "edge.csv"; } 2> "${STG3_FINAL_DIR}/time_and_err.log"
    # Copy rather than move so stage 3b's done-file and stage 1a's
    # ${STG1_CLEAN_DIR}/com.csv hash still validate on a --keep-state rerun
    # that mutates the final outputs.
    cp "${STG3_FINAL_DIR}/edge.csv" "${OUTPUT_DIR}/edge.csv"
    cp "${STG3_FINAL_DIR}/sources.json" "${OUTPUT_DIR}/sources.json"
    mark_done "${STATE_DIR}/final.done" "Stage 3b (Final Combine)" "${IN_3B}" "${OUT_3B}"
else
    echo "Skipping Stage 3b: Valid state found."
fi

# Promote com.csv from .state/ to OUTPUT_DIR.  Copy rather than move so
# stage 1a's hashed ${STG1_CLEAN_DIR}/com.csv still validates on a
# --keep-state rerun (and so stale ${OUTPUT_DIR}/com.csv is always refreshed
# from the canonical stage 1a output).
cp "${STG1_CLEAN_DIR}/com.csv" "${OUTPUT_DIR}/com.csv"

# ==========================================
# Consolidate per-stage logs into one top-level run.log
# ==========================================
FINAL_LOG="${OUTPUT_DIR}/run.log"
rm -f "${FINAL_LOG}"
append_stage_log "${FINAL_LOG}" "Stage 1a" "${STG1_CLEAN_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 1a" "${STG1_CLEAN_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 1b" "${STG1_SETUP_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 1b" "${STG1_SETUP_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 1c" "${STG1_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 1c" "${STG1_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 2a" "${STG2_OUTLIER_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 2a" "${STG2_OUTLIER_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 2b" "${STG2_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 2b" "${STG2_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 3a" "${STG3_MATCH_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 3a" "${STG3_MATCH_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 3b" "${STG3_FINAL_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 3b" "${STG3_FINAL_DIR}/run.log"

# ==========================================
# Record top-level done (original inputs -> final outputs) and clean up
# ==========================================
mark_done "${FINAL_DONE}" "Pipeline" "${FINAL_IN}" "${FINAL_OUT}"

if [ "${KEEP_STATE}" = "1" ]; then
    echo "Keeping intermediates under ${STATE_DIR} (--keep-state)."
else
    rm -rf "${STATE_DIR}"
fi

echo "=== Pipeline execution completed successfully! ==="
echo "Final Network: ${OUTPUT_DIR}/edge.csv"
