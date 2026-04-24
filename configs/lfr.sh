GEN_PIPELINE="src/lfr/pipeline.sh"
GEN_REQUIRED_DIR_VAR="lfr_binary"
GEN_REQUIRED_DIR_FLAG="--lfr-binary"
GEN_EXTRA_ARGS=(
    --binary "${lfr_binary}"
    --seed "${seed}"
    --timeout "${timeout_duration}"
    "${KEEP_STATE_ARG[@]}"
)
