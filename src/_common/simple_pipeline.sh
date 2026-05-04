#!/bin/bash
#
# Shared two-stage pipeline for sbm, abcd, abcd+o, lfr, npso.
# Each generator's src/<gen>/pipeline.sh sets the variables below and sources this.
#
# Wrapper must set: GEN_NAME, GEN_SCRIPT_DIR, INPUT_EDGELIST, INPUT_CLUSTERING,
#   OUTPUT_DIR, TIMEOUT, SEED, N_THREADS,
#   GEN_PROFILE_OUTPUTS (stage-1 output basenames that gen.py consumes),
#   GEN_CLI_ARGS (flags for gen.py beyond --output-folder/--seed).
# Optional: GEN_PROFILE_CLI_ARGS, GEN_EXTRA_STAGE2_INPUTS,
#   GEN_TOPLEVEL_PARAMS / GEN_PROFILE_PARAMS / GEN_STAGE2_PARAMS (key=value lists),
#   GEN_MATCH_DEGREE_ENABLE (0|1, default 0; add post-gen match_degree + combine stages),
#   GEN_MATCH_DEGREE_ALGORITHM (one of src/match_degree.py:ALGO_TABLE keys; default true_greedy).
#     Includes greedy, true_greedy, random_greedy, rewire, hybrid + the
#     cluster_preserving_* family. Mode is inferred from the algorithm-name
#     prefix: cluster_preserving_* algorithms gate each accepted edge on a
#     per-(min_block, max_block) budget read from the gen's com.csv +
#     reference clustering; everything else runs unconstrained.

set -u

if [ -z "${GEN_NAME:-}" ] || [ -z "${GEN_SCRIPT_DIR:-}" ] \
   || [ -z "${INPUT_EDGELIST:-}" ] || [ -z "${INPUT_CLUSTERING:-}" ] \
   || [ -z "${OUTPUT_DIR:-}" ]; then
    echo "Error [simple_pipeline]: wrapper did not set required variables." >&2
    exit 2
fi

: "${TIMEOUT:=3d}"
: "${SEED:=0}"
: "${N_THREADS:=1}"
: "${KEEP_STATE:=0}"
: "${GEN_EXTRA_STAGE2_INPUTS:=}"
: "${GEN_MATCH_DEGREE_ENABLE:=0}"
: "${GEN_MATCH_DEGREE_ALGORITHM:=true_greedy}"
: "${GEN_MATCH_DEGREE_USE_REMAP:=0}"
: "${GEN_MATCH_DEGREE_OUTLIER_MODE:=combined}"

if ! declare -p GEN_PROFILE_CLI_ARGS >/dev/null 2>&1; then
    GEN_PROFILE_CLI_ARGS=()
fi

for _v in GEN_TOPLEVEL_PARAMS GEN_PROFILE_PARAMS GEN_STAGE2_PARAMS; do
    if ! declare -p "${_v}" >/dev/null 2>&1; then
        eval "${_v}=()"
    fi
done
unset _v

SHARED_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SRC_DIR="$( cd "${SHARED_DIR}/.." && pwd )"
export PYTHONPATH="${SRC_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

if [ ! -f "${INPUT_EDGELIST}" ] || [ ! -f "${INPUT_CLUSTERING}" ]; then
    echo "Error: The input network or clustering file does not exist." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "${SHARED_DIR}/state.sh"

# ==========================================
# Top-level short-circuit
# ==========================================
FINAL_DONE="${OUTPUT_DIR}/done"
FINAL_PARAMS="${OUTPUT_DIR}/params.txt"
FINAL_IN="${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${FINAL_PARAMS}"
if [ "${GEN_MATCH_DEGREE_ENABLE}" = "1" ]; then
    FINAL_OUT="${OUTPUT_DIR}/edge.csv ${OUTPUT_DIR}/com.csv ${OUTPUT_DIR}/sources.json"
else
    FINAL_OUT="${OUTPUT_DIR}/edge.csv ${OUTPUT_DIR}/com.csv"
fi
FINAL_LOG="${OUTPUT_DIR}/run.log"

mkdir -p "${OUTPUT_DIR}"

