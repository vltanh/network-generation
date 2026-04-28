#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}"
fi
SRC_DIR="$( cd "${SCRIPT_DIR}/.." && pwd )"
SHARED_DIR="$( cd "${SRC_DIR}/_common" && pwd )"

# Default values. Per-stage knobs start empty so --version presets can
# fill them; any explicit flag after --version wins via first-assign.
TIMEOUT="3d"
N_THREADS=1
KEEP_STATE=0
SEED=1
PACKAGE_DIR=""
VERSION=""
OUTLIER_MODE="excluded"
DROP_OO_BOOL="false"
SBM_OVERLAY_BOOL=""
SCOPE=""
GEN_OUTLIER_MODE=""
EDGE_CORRECTION=""
ALGORITHM=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-edgelist) INPUT_EDGELIST="$2"; shift ;;
        --input-clustering) INPUT_CLUSTERING="$2"; shift ;;
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --version) VERSION="$2"; shift ;;
        --outlier-mode) OUTLIER_MODE="$2"; shift ;;
        --drop-outlier-outlier-edges) DROP_OO_BOOL="true" ;;
        --keep-outlier-outlier-edges) DROP_OO_BOOL="false" ;;
        --sbm-overlay) SBM_OVERLAY_BOOL="true" ;;
        --no-sbm-overlay) SBM_OVERLAY_BOOL="false" ;;
        --scope) SCOPE="$2"; shift ;;
        --gen-outlier-mode) GEN_OUTLIER_MODE="$2"; shift ;;
        --edge-correction) EDGE_CORRECTION="$2"; shift ;;
        --match-degree-algorithm) ALGORITHM="$2"; shift ;;
        --timeout) TIMEOUT="$2"; shift ;;
        --n-threads) N_THREADS="$2"; shift ;;
        --keep-state) KEEP_STATE=1 ;;
        --seed) SEED="$2"; shift ;;
        --package-dir) PACKAGE_DIR="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# --version selects a preset flag bundle. Individual flags passed
# alongside --version win (set earlier in the parse), because the
# fallback below fills only empty slots.
case "${VERSION}" in
    v1)
        : "${SBM_OVERLAY_BOOL:=true}"
        : "${SCOPE:=outlier-incident}"
        : "${GEN_OUTLIER_MODE:=singleton}"
        : "${EDGE_CORRECTION:=none}"
        : "${ALGORITHM:=greedy}"
        ;;
    v2)
        : "${SBM_OVERLAY_BOOL:=false}"
        : "${SCOPE:=all}"
        : "${GEN_OUTLIER_MODE:=combined}"
        : "${EDGE_CORRECTION:=rewire}"
        : "${ALGORITHM:=true_greedy}"
        ;;
    "") ;;
    *) echo "Error: --version must be v1 or v2 (got '${VERSION}')." >&2; exit 1 ;;
esac

# Defaults if neither --version nor explicit flag set them.
: "${SBM_OVERLAY_BOOL:=false}"
: "${SCOPE:=all}"
: "${GEN_OUTLIER_MODE:=combined}"
: "${EDGE_CORRECTION:=rewire}"
: "${ALGORITHM:=true_greedy}"

if [ -z "${PACKAGE_DIR}" ]; then
    echo "Error: --package-dir is required (path to externals/ec-sbm)." >&2
    exit 1
fi
PACKAGE_DIR="$( cd "${PACKAGE_DIR}" && pwd )"
PACKAGE_PY_DIR="${PACKAGE_DIR}/src"

# SRC_DIR first so this repo's canonical helpers shadow ec-sbm's vendored
# copies; PACKAGE_PY_DIR last so the algorithm modules (and
# gen_kec_core) resolve.
export PYTHONPATH="${SRC_DIR}:${PACKAGE_PY_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

export OMP_NUM_THREADS="${N_THREADS}"
# Pin: gen_outlier + match_degree's true_greedy iterate sets/dicts whose
# order depends on hash seed.
export PYTHONHASHSEED=0

if [ ! -f "${INPUT_EDGELIST}" ] || [ ! -f "${INPUT_CLUSTERING}" ]; then
    echo "Error: The input network or clustering file does not exist."
    exit 1
fi

source "${SHARED_DIR}/state.sh"

