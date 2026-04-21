#!/bin/bash
#
# Shared two-stage pipeline for the simple generators (sbm, abcd, abcd+o,
# lfr, npso).  Each generator has a thin wrapper at `src/<gen>/pipeline.sh`
# that parses args, sets a handful of variables, then sources this file.
#
# Contract with the wrapper (variables the wrapper must set before sourcing):
#
#   GEN_NAME            — short generator name, used for --generator and banners
#   GEN_SCRIPT_DIR      — absolute path to the generator's src directory
#                         (contains `gen.py`)
#   INPUT_EDGELIST      — path to the input edgelist (.csv)
#   INPUT_CLUSTERING    — path to the input clustering (.csv)
#   OUTPUT_DIR          — top-level output directory
#   TIMEOUT             — e.g. "3d"; passed to `timeout` for both stages
#   SEED                — RNG seed
#   N_THREADS           — thread count (wrapper also decides whether/how to
#                         export it to a per-gen env var before sourcing)
#
# Stage 1 (profile) invokes the generator's own profile module at
# `${GEN_SCRIPT_DIR}/profile.py`.  Each generator's profile module writes
# only the files that generator's gen.py needs; the wrapper declares them
# via:
#
#   GEN_PROFILE_OUTPUTS  — bash array of stage-1 output *basenames* (relative
#                          to the stage-1 setup dir) that gen.py consumes
#                          (they're the files hashed into stage 1's done).
#   GEN_PROFILE_CLI_ARGS — (optional) bash array of extra flags/values to
#                          pass to profile.py beyond the mandatory
#                          --edgelist/--clustering/--output-folder trio.
#                          Used to surface --outlier-mode and friends.
#
# Stage 2 (gen) invokes `${GEN_SCRIPT_DIR}/gen.py`.  The wrapper specifies
# its CLI shape via:
#
#   GEN_CLI_ARGS         — bash array of flags/values to pass to gen.py,
#                          in addition to the always-present
#                          `--output-folder ${STG2_DIR}` and `--seed ${SEED}`.
#                          The wrapper is responsible for referencing files
#                          in the stage-1 setup dir via ${STG1_SETUP_DIR}.
#   GEN_EXTRA_STAGE2_INPUTS  — (optional) space-separated extra files beyond
#                              the profile outputs that stage 2 reads (e.g.
#                              npso also reads the original edgelist).
#
# The dispatcher handles: top-level short-circuit, `.state/` hiding, stage
# caching via is_step_done/mark_done, consolidated run.log, and cleanup.

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

# GEN_PROFILE_CLI_ARGS is optional; default to empty array if unset.
if ! declare -p GEN_PROFILE_CLI_ARGS >/dev/null 2>&1; then
    GEN_PROFILE_CLI_ARGS=()
fi

# Per-stage params.txt contents. These are pre-rendered key=value strings
# that the pipeline writes into each stage dir as a cache fingerprint before
# is_step_done runs. The wrapper populates them; simple_pipeline.sh writes
# them and threads them into the stage's IN list.
#
#   GEN_TOPLEVEL_PARAMS   — applied to ${OUTPUT_DIR}/params.txt (e.g. seed,
#                           n_threads, outlier_mode, drop_outlier_outlier_edges).
#   GEN_PROFILE_PARAMS    — applied to stage-1 params.txt.
#   GEN_STAGE2_PARAMS     — applied to stage-2 params.txt.
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
FINAL_OUT="${OUTPUT_DIR}/edge.csv ${OUTPUT_DIR}/com.csv"
FINAL_LOG="${OUTPUT_DIR}/run.log"

mkdir -p "${OUTPUT_DIR}"

# Write top-level params.txt first — changes to any user-facing knob
# invalidate the top-level done-file (FINAL_IN includes FINAL_PARAMS).
write_params_file "${FINAL_PARAMS}" "${GEN_TOPLEVEL_PARAMS[@]}"

log_invocation_header "${FINAL_LOG}" "${SEED}" "${KEEP_STATE}"