write_params_file "${FINAL_PARAMS}" "${GEN_TOPLEVEL_PARAMS[@]}"

log_invocation_header "${FINAL_LOG}" "${SEED}" "${KEEP_STATE}"

if is_step_done "${FINAL_DONE}" "${FINAL_OUT}"; then
    # When --keep-state is set, .state/ must exist and be consistent. If missing
    # or stale, drop the top-level done so the pipeline regenerates everything.
    if [ "${KEEP_STATE}" = "1" ] && ! is_state_tree_consistent "${OUTPUT_DIR}/.state"; then
        echo "Top-level done valid but .state/ is missing or inconsistent; regenerating to restore cache."
        rm -rf "${OUTPUT_DIR}/.state" "${FINAL_DONE}"
    else
        echo "Skipping entire pipeline: valid top-level done-file found."
        if [ "${KEEP_STATE}" = "1" ]; then
            echo "Keeping intermediates under ${OUTPUT_DIR}/.state (--keep-state)."
        else
            rm -rf "${OUTPUT_DIR}/.state"
        fi
        echo "=== ${GEN_NAME} pipeline completed successfully ==="
        echo "Final Network: ${OUTPUT_DIR}/edge.csv"
        exit 0
    fi
fi

STATE_DIR="${OUTPUT_DIR}/.state"
STG1_SETUP_DIR="${STATE_DIR}/setup"
STG2_DIR="${STATE_DIR}/gen"

mkdir -p "${STG1_SETUP_DIR}" "${STG2_DIR}"

# ==========================================
# STAGE 1: Profile (shared across all simple gens)
# ==========================================
echo "=== Starting Stage 1: Profile (${GEN_NAME}) ==="

STG1_PARAMS="${STG1_SETUP_DIR}/params.txt"
write_params_file "${STG1_PARAMS}" "${GEN_PROFILE_PARAMS[@]}"

IN_1="${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${STG1_PARAMS}"

OUT_1_PATHS=()
for name in "${GEN_PROFILE_OUTPUTS[@]}"; do
    OUT_1_PATHS+=("${STG1_SETUP_DIR}/${name}")
done
OUT_1="${OUT_1_PATHS[*]}"

if ! is_step_done "${STG1_SETUP_DIR}/done" "${OUT_1}"; then
    run_stage "${STG1_SETUP_DIR}/time_and_err.log" \
        python "${GEN_SCRIPT_DIR}/profile.py" \
        --edgelist "${INPUT_EDGELIST}" \
        --clustering "${INPUT_CLUSTERING}" \
        --output-folder "${STG1_SETUP_DIR}" \
        "${GEN_PROFILE_CLI_ARGS[@]}"
    mark_done "${STG1_SETUP_DIR}/done" "Stage 1 (profile)" "${IN_1}" "${OUT_1}"
else
    note_stage_skipped "${STG1_SETUP_DIR}/time_and_err.log"
    echo "Skipping Stage 1: Valid state found."
fi

# Post-stage-1 hook. Wrappers that derive stage-2 CLI flags from profile
# outputs (e.g. nPSO reads derived.json into scalar --N/--m/... flags) can
# define a `gen_post_stage1 <setup_dir>` bash function to re-populate
# GEN_CLI_ARGS now that stage 1 artifacts are on disk.
if declare -f gen_post_stage1 >/dev/null 2>&1; then
    gen_post_stage1 "${STG1_SETUP_DIR}"
fi

# ==========================================
# STAGE 2: Gen (per-gen CLI via wrapper-supplied GEN_CLI_ARGS)
# ==========================================
echo "=== Starting Stage 2: Gen (${GEN_NAME}) ==="

STG2_PARAMS="${STG2_DIR}/params.txt"
write_params_file "${STG2_PARAMS}" "${GEN_STAGE2_PARAMS[@]}"

IN_2="${OUT_1} ${STG2_PARAMS} ${GEN_EXTRA_STAGE2_INPUTS}"
OUT_2="${STG2_DIR}/edge.csv ${STG2_DIR}/com.csv"