# ==========================================
# Top-level short-circuit
# ==========================================
FINAL_DONE="${OUTPUT_DIR}/done"
FINAL_PARAMS="${OUTPUT_DIR}/params.txt"
FINAL_IN="${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${FINAL_PARAMS}"
FINAL_OUT="${OUTPUT_DIR}/edge.csv ${OUTPUT_DIR}/com.csv ${OUTPUT_DIR}/sources.json"
FINAL_LOG="${OUTPUT_DIR}/run.log"

mkdir -p "${OUTPUT_DIR}"

write_params_file "${FINAL_PARAMS}" \
    "seed=${SEED}" \
    "n_threads=${N_THREADS}" \
    "outlier_mode=${OUTLIER_MODE}" \
    "drop_outlier_outlier_edges=${DROP_OO_BOOL}" \
    "sbm_overlay=${SBM_OVERLAY_BOOL}" \
    "scope=${SCOPE}" \
    "gen_outlier_mode=${GEN_OUTLIER_MODE}" \
    "edge_correction=${EDGE_CORRECTION}" \
    "algorithm=${ALGORITHM}"

log_invocation_header "${FINAL_LOG}" "${SEED}" "${KEEP_STATE}"

if is_step_done "${FINAL_DONE}" "${FINAL_OUT}"; then
    # Top-level done must not coexist with an inconsistent .state/.
    if [ -d "${OUTPUT_DIR}/.state" ] && ! is_state_tree_consistent "${OUTPUT_DIR}/.state"; then
        echo "Top-level done valid but .state/ is inconsistent; regenerating to restore cache."
        rm -rf "${OUTPUT_DIR}/.state" "${FINAL_DONE}"
    else
        echo "Skipping entire pipeline: valid top-level done-file found."
        if [ "${KEEP_STATE}" = "1" ]; then
            echo "Keeping intermediates under ${OUTPUT_DIR}/.state (--keep-state)."
        else
            rm -rf "${OUTPUT_DIR}/.state"
        fi
        echo "=== Pipeline execution completed successfully! ==="
        echo "Final Network: ${OUTPUT_DIR}/edge.csv"
        exit 0
    fi
fi

STATE_DIR="${OUTPUT_DIR}/.state"
STG_PROFILE_DIR="${STATE_DIR}/profile"
STG_GEN_CLUSTERED_DIR="${STATE_DIR}/gen_clustered"
STG_GEN_OUTLIER_EDGES_DIR="${STATE_DIR}/gen_outlier/edges"
STG_GEN_OUTLIER_DIR="${STATE_DIR}/gen_outlier"
STG_MATCH_DEGREE_EDGES_DIR="${STATE_DIR}/match_degree/edges"
STG_MATCH_DEGREE_DIR="${STATE_DIR}/match_degree"

mkdir -p "${STG_PROFILE_DIR}" "${STG_GEN_CLUSTERED_DIR}" \
         "${STG_GEN_OUTLIER_EDGES_DIR}" "${STG_GEN_OUTLIER_DIR}" \
         "${STG_MATCH_DEGREE_EDGES_DIR}" "${STG_MATCH_DEGREE_DIR}"

# ==========================================
# STAGE 1: Profile
# ==========================================
echo "=== Starting Stage 1: Profile ==="

STG_PROFILE_PARAMS="${STG_PROFILE_DIR}/params.txt"
write_params_file "${STG_PROFILE_PARAMS}" \
    "outlier_mode=${OUTLIER_MODE}" \
    "drop_outlier_outlier_edges=${DROP_OO_BOOL}"

IN_PROFILE="${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${STG_PROFILE_PARAMS}"
OUT_PROFILE="${STG_PROFILE_DIR}/node_id.csv ${STG_PROFILE_DIR}/cluster_id.csv ${STG_PROFILE_DIR}/assignment.csv ${STG_PROFILE_DIR}/degree.csv ${STG_PROFILE_DIR}/mincut.csv ${STG_PROFILE_DIR}/edge_counts.csv ${STG_PROFILE_DIR}/com.csv"

if ! is_step_done "${STG_PROFILE_DIR}/done" "${OUT_PROFILE}"; then
    run_stage "${STG_PROFILE_DIR}/time_and_err.log" \
        python "${PACKAGE_PY_DIR}/profile.py" \
        --edgelist "${INPUT_EDGELIST}" \
        --clustering "${INPUT_CLUSTERING}" \
        --output-folder "${STG_PROFILE_DIR}" \
        --params-file "${STG_PROFILE_PARAMS}"
    mark_done "${STG_PROFILE_DIR}/done" "Stage 1 (profile)" "${IN_PROFILE}" "${OUT_PROFILE}"
