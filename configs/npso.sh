GEN_PIPELINE="src/npso/pipeline.sh"
GEN_REQUIRED_DIR_VAR="npso_dir"
GEN_REQUIRED_DIR_FLAG="--npso-dir"

NPSO_MODEL_ARG=()
if [ -n "${npso_model:-}" ]; then
    NPSO_MODEL_ARG=(--model "${npso_model}")
fi

NPSO_SEARCH_ARG=()
if [ -n "${npso_search_max_iters:-}" ]; then
    NPSO_SEARCH_ARG+=(--search-max-iters "${npso_search_max_iters}")
fi
if [ -n "${npso_search_diff_tol:-}" ]; then
    NPSO_SEARCH_ARG+=(--search-diff-tol "${npso_search_diff_tol}")
fi
if [ -n "${npso_search_step_tol:-}" ]; then
    NPSO_SEARCH_ARG+=(--search-step-tol "${npso_search_step_tol}")
fi
if [ -n "${npso_search_t_min:-}" ]; then
    NPSO_SEARCH_ARG+=(--search-t-min "${npso_search_t_min}")
fi

GEN_EXTRA_ARGS=(
    --package-dir "${npso_dir}"
    --seed "${seed}"
    --n-threads "${n_threads}"
    --timeout "${timeout_duration}"
    "${NPSO_MODEL_ARG[@]}"
    "${NPSO_SEARCH_ARG[@]}"
    "${KEEP_STATE_ARG[@]}"
)
