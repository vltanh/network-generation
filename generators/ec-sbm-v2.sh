GEN_PIPELINE="src/ec-sbm/v2/pipeline.sh"
GEN_REQUIRED_DIR_VAR=""
GEN_REQUIRED_DIR_FLAG=""
GEN_EXTRA_ARGS=(
    --outlier-mode "combined"
    --edge-correction "rewire"
    --algorithm "true_greedy"
    --n-threads "${n_threads}"
    --seed "${seed}"
    "${KEEP_STATE_ARG[@]}"
)