else
    note_stage_skipped "${STG_PROFILE_DIR}/time_and_err.log"
    echo "Skipping Stage 1: Valid state found."
fi

# ==========================================
# STAGE 2: Generate Clustered
# ==========================================
echo "=== Starting Stage 2: Generate Clustered ==="

STG_GEN_CLUSTERED_PARAMS="${STG_GEN_CLUSTERED_DIR}/params.txt"
write_params_file "${STG_GEN_CLUSTERED_PARAMS}" \
    "seed=${SEED}" \
    "n_threads=${N_THREADS}" \
    "sbm_overlay=${SBM_OVERLAY_BOOL}"

IN_GEN_CLUSTERED="${OUT_PROFILE} ${STG_GEN_CLUSTERED_PARAMS}"
OUT_GEN_CLUSTERED="${STG_GEN_CLUSTERED_DIR}/edge.csv ${STG_GEN_CLUSTERED_DIR}/sources.json"

if [ "${SBM_OVERLAY_BOOL}" = "true" ]; then
    GEN_CLUSTERED_OVERLAY_FLAG=(--sbm-overlay)
else
    GEN_CLUSTERED_OVERLAY_FLAG=(--no-sbm-overlay)
fi

if ! is_step_done "${STG_GEN_CLUSTERED_DIR}/done" "${OUT_GEN_CLUSTERED}"; then
    run_stage "${STG_GEN_CLUSTERED_DIR}/time_and_err.log" \
        python "${PACKAGE_PY_DIR}/gen_clustered.py" \
        --node-id "${STG_PROFILE_DIR}/node_id.csv" \
        --cluster-id "${STG_PROFILE_DIR}/cluster_id.csv" \
        --assignment "${STG_PROFILE_DIR}/assignment.csv" \
        --degree "${STG_PROFILE_DIR}/degree.csv" \
        --mincut "${STG_PROFILE_DIR}/mincut.csv" \
        --edge-counts "${STG_PROFILE_DIR}/edge_counts.csv" \
        --output-folder "${STG_GEN_CLUSTERED_DIR}" \
        --seed "${SEED}" \
        "${GEN_CLUSTERED_OVERLAY_FLAG[@]}"
    mark_done "${STG_GEN_CLUSTERED_DIR}/done" "Stage 2 (gen_clustered)" "${IN_GEN_CLUSTERED}" "${OUT_GEN_CLUSTERED}"
else
    note_stage_skipped "${STG_GEN_CLUSTERED_DIR}/time_and_err.log"
    echo "Skipping Stage 2: Valid state found."
fi

# ==========================================
# STAGE 3: Outlier Generation & Combine
# ==========================================
echo "=== Starting Stage 3: Outlier Generation & Combine ==="

# 3a. Generate Outliers (gen_outlier_mode is independent of profile stage).
STG_GEN_OUTLIER_EDGES_PARAMS="${STG_GEN_OUTLIER_EDGES_DIR}/params.txt"
write_params_file "${STG_GEN_OUTLIER_EDGES_PARAMS}" \
    "seed=$((SEED + 1))" \
    "scope=${SCOPE}" \
    "outlier_mode=${GEN_OUTLIER_MODE}" \
    "edge_correction=${EDGE_CORRECTION}"

# scope=outlier-incident (v1) ignores exist-edgelist; only the
# residual-SBM-over-all-blocks branch (scope=all) subtracts it.
GEN_OUTLIER_EXIST_FLAG=()
if [ "${SCOPE}" = "all" ]; then
    GEN_OUTLIER_EXIST_FLAG=(--exist-edgelist "${STG_GEN_CLUSTERED_DIR}/edge.csv")
fi

IN_GEN_OUTLIER="${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${STG_GEN_CLUSTERED_DIR}/edge.csv ${STG_GEN_OUTLIER_EDGES_PARAMS}"
OUT_GEN_OUTLIER="${STG_GEN_OUTLIER_EDGES_DIR}/edge_outlier.csv ${STG_GEN_OUTLIER_EDGES_DIR}/sources.json"