if ! is_step_done "${STG2_DIR}/done" "${OUT_2}"; then
    run_stage "${STG2_DIR}/time_and_err.log" \
        python "${GEN_SCRIPT_DIR}/gen.py" \
        "${GEN_CLI_ARGS[@]}" \
        --output-folder "${STG2_DIR}" \
        --seed "${SEED}"
    mark_done "${STG2_DIR}/done" "Stage 2 (gen)" "${IN_2}" "${OUT_2}"
else
    note_stage_skipped "${STG2_DIR}/time_and_err.log"
    echo "Skipping Stage 2: Valid state found."
fi

# ==========================================
# STAGE 3: Optional post-gen degree matching + combine.
# Guarded by GEN_MATCH_DEGREE_ENABLE. When enabled, the shared tool
# src/match_degree.py tops up missing stubs against the input edgelist
# (3a), then src/combine_edgelists.py merges stage-2 + stage-3a with
# provenance (3b).
# Only meaningful for gens whose stage-2 edge.csv is keyed by input node
# IDs (e.g. sbm). Gens that emit fresh node IDs (lfr, npso) or integer
# IDs without remap (abcd, abcd+o) need extra wiring first.
# ==========================================
if [ "${GEN_MATCH_DEGREE_ENABLE}" = "1" ]; then
    STG_MATCH_DEGREE_EDGES_DIR="${STATE_DIR}/match_degree/edges"
    STG_MATCH_DEGREE_DIR="${STATE_DIR}/match_degree"
    mkdir -p "${STG_MATCH_DEGREE_EDGES_DIR}" "${STG_MATCH_DEGREE_DIR}"

    echo "=== Starting Stage 3: Degree Matching & Combine (${GEN_NAME}) ==="

    # 3a. Match Degrees
    STG_MATCH_DEGREE_EDGES_PARAMS="${STG_MATCH_DEGREE_EDGES_DIR}/params.txt"
    write_params_file "${STG_MATCH_DEGREE_EDGES_PARAMS}" \
        "seed=$((SEED + 1))" \
        "matcher=${GEN_MATCH_DEGREE_ALGORITHM}" \
        "matcher_use_remap=${GEN_MATCH_DEGREE_USE_REMAP}"

    IN_MATCH_DEGREE="${STG2_DIR}/edge.csv ${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${STG_MATCH_DEGREE_EDGES_PARAMS}"
    OUT_MATCH_DEGREE="${STG_MATCH_DEGREE_EDGES_DIR}/degree_matching_edge.csv"

    MATCH_DEGREE_REMAP_FLAG=()
    if [ "${GEN_MATCH_DEGREE_USE_REMAP}" = "1" ]; then
        MATCH_DEGREE_REMAP_FLAG=(--remap)
    fi

    # cluster_preserving_* algorithms need the input + reference clustering
    # to build the per-(min_block, max_block) budget; the global family
    # ignores them. Mode is inferred from the algorithm-name prefix so
    # there is no separate --match-degree-mode flag to keep in sync.
    MATCH_DEGREE_CP_FLAGS=()
    case "${GEN_MATCH_DEGREE_ALGORITHM}" in
        cluster_preserving_*)
            MATCH_DEGREE_CP_FLAGS=(
                --input-clustering "${STG2_DIR}/com.csv"
                --ref-clustering   "${INPUT_CLUSTERING}"
                --outlier-mode     "${GEN_MATCH_DEGREE_OUTLIER_MODE}"
            )
            ;;
    esac

    if ! is_step_done "${STG_MATCH_DEGREE_EDGES_DIR}/done" "${OUT_MATCH_DEGREE}"; then
        run_stage "${STG_MATCH_DEGREE_EDGES_DIR}/time_and_err.log" \
            python "${SRC_DIR}/match_degree.py" \
            --input-edgelist "${STG2_DIR}/edge.csv" \
            --ref-edgelist   "${INPUT_EDGELIST}" \
            --degree-matcher "${GEN_MATCH_DEGREE_ALGORITHM}" \
            "${MATCH_DEGREE_REMAP_FLAG[@]}" \
            "${MATCH_DEGREE_CP_FLAGS[@]}" \
            --output-folder  "${STG_MATCH_DEGREE_EDGES_DIR}" \
            --seed           "$((SEED + 1))"
        mark_done "${STG_MATCH_DEGREE_EDGES_DIR}/done" "Stage 3a (match_degree)" "${IN_MATCH_DEGREE}" "${OUT_MATCH_DEGREE}"
    else
        note_stage_skipped "${STG_MATCH_DEGREE_EDGES_DIR}/time_and_err.log"
        echo "Skipping Stage 3a: Valid state found."
    fi

    # 3b. Final Combination
    IN_MATCH_DEGREE_COMBINE="${STG2_DIR}/edge.csv ${STG_MATCH_DEGREE_EDGES_DIR}/degree_matching_edge.csv"
    OUT_MATCH_DEGREE_COMBINE="${STG_MATCH_DEGREE_DIR}/edge.csv ${STG_MATCH_DEGREE_DIR}/sources.json"

    if ! is_step_done "${STG_MATCH_DEGREE_DIR}/done" "${OUT_MATCH_DEGREE_COMBINE}"; then
        run_stage "${STG_MATCH_DEGREE_DIR}/time_and_err.log" \
            python "${SRC_DIR}/combine_edgelists.py" \
            --edgelist-1 "${STG2_DIR}/edge.csv" \
            --name-1     "gen" \
            --edgelist-2 "${STG_MATCH_DEGREE_EDGES_DIR}/degree_matching_edge.csv" \
            --name-2     "match_degree" \
            --output-folder   "${STG_MATCH_DEGREE_DIR}" \
            --output-filename "edge.csv"
        mark_done "${STG_MATCH_DEGREE_DIR}/done" "Stage 3b (match_degree/combine)" "${IN_MATCH_DEGREE_COMBINE}" "${OUT_MATCH_DEGREE_COMBINE}"
    else
        note_stage_skipped "${STG_MATCH_DEGREE_DIR}/time_and_err.log"
        echo "Skipping Stage 3b: Valid state found."
    fi
