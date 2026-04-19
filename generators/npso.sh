GEN_PIPELINE="src/npso/pipeline.sh"
GEN_REQUIRED_DIR_VAR="npso_dir"
GEN_REQUIRED_DIR_FLAG="--npso-dir"
GEN_EXTRA_ARGS=(
    --npso-dir "${npso_dir}"
    --seed "${seed}"
    --n-threads "${n_threads}"
    "${KEEP_STATE_ARG[@]}"
)