if ! is_step_done "${STG_GEN_OUTLIER_EDGES_DIR}/done" "${OUT_GEN_OUTLIER}"; then
    run_stage "${STG_GEN_OUTLIER_EDGES_DIR}/time_and_err.log" \
        python "${PACKAGE_PY_DIR}/gen_outlier.py" \
        --orig-edgelist "${INPUT_EDGELIST}" \
        --orig-clustering "${INPUT_CLUSTERING}" \
        "${GEN_OUTLIER_EXIST_FLAG[@]}" \
        --scope "${SCOPE}" \
        --outlier-mode "${GEN_OUTLIER_MODE}" \
        --edge-correction "${EDGE_CORRECTION}" \
        --output-folder "${STG_GEN_OUTLIER_EDGES_DIR}" \
        --seed "$((SEED + 1))"
    mark_done "${STG_GEN_OUTLIER_EDGES_DIR}/done" "Stage 3a (gen_outlier)" "${IN_GEN_OUTLIER}" "${OUT_GEN_OUTLIER}"
else
    note_stage_skipped "${STG_GEN_OUTLIER_EDGES_DIR}/time_and_err.log"
    echo "Skipping Stage 3a: Valid state found."
fi

# 3b. Combine Clustered + Outliers (pure concat; no params.txt).
IN_GEN_OUTLIER_COMBINE="${STG_GEN_CLUSTERED_DIR}/edge.csv ${STG_GEN_CLUSTERED_DIR}/sources.json ${STG_GEN_OUTLIER_EDGES_DIR}/edge_outlier.csv ${STG_GEN_OUTLIER_EDGES_DIR}/sources.json"
OUT_GEN_OUTLIER_COMBINE="${STG_GEN_OUTLIER_DIR}/edge.csv ${STG_GEN_OUTLIER_DIR}/sources.json"

if ! is_step_done "${STG_GEN_OUTLIER_DIR}/done" "${OUT_GEN_OUTLIER_COMBINE}"; then
    run_stage "${STG_GEN_OUTLIER_DIR}/time_and_err.log" \
        python "${SRC_DIR}/combine_edgelists.py" \
        --edgelist-1 "${STG_GEN_CLUSTERED_DIR}/edge.csv" \
        --json-1 "${STG_GEN_CLUSTERED_DIR}/sources.json" \
        --edgelist-2 "${STG_GEN_OUTLIER_EDGES_DIR}/edge_outlier.csv" \
        --json-2 "${STG_GEN_OUTLIER_EDGES_DIR}/sources.json" \
        --output-folder "${STG_GEN_OUTLIER_DIR}" \
        --output-filename "edge.csv"
    mark_done "${STG_GEN_OUTLIER_DIR}/done" "Stage 3b (gen_outlier/combine)" "${IN_GEN_OUTLIER_COMBINE}" "${OUT_GEN_OUTLIER_COMBINE}"
else
    note_stage_skipped "${STG_GEN_OUTLIER_DIR}/time_and_err.log"
    echo "Skipping Stage 3b: Valid state found."
fi

# ==========================================
# STAGE 4: Degree Matching & Final Combine
# ==========================================
echo "=== Starting Stage 4: Degree Matching & Final Combine ==="

# 4a. Match Degrees
STG_MATCH_DEGREE_EDGES_PARAMS="${STG_MATCH_DEGREE_EDGES_DIR}/params.txt"
write_params_file "${STG_MATCH_DEGREE_EDGES_PARAMS}" \
    "seed=$((SEED + 2))" \
    "algorithm=${ALGORITHM}"

IN_MATCH_DEGREE="${STG_GEN_OUTLIER_DIR}/edge.csv ${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${STG_MATCH_DEGREE_EDGES_PARAMS}"
OUT_MATCH_DEGREE="${STG_MATCH_DEGREE_EDGES_DIR}/degree_matching_edge.csv ${STG_MATCH_DEGREE_EDGES_DIR}/sources.json"

if ! is_step_done "${STG_MATCH_DEGREE_EDGES_DIR}/done" "${OUT_MATCH_DEGREE}"; then
    run_stage "${STG_MATCH_DEGREE_EDGES_DIR}/time_and_err.log" \
        python "${SRC_DIR}/match_degree.py" \
        --input-edgelist "${STG_GEN_OUTLIER_DIR}/edge.csv" \
        --ref-edgelist "${INPUT_EDGELIST}" \
        --match-degree-algorithm "${ALGORITHM}" \
        --output-folder "${STG_MATCH_DEGREE_EDGES_DIR}" \
        --seed "$((SEED + 2))"
    mark_done "${STG_MATCH_DEGREE_EDGES_DIR}/done" "Stage 4a (match_degree)" "${IN_MATCH_DEGREE}" "${OUT_MATCH_DEGREE}"
