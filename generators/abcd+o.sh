GEN_PIPELINE="src/abcd+o/pipeline.sh"
GEN_REQUIRED_DIR_VAR="abcd_dir"
GEN_REQUIRED_DIR_FLAG="--abcd-dir"
GEN_EXTRA_ARGS=(
    --abcd-dir "${abcd_dir}"
    --seed "${seed}"
    --n-threads "${n_threads}"
    "${KEEP_STATE_ARG[@]}"
)