fi

# ==========================================
# Promote final outputs. Copy (not move) so stage done-files stay valid.
# When match_degree is enabled, promote stage-3b's edge.csv + sources.json.
# ==========================================
if [ "${GEN_MATCH_DEGREE_ENABLE}" = "1" ]; then
    cp "${STG_MATCH_DEGREE_DIR}/edge.csv"     "${OUTPUT_DIR}/edge.csv"
    cp "${STG_MATCH_DEGREE_DIR}/sources.json" "${OUTPUT_DIR}/sources.json"
else
    cp "${STG2_DIR}/edge.csv"     "${OUTPUT_DIR}/edge.csv"
fi
cp "${STG2_DIR}/com.csv" "${OUTPUT_DIR}/com.csv"

# ==========================================
# Consolidate per-stage logs into top-level run.log (append-only).
# ==========================================
append_stage_log "${FINAL_LOG}" "Stage 1 (profile)" "${STG1_SETUP_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 1 (profile)" "${STG1_SETUP_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 2 (gen)"     "${STG2_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 2 (gen)"     "${STG2_DIR}/run.log"
if [ "${GEN_MATCH_DEGREE_ENABLE}" = "1" ]; then
    append_stage_log "${FINAL_LOG}" "Stage 3a (match_degree)"         "${STG_MATCH_DEGREE_EDGES_DIR}/time_and_err.log"
    append_stage_log "${FINAL_LOG}" "Stage 3a (match_degree)"         "${STG_MATCH_DEGREE_EDGES_DIR}/run.log"
    append_stage_log "${FINAL_LOG}" "Stage 3b (match_degree/combine)" "${STG_MATCH_DEGREE_DIR}/time_and_err.log"
    append_stage_log "${FINAL_LOG}" "Stage 3b (match_degree/combine)" "${STG_MATCH_DEGREE_DIR}/run.log"
fi

# ==========================================
# Record top-level done and clean up
# ==========================================
mark_done "${FINAL_DONE}" "Pipeline" "${FINAL_IN}" "${FINAL_OUT}"

if [ "${KEEP_STATE}" = "1" ]; then
    echo "Keeping intermediates under ${STATE_DIR} (--keep-state)."
else
    rm -rf "${STATE_DIR}"
fi

echo "=== ${GEN_NAME} pipeline completed successfully ==="
echo "Final Network: ${OUTPUT_DIR}/edge.csv"