else
    note_stage_skipped "${STG_MATCH_DEGREE_EDGES_DIR}/time_and_err.log"
    echo "Skipping Stage 4a: Valid state found."
fi

# 4b. Final Combination (com.csv is a Stage-1 passthrough; moved separately).
IN_MATCH_DEGREE_COMBINE="${STG_GEN_OUTLIER_DIR}/edge.csv ${STG_GEN_OUTLIER_DIR}/sources.json ${STG_MATCH_DEGREE_EDGES_DIR}/degree_matching_edge.csv ${STG_MATCH_DEGREE_EDGES_DIR}/sources.json"
OUT_MATCH_DEGREE_COMBINE="${OUTPUT_DIR}/edge.csv ${OUTPUT_DIR}/sources.json"

if ! is_step_done "${STG_MATCH_DEGREE_DIR}/done" "${OUT_MATCH_DEGREE_COMBINE}"; then
    run_stage "${STG_MATCH_DEGREE_DIR}/time_and_err.log" \
        python "${SRC_DIR}/combine_edgelists.py" \
        --edgelist-1 "${STG_GEN_OUTLIER_DIR}/edge.csv" \
        --json-1 "${STG_GEN_OUTLIER_DIR}/sources.json" \
        --edgelist-2 "${STG_MATCH_DEGREE_EDGES_DIR}/degree_matching_edge.csv" \
        --json-2 "${STG_MATCH_DEGREE_EDGES_DIR}/sources.json" \
        --output-folder "${STG_MATCH_DEGREE_DIR}" \
        --output-filename "edge.csv"
    # Copy (not move) so hashes in stage done-files stay valid on rerun.
    cp "${STG_MATCH_DEGREE_DIR}/edge.csv" "${OUTPUT_DIR}/edge.csv"
    cp "${STG_MATCH_DEGREE_DIR}/sources.json" "${OUTPUT_DIR}/sources.json"
    mark_done "${STG_MATCH_DEGREE_DIR}/done" "Stage 4b (match_degree/combine)" "${IN_MATCH_DEGREE_COMBINE}" "${OUT_MATCH_DEGREE_COMBINE}"
else
    note_stage_skipped "${STG_MATCH_DEGREE_DIR}/time_and_err.log"
    echo "Skipping Stage 4b: Valid state found."
fi

# Promote com.csv from Stage 1 (copy, not move — preserves stage-1 hash).
cp "${STG_PROFILE_DIR}/com.csv" "${OUTPUT_DIR}/com.csv"

# ==========================================
# Consolidate per-stage logs into top-level run.log (append-only).
# ==========================================
append_stage_log "${FINAL_LOG}" "Stage 1 (profile)" "${STG_PROFILE_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 1 (profile)" "${STG_PROFILE_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 2 (gen_clustered)" "${STG_GEN_CLUSTERED_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 2 (gen_clustered)" "${STG_GEN_CLUSTERED_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 3a (gen_outlier)" "${STG_GEN_OUTLIER_EDGES_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 3a (gen_outlier)" "${STG_GEN_OUTLIER_EDGES_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 3b (gen_outlier/combine)" "${STG_GEN_OUTLIER_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 3b (gen_outlier/combine)" "${STG_GEN_OUTLIER_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 4a (match_degree)" "${STG_MATCH_DEGREE_EDGES_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 4a (match_degree)" "${STG_MATCH_DEGREE_EDGES_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 4b (match_degree/combine)" "${STG_MATCH_DEGREE_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 4b (match_degree/combine)" "${STG_MATCH_DEGREE_DIR}/run.log"

# ==========================================
# Record top-level done and clean up
# ==========================================
mark_done "${FINAL_DONE}" "Pipeline" "${FINAL_IN}" "${FINAL_OUT}"

if [ "${KEEP_STATE}" = "1" ]; then
    echo "Keeping intermediates under ${STATE_DIR} (--keep-state)."
else
    rm -rf "${STATE_DIR}"
fi

echo "=== Pipeline execution completed successfully! ==="
echo "Final Network: ${OUTPUT_DIR}/edge.csv"
