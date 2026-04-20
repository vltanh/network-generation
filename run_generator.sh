#!/bin/bash

# Constants
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
if [[ "${SCRIPT_DIR}" == *"/slurmd/job"* ]]; then
    SCRIPT_DIR="${SLURM_SUBMIT_DIR}"
fi

# ==========================================
# Helper Functions: Logging & State
# ==========================================

# Prepend a timestamp to every message. Use this instead of echo throughout
# the script so all output is consistently timestamped.
log() {
    builtin echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

# ==========================================
# Argument Parsing
# ==========================================
generator=""
network_id=""
clustering_id=""
run_id=""
is_macro=0

custom_inp_edge=""
custom_inp_com=""
custom_out_dir=""
custom_emp_network_stats=""
custom_ref_cluster_stats=""

run_stats_flag=0
run_comp_flag=0
keep_state=0

seed=1
n_threads=1
abcd_dir="${SCRIPT_DIR}/externals/abcd"
lfr_binary="${SCRIPT_DIR}/externals/lfr/unweighted_undirected/benchmark"
npso_dir="${SCRIPT_DIR}/externals/npso"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --generator) generator="$2"; shift 2 ;;
        --network) network_id="$2"; shift 2 ;;
        --clustering-id) clustering_id="$2"; shift 2 ;;
        --run-id) run_id="$2"; shift 2 ;;
        --macro) is_macro=1; shift 1 ;;
        --input-edgelist) custom_inp_edge="$2"; shift 2 ;;
        --input-clustering) custom_inp_com="$2"; shift 2 ;;
        --output-dir) custom_out_dir="$2"; shift 2 ;;
        --input-network-stats) custom_emp_network_stats="$2"; shift 2 ;;
        --input-cluster-stats) custom_ref_cluster_stats="$2"; shift 2 ;;
        --run-stats) run_stats_flag=1; shift 1 ;;
        --run-comp) run_comp_flag=1; shift 1 ;;
        --keep-state) keep_state=1; shift 1 ;;
        --seed) seed="$2"; shift 2 ;;
        --n-threads) n_threads="$2"; shift 2 ;;
        --abcd-dir) abcd_dir="$2"; shift 2 ;;
        --lfr-binary) lfr_binary="$2"; shift 2 ;;
        --npso-dir) npso_dir="$2"; shift 2 ;;
        -*) log "Unknown parameter passed: $1"; exit 1 ;;
        *) log "Unexpected argument: $1"; exit 1 ;;
    esac
done

# Validation for required generic arguments
if [ -z "${generator}" ]; then
    log "Error: --generator is a required parameter."
    exit 1
fi

if [ -z "${run_id}" ]; then
    log "Error: --run-id is a required parameter."
    exit 1
fi

