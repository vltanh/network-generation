GEN_PIPELINE="src/ec-sbm/pipeline.sh"
GEN_REQUIRED_DIR_VAR="ec_sbm_dir"
GEN_REQUIRED_DIR_FLAG="--ec-sbm-dir"
GEN_EXTRA_ARGS=(
    --package-dir "${ec_sbm_dir}"
    --no-sbm-overlay
    --scope "all"
    --gen-outlier-mode "combined"
    --edge-correction "rewire"
    --match-degree-algorithm "hybrid"
    --n-threads "${n_threads}"
    --seed "${seed}"
    --timeout "${timeout_duration}"
    "${KEEP_STATE_ARG[@]}"
)