if is_step_done "${FINAL_DONE}" "${FINAL_OUT}"; then
    # If a .state/ tree exists alongside the top-level done, it must be
    # internally consistent: otherwise the cache is lying about what's on
    # disk, which we must not preserve (under --keep-state) and must not
    # silently wipe (without --keep-state, since an inconsistent .state/
    # is a signal something went wrong — regenerate so the final state is
    # coherent regardless of --keep-state).
    if [ -d "${OUTPUT_DIR}/.state" ] && ! is_state_tree_consistent "${OUTPUT_DIR}/.state"; then
        echo "Top-level done valid but .state/ is inconsistent; regenerating to restore cache."
        rm -rf "${OUTPUT_DIR}/.state" "${FINAL_DONE}"
    else
        echo "Skipping entire pipeline: valid top-level done-file found."
        if [ "${KEEP_STATE}" = "1" ]; then
            echo "Keeping intermediates under ${OUTPUT_DIR}/.state (--keep-state)."
        else
            # Default mode: the top-level done is authoritative and .state/,
            # if present, is already verified consistent above.  Either way
            # we don't need it in the user-facing tree, so remove it.
            rm -rf "${OUTPUT_DIR}/.state"
        fi
        echo "=== ${GEN_NAME} pipeline completed successfully ==="
        echo "Final Network: ${OUTPUT_DIR}/edge.csv"
        exit 0
    fi
fi

# All intermediates live under .state/ — cleaned on success.
STATE_DIR="${OUTPUT_DIR}/.state"
STG1_SETUP_DIR="${STATE_DIR}/setup"
STG2_DIR="${STATE_DIR}/gen"

mkdir -p "${STG1_SETUP_DIR}" "${STG2_DIR}"

# ==========================================
# STAGE 1: Profile (shared across all simple gens)
# ==========================================
echo "=== Starting Stage 1: Profile (${GEN_NAME}) ==="

# Write stage 1 params.txt before is_step_done checks: the file is part of
# IN_1, so changing any profile-stage knob invalidates the cache.
STG1_PARAMS="${STG1_SETUP_DIR}/params.txt"
write_params_file "${STG1_PARAMS}" "${GEN_PROFILE_PARAMS[@]}"

IN_1="${INPUT_EDGELIST} ${INPUT_CLUSTERING} ${STG1_PARAMS}"

# Expand declared profile output basenames into absolute paths.
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
# Promote final outputs into the user-facing tree.
# ==========================================
# Copy rather than move so the stage-2 done-file (which hashes the paths
# under ${STG2_DIR}/) stays valid on rerun.  In default mode .state/ is
# wiped immediately below, so the duplicate is short-lived; under
# --keep-state the copy lets stage 2 short-circuit even if the final
# edge.csv is later mutated.
cp "${STG2_DIR}/edge.csv" "${OUTPUT_DIR}/edge.csv"
cp "${STG2_DIR}/com.csv"  "${OUTPUT_DIR}/com.csv"

# ==========================================
# Consolidate per-stage logs into one top-level run.log
# ==========================================
# FINAL_LOG is append-only and already has this invocation's header from
# log_invocation_header at pipeline start; per-stage logs get appended under it.
append_stage_log "${FINAL_LOG}" "Stage 1 (profile)" "${STG1_SETUP_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 1 (profile)" "${STG1_SETUP_DIR}/run.log"
append_stage_log "${FINAL_LOG}" "Stage 2 (gen)"     "${STG2_DIR}/time_and_err.log"
append_stage_log "${FINAL_LOG}" "Stage 2 (gen)"     "${STG2_DIR}/run.log"

# ==========================================
# Record top-level done (original inputs -> final outputs) and clean up
# ==========================================
mark_done "${FINAL_DONE}" "Pipeline" "${FINAL_IN}" "${FINAL_OUT}"

if [ "${KEEP_STATE}" = "1" ]; then
    echo "Keeping intermediates under ${STATE_DIR} (--keep-state)."
else
    rm -rf "${STATE_DIR}"
fi

echo "=== ${GEN_NAME} pipeline completed successfully ==="
echo "Final Network: ${OUTPUT_DIR}/edge.csv"
