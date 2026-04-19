GEN_PIPELINE="src/ec-sbm/v1/pipeline.sh"
GEN_REQUIRED_DIR_VAR=""
GEN_REQUIRED_DIR_FLAG=""
GEN_EXTRA_ARGS=(
    --n-threads "${n_threads}"
    "${KEEP_STATE_ARG[@]}"
)
