GEN_PIPELINE="src/ec-sbm/pipeline.sh"
GEN_REQUIRED_DIR_VAR="ec_sbm_dir"
GEN_REQUIRED_DIR_FLAG="--ec-sbm-dir"
GEN_EXTRA_ARGS=(
    --package-dir "${ec_sbm_dir}"
    --version v1
    --n-threads "${n_threads}"
    --seed "${seed}"
    --timeout "${timeout_duration}"
    "${KEEP_STATE_ARG[@]}"
)
