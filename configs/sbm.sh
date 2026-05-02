GEN_PIPELINE="src/sbm/pipeline.sh"
GEN_REQUIRED_DIR_VAR=""
GEN_REQUIRED_DIR_FLAG=""
GEN_EXTRA_ARGS=(
    --seed "${seed}"
    --n-threads "${n_threads}"
    --timeout "${timeout_duration}"
    "${KEEP_STATE_ARG[@]}"
    "${MATCH_DEGREE_ALGORITHM_ARG[@]}"
    "${MATCH_DEGREE_MODE_ARG[@]}"
    "${REMAP_ARG[@]}"
    "${OUTLIER_MODE_ARG[@]}"
)
