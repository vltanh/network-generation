# State-tracking helpers for network-generation pipelines.
#
# Sourced by per-generator pipeline scripts to provide cache-aware stage
# execution: a stage is skipped if its recorded input/output hashes still
# match what is on disk.

# Check whether a pipeline stage has already been completed and its results
# are still valid.
#
# Returns 0 (true) if:
#   - the done-file exists,
#   - every output file exists and is non-empty, and
#   - sha256sum verifies every hash recorded in the done-file (the declared
#     inputs and declared outputs; logs and other side-files are not hashed).
#
# The declared inputs aren't passed because input integrity is verified
# implicitly: mark_done recorded the input hashes into the done-file, and
# `sha256sum -c` validates them together with the outputs.
#
# Usage: is_step_done "done_file" "output1 output2..."
is_step_done() {
    local done_file="$1"
    read -r -a outputs <<< "$2"

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
# containing SHA-256 hashes of the declared input files and the declared
# output files only.  Side-files in the output directory (logs, scratch,
# etc.) are deliberately *not* hashed, so incidental churn in those files
# does not invalidate the cache on the next run.
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

    local tmp_done="${done_file}.tmp.$$"
    if ! sha256sum "${inputs[@]}" "${outputs[@]}" > "${tmp_done}"; then
        echo "Error [${stage_name}]: sha256sum failed while recording I/O hashes."
        rm -f "${tmp_done}"
        exit 1
    fi
    mv "${tmp_done}" "${done_file}"
    echo "Success [${stage_name}]: I/O hashes recorded atomically. Marked as done."
}

# Check whether every `done` file under a .state/ tree is still consistent.
#
# Returns 0 (true) if `state_dir` exists, contains at least one `done` file,
# and `sha256sum -c` succeeds for every one of them (i.e. every hashed input
# and output recorded by a prior run still matches on disk).
#
# Returns 1 (false) if `state_dir` does not exist, contains no `done` files,
# or any `done` file fails verification.
#
# Used by `--keep-state` reruns: if the top-level done-file validates but
# an inner `.state/*/done` points at a file that was deleted or mutated,
# the intermediates are no longer a faithful rerun-cache and the pipeline
# must regenerate from scratch rather than preserve a broken `.state/`.
#
# Usage: is_state_tree_consistent "state_dir"
is_state_tree_consistent() {
    local state_dir="$1"
    [ -d "${state_dir}" ] || return 1
    local found_any=0
    while IFS= read -r -d '' done_file; do
        found_any=1
        if ! sha256sum --status -c "${done_file}" 2>/dev/null; then
            return 1
        fi
    done < <(find "${state_dir}" -type f -name done -print0)
    [ "${found_any}" = "1" ]
}

# Run a stage under timeout + /usr/bin/time -v, appending a delimited
# EXECUTED block to the stage's time_and_err.log.  Captures the exit code
# outside `time` so timeouts (124) and SIGKILLs get recorded in the footer
# — /usr/bin/time's own "Exit status:" line is only emitted on clean exit.
#
# The log is append-only across invocations; `grep '^===' time_and_err.log`
# yields one header per executed attempt.
#
# Usage: run_stage "time_and_err.log" <command and args...>
run_stage() {
    local log="$1"; shift
    mkdir -p "$(dirname "${log}")"
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) | pid=$$ | host=$(hostname) | EXECUTED ===" >> "${log}"
    { timeout "${TIMEOUT}" /usr/bin/time -v "$@"; } 2>> "${log}"
    local rc=$?
    echo "=== exit=${rc} ===" >> "${log}"
    return ${rc}
}

# Record that a stage was skipped because its cache still validates.
# Same "^===" header shape as run_stage so the stage's full decision history
# can be grepped with one pattern.
#
# Usage: note_stage_skipped "time_and_err.log"
note_stage_skipped() {
    local log="$1"
    mkdir -p "$(dirname "${log}")"
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) | pid=$$ | host=$(hostname) | SKIPPED (cache hit) ===" >> "${log}"
}

# Append a pipeline-invocation header to the top-level run.log.  Called
# unconditionally at pipeline start, before the top-level short-circuit,
# so every invocation (including top-level cache hits) leaves a trace.
#
# Usage: log_invocation_header "run.log" "seed" "keep_state"
log_invocation_header() {
    local final_log="$1"
    local seed="$2"
    local keep_state="$3"
    mkdir -p "$(dirname "${final_log}")"
    echo "=== Invocation $(date -u +%Y-%m-%dT%H:%M:%SZ) | seed=${seed} | keep_state=${keep_state} | pid=$$ | host=$(hostname) ===" >> "${final_log}"
}

# Append a per-stage log file to a consolidated run.log with a prefix.
#
# If the source log exists, each line is written to ${dest_log} prefixed
# with "[<stage_label>] ".  Missing source logs are silently skipped so
# callers can idempotently consolidate without pre-checking existence.
#
# Usage: append_stage_log "dest_log" "stage_label" "source_log"
append_stage_log() {
    local dest_log="$1"
    local stage_label="$2"
    local source_log="$3"

    [ -f "${source_log}" ] || return 0

    mkdir -p "$(dirname "${dest_log}")"
    {
        echo "=== [${stage_label}] ${source_log} ==="
        # Use '|' as sed delimiter so labels that contain '/' (e.g. the
        # ec-sbm "gen_outlier/combine" stage) don't break the substitution.
        sed -e "s|^|[${stage_label}] |" "${source_log}"
        echo ""
    } >> "${dest_log}"
}
