# State-tracking helpers for ec-sbm pipelines.
#
# Sourced by src/ec-sbm/{v1,v2}/pipeline.sh to provide cache-aware stage
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
# Note: the $2 "inputs" argument is parsed but not used directly.  Input
# integrity is verified implicitly because mark_done recorded the input
# hashes into the done-file; sha256sum -c validates them together with the
# outputs.
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
    sha256sum "${inputs[@]}" "${outputs[@]}" > "${tmp_done}"
    mv "${tmp_done}" "${done_file}"
    echo "Success [${stage_name}]: I/O hashes recorded atomically. Marked as done."
}