GENERATORS_DIR="${SCRIPT_DIR}/generators"
ACCEPTED_GENERATORS=()
for cfg in "${GENERATORS_DIR}"/*.sh; do
    # -f requires a regular file (not a directory); dereferences symlinks
    # and returns false on dangling ones.
    [ -f "${cfg}" ] || continue
    ACCEPTED_GENERATORS+=("$(basename "${cfg}" .sh)")
done

if [[ ! " ${ACCEPTED_GENERATORS[*]} " =~ " ${generator} " ]]; then
    log "Error: Unsupported generator '${generator}'. Accepted generators are: ${ACCEPTED_GENERATORS[*]}"
    exit 1
fi

# ==========================================
# Input/Output Path Routing (Unified)
# ==========================================
if [ "${is_macro}" -eq 1 ]; then
    if [ -z "${network_id}" ] || [ -z "${clustering_id}" ]; then
        log "Error: --network and --clustering-id are required when using --macro."
        exit 1
    fi
    
    INP_EDGE="data/empirical_networks/networks/${network_id}/${network_id}.csv"
    INP_COM="data/reference_clusterings/clusterings/${clustering_id}/${network_id}/com.csv"
    
    OUT_DIR="data/synthetic_networks/networks/${generator}/${clustering_id}/${network_id}/${run_id}"
    STATS_DIR="data/synthetic_networks/stats/${generator}/${clustering_id}/${network_id}/${run_id}"
    
    EMPIRICAL_NETWORK_STATS_DIR="data/empirical_networks/stats/${network_id}"
    REFERENCE_STATS_DIR="data/reference_clusterings/stats/${clustering_id}/${network_id}"
    
    dataset_name="${network_id} (Clustering: ${clustering_id}, Run: ${run_id})"
else
    if [ -z "${custom_inp_edge}" ] || [ -z "${custom_inp_com}" ] || [ -z "${custom_out_dir}" ]; then
        log "Error: In custom mode, you must provide --input-edgelist, --input-clustering, and --output-dir."
        exit 1
    fi
    
    if [ "${run_comp_flag}" -eq 1 ]; then
        if [ -z "${custom_emp_network_stats}" ] || [ -z "${custom_ref_cluster_stats}" ]; then
            log "Error: --run-comp requires --input-network-stats and --input-cluster-stats in custom mode."
            exit 1
        fi
    fi
    
    INP_EDGE="${custom_inp_edge}"
    INP_COM="${custom_inp_com}"
    
    # Dynamically build the trailing subpath for custom mode
    opt_subpath="${clustering_id:+/${clustering_id}}${network_id:+/${network_id}}/${run_id}"
    
    OUT_DIR="${custom_out_dir}/networks/${generator}${opt_subpath}"
    STATS_DIR="${custom_out_dir}/stats/${generator}${opt_subpath}"
    
    EMPIRICAL_NETWORK_STATS_DIR="${custom_emp_network_stats}"
    REFERENCE_STATS_DIR="${custom_ref_cluster_stats}"
    
    # Build a clean log display string
    dataset_name="[Custom] ${network_id:+"${network_id} "}${clustering_id:+"(Clustering: ${clustering_id}) "}(Run: ${run_id})"
fi

if [ ! -f "${INP_EDGE}" ]; then log "CRITICAL: Input network missing: ${INP_EDGE}"; exit 1; fi
if [ ! -f "${INP_COM}" ]; then log "CRITICAL: Input clustering missing: ${INP_COM}"; exit 1; fi

# Sniff delimiter from the header: tab if present, else comma.  An awk -F','
# on a TSV silently reports 0 singletons because `$2` captures the whole line.
if head -1 "${INP_COM}" | grep -q $'\t'; then
    _com_fs=$'\t'
else
    _com_fs=','
fi
singleton_count=$(awk -F"${_com_fs}" 'NR>1 {c[$2]++} END {n=0; for (k in c) if (c[k]==1) n++; print n}' "${INP_COM}")
if [ "${singleton_count}" -gt 0 ]; then
    log "WARNING: Input clustering contains ${singleton_count} singleton cluster(s). Generators that reuse the reference (sbm, ec-sbm-v1, ec-sbm-v2) will propagate them; strip them beforehand for consistency with the new-clustering generators."
fi

SYNTH_CLUSTER_STATS_DIR="${STATS_DIR}/cluster"
SYNTH_NETWORK_STATS_DIR="${STATS_DIR}/network"

# ==========================================
# Evaluation Functions
# ==========================================
# Compute cluster-quality statistics for a generated network.
# No-ops silently when --run-stats was not passed (run_stats_flag=0).
# Non-zero exit from the Python script is logged as an ERROR but does not
# stop the outer script (soft failure).
# Args: <edge_file> <com_file> <stats_dir>
run_cluster_stats() {
    if [ "${run_stats_flag}" -eq 0 ]; then return; fi
    local edge_file=$1; local com_file=$2; local stats_dir=$3
    
    log "Evaluating synthetic cluster stats state via Python StateTracker..."
    mkdir -p "${stats_dir}"
    
    { /usr/bin/time -v python "${SCRIPT_DIR}/network_evaluation/network_stats/compute_cluster_stats.py" \
        --network "${edge_file}" \
        --community "${com_file}" \
        --outdir "${stats_dir}"; } 2> "${stats_dir}/error.log"
        
    if [ ${?} -ne 0 ]; then
        log "ERROR: Cluster stats computation failed."
    else
        log "Cluster stats evaluation complete."
    fi
}

# Compute graph-level statistics for a generated network.
# Same no-op and soft-failure contract as run_cluster_stats.
# Args: <edge_file> <stats_dir>
run_network_stats() {
    if [ "${run_stats_flag}" -eq 0 ]; then return; fi
    local edge_file=$1; local stats_dir=$2
    
    log "Evaluating synthetic network stats state via Python StateTracker..."
    mkdir -p "${stats_dir}"
    
    { /usr/bin/time -v python "${SCRIPT_DIR}/network_evaluation/network_stats/compute_network_stats.py" \
        --network "${edge_file}" \
        --outdir "${stats_dir}"; } 2> "${stats_dir}/error.log"
        
    if [ ${?} -ne 0 ]; then
        log "ERROR: Network stats computation failed."
    else
        log "Network stats evaluation complete."
    fi
}

# Compare synthetic vs. reference statistics (cluster and network level).
# No-ops silently when --run-comp was not passed (run_comp_flag=0).
# Requires all four stat directories to exist; warns and skips if any are
# absent rather than exiting (soft failure).
# Args: <synth_cluster_stats> <ref_cluster_stats> <synth_network_stats> <ref_network_stats> <out_dir>
run_comparison() {
    if [ "${run_comp_flag}" -eq 0 ]; then return; fi
    local synth_c_stats=$1; local ref_c_stats=$2
    local synth_n_stats=$3; local ref_n_stats=$4
    local out_dir=$5
    
    if [ -d "${synth_c_stats}" ] && [ -d "${ref_c_stats}" ] && \
       [ -d "${synth_n_stats}" ] && [ -d "${ref_n_stats}" ]; then
        
        log "Running statistics comparison..."
        mkdir -p "${out_dir}"
        
        { /usr/bin/time -v python "${SCRIPT_DIR}/network_evaluation/compare/compare_pair.py" \
            --cluster-1-folder "${synth_c_stats}" \
            --cluster-2-folder "${ref_c_stats}" \
            --network-1-folder "${synth_n_stats}" \
            --network-2-folder "${ref_n_stats}" \
            --output-file "${out_dir}/comparison.csv" \
            --is-compare-sequence; } 2> "${out_dir}/error.log"
            
        if [ ${?} -ne 0 ]; then
            log "ERROR: Statistics comparison failed."
        else
            log "Statistics comparison complete."
        fi
    else
        log "Warning: Skipping comparison. One or more stat directories do not exist."
        log "  - Synth Cluster Stats: ${synth_c_stats}"
        log "  - Synth Network Stats: ${synth_n_stats}"
        log "  - Ref Cluster Stats:   ${ref_c_stats}"
        log "  - Ref Network Stats:   ${ref_n_stats}"
    fi
}

# ==========================================
# Orchestration
# ==========================================
log "============================"
log "Running: ${generator} on ${dataset_name}"

# ==========================================
# 1. Run Generation Pipeline
# ==========================================
log "Evaluating synthetic network generation state..."

# Source the per-generator config (see generators/README.md). Each config
# declares: GEN_PIPELINE, GEN_REQUIRED_DIR_VAR, GEN_REQUIRED_DIR_FLAG,
# GEN_EXTRA_ARGS. GEN_EXTRA_ARGS is evaluated here, so it can reference
# parsed CLI vars like ${seed}, ${n_threads}, ${abcd_dir}.
GEN_PIPELINE=""
GEN_REQUIRED_DIR_VAR=""
GEN_REQUIRED_DIR_FLAG=""
GEN_EXTRA_ARGS=()
# Available to gen configs as `"${KEEP_STATE_ARG[@]}"` — expands to
# `--keep-state` when the user passed it, otherwise to nothing.
KEEP_STATE_ARG=()
if [ "${keep_state}" -eq 1 ]; then
    KEEP_STATE_ARG=(--keep-state)
fi
# shellcheck source=/dev/null
source "${GENERATORS_DIR}/${generator}.sh"

if [ -n "${GEN_REQUIRED_DIR_VAR}" ] && [ -z "${!GEN_REQUIRED_DIR_VAR}" ]; then
    log "Error: ${GEN_REQUIRED_DIR_FLAG} is required for generator '${generator}'."
    exit 1
fi

mkdir -p "${OUT_DIR}"
"${SCRIPT_DIR}/${GEN_PIPELINE}" \
    --input-edgelist "${INP_EDGE}" \
    --input-clustering "${INP_COM}" \
    --output-dir "${OUT_DIR}" \
    "${GEN_EXTRA_ARGS[@]}"

if [ ! -f "${OUT_DIR}/edge.csv" ]; then
    log "CRITICAL: Generation failed or timed out."
    exit 1
fi

# ==========================================
# 2. Run Statistics & Comparisons
# ==========================================
run_cluster_stats "${OUT_DIR}/edge.csv" "${OUT_DIR}/com.csv" "${SYNTH_CLUSTER_STATS_DIR}"

run_network_stats "${OUT_DIR}/edge.csv" "${SYNTH_NETWORK_STATS_DIR}"

run_comparison "${SYNTH_CLUSTER_STATS_DIR}" "${REFERENCE_STATS_DIR}" \
               "${SYNTH_NETWORK_STATS_DIR}" "${EMPIRICAL_NETWORK_STATS_DIR}" \
               "${STATS_DIR}"

log "Process completed for ${generator} on ${dataset_name}"
log "[gen] ${generator} ${network_id:-custom} ${clustering_id:-custom} ${run_id}" >> complete.log