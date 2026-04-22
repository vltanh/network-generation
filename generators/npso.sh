GEN_PIPELINE="src/npso/pipeline.sh"
GEN_REQUIRED_DIR_VAR="npso_dir"
GEN_REQUIRED_DIR_FLAG="--npso-dir"

NPSO_MODEL_ARG=()
if [ -n "${npso_model:-}" ]; then
    NPSO_MODEL_ARG=(--model "${npso_model}")
fi

GEN_EXTRA_ARGS=(
    --npso-dir "${npso_dir}"
    --seed "${seed}"
    --n-threads "${n_threads}"
    --timeout "${timeout_duration}"
    "${NPSO_MODEL_ARG[@]}"
    "${KEEP_STATE_ARG[@]}"
)
