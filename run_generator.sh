#!/bin/bash

# Constants
TIMEOUT="3d"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"

# ==========================================
# Helper Functions: Logging & State
# ==========================================
log() {
    builtin echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

is_step_done() {
    local done_file="$1"
    if [ ! -f "${done_file}" ]; then return 1; fi
    if ! sha256sum --status -c "${done_file}" 2>/dev/null; then
        log "State change detected. Recomputing..."
        return 1
    fi
    return 0
}

mark_done() {
    local done_file="$1"
    local stage_name="$2"
    read -r -a inputs <<< "$3"
    local out_dir="$4"
    
    local tmp_done="${done_file}.tmp.$$"
    
    sha256sum "${inputs[@]}" > "${tmp_done}"
    find "${out_dir}" -maxdepth 1 -type f ! -name "$(basename "${done_file}")" ! -name "$(basename "${tmp_done}")" -exec sha256sum {} + >> "${tmp_done}"
    
    mv "${tmp_done}" "${done_file}"
    log "Success [${stage_name}]: I/O hashes recorded atomically."
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

ACCEPTED_GENERATORS=("ec-sbm-v2" "ec-sbm-v2-SDG" "ec-sbm-v1.5")
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

SYNTH_CLUSTER_STATS_DIR="${STATS_DIR}/cluster"
SYNTH_NETWORK_STATS_DIR="${STATS_DIR}/network"

# ==========================================
# Orchestration
# ==========================================
log "============================"
log "Running: ${generator} on ${dataset_name}"

# ==========================================
# 1. Run Generation Pipeline
# ==========================================
log "Evaluating synthetic network generation state..."

if [[ "${generator}" == ec-sbm-v2* ]]; then
    # Generator Configuration Parsing
    if [[ "${generator}" == "ec-sbm-v2" ]]; then
        OUTLIER_MODE="combined"
        EDGE_CORRECTION="rewire"
        MATCH_ALGO="true_greedy"
    elif [[ "${generator}" == "ec-sbm-v2-SDG" ]]; then
        OUTLIER_MODE="singleton"
        EDGE_CORRECTION="drop"
        MATCH_ALGO="greedy"
    else
        OUTLIER_MODE="combined"
        EDGE_CORRECTION="rewire"
        MATCH_ALGO="true_greedy"
    fi

    mkdir -p "${OUT_DIR}"
    "${SCRIPT_DIR}/src/ec-sbm/v2/pipeline.sh" \
        --input-edgelist "${INP_EDGE}" \
        --input-clustering "${INP_COM}" \
        --output-dir "${OUT_DIR}" \
        --outlier-mode "${OUTLIER_MODE}" \
        --edge-correction "${EDGE_CORRECTION}" \
        --algorithm "${MATCH_ALGO}"
elif [[ "${generator}" == "ec-sbm-v1.5" ]]; then
    mkdir -p "${OUT_DIR}"
    "${SCRIPT_DIR}/src/ec-sbm/v1.5/pipeline.sh" \
        --input-edgelist "${INP_EDGE}" \
        --input-clustering "${INP_COM}" \
        --output-dir "${OUT_DIR}"
fi

if [ ! -f "${OUT_DIR}/edge.csv" ]; then
    log "CRITICAL: Generation failed or timed out."
    exit 1
fi

# ==========================================
# 2. Run Statistics (Synthetic)
# ==========================================
if [ "${run_stats_flag}" -eq 1 ]; then
    log "Evaluating statistics state..."
    
    # 1. Compute Cluster-Dependent Stats
    if ! is_step_done "${SYNTH_CLUSTER_STATS_DIR}/done"; then
        log "Computing Cluster Stats..."
        mkdir -p "${SYNTH_CLUSTER_STATS_DIR}"
        { /usr/bin/time -v python "${SCRIPT_DIR}/network_evaluation/network_stats/compute_cluster_stats.py" \
            --network "${OUT_DIR}/edge.csv" \
            --community "${INP_COM}" \
            --outdir "${SYNTH_CLUSTER_STATS_DIR}"; } 2> "${SYNTH_CLUSTER_STATS_DIR}/cluster_time.log"
            
        mark_done "${SYNTH_CLUSTER_STATS_DIR}/done" "Synth Cluster Stats" "${OUT_DIR}/edge.csv ${INP_COM}" "${SYNTH_CLUSTER_STATS_DIR}"
    else
        log "Cluster stats already up-to-date."
    fi

    # 2. Compute Network-Only Stats
    if ! is_step_done "${SYNTH_NETWORK_STATS_DIR}/done"; then
        log "Computing Network Stats..."
        mkdir -p "${SYNTH_NETWORK_STATS_DIR}"
        { /usr/bin/time -v python "${SCRIPT_DIR}/network_evaluation/network_stats/compute_network_stats.py" \
            --network "${OUT_DIR}/edge.csv" \
            --outdir "${SYNTH_NETWORK_STATS_DIR}"; } 1> "${SYNTH_NETWORK_STATS_DIR}/out.log" 2> "${SYNTH_NETWORK_STATS_DIR}/network_time.log"
            
        mark_done "${SYNTH_NETWORK_STATS_DIR}/done" "Synth Network Stats" "${OUT_DIR}/edge.csv" "${SYNTH_NETWORK_STATS_DIR}"
    else
        log "Network stats already up-to-date."
    fi
fi

# ==========================================
# 3. Compare Statistics
# ==========================================
if [ "${run_comp_flag}" -eq 1 ]; then
    if [ -d "${SYNTH_CLUSTER_STATS_DIR}" ] && [ -d "${REFERENCE_STATS_DIR}" ] && \
       [ -d "${SYNTH_NETWORK_STATS_DIR}" ] && [ -d "${EMPIRICAL_NETWORK_STATS_DIR}" ]; then
        
        if ! is_step_done "${STATS_DIR}/done"; then
            log "Comparing stats..."
            mkdir -p "${STATS_DIR}"
            { /usr/bin/time -v python "${SCRIPT_DIR}/network_evaluation/compare/compare_pair.py" \
                --cluster-1-folder "${SYNTH_CLUSTER_STATS_DIR}" \
                --cluster-2-folder "${REFERENCE_STATS_DIR}" \
                --network-1-folder "${SYNTH_NETWORK_STATS_DIR}" \
                --network-2-folder "${EMPIRICAL_NETWORK_STATS_DIR}" \
                --output-file "${STATS_DIR}/comparison.csv" \
                --is-compare-sequence; } 1> "${STATS_DIR}/out.log" 2> "${STATS_DIR}/error.log"
            
            # Use upstream ledgers as dependencies
            mark_done "${STATS_DIR}/done" "Comparison" "${SYNTH_CLUSTER_STATS_DIR}/done ${SYNTH_NETWORK_STATS_DIR}/done" "${STATS_DIR}"
        else
            log "Statistics comparison already up-to-date."
        fi
    else
        log "Warning: Skipping comparison. One or more stat directories do not exist."
        log "  - Synth Cluster Stats: ${SYNTH_CLUSTER_STATS_DIR}"
        log "  - Synth Network Stats: ${SYNTH_NETWORK_STATS_DIR}"
        log "  - Ref Cluster Stats:   ${REFERENCE_STATS_DIR}"
        log "  - Ref Network Stats:   ${EMPIRICAL_NETWORK_STATS_DIR}"
    fi

    if [ ! -f "${STATS_DIR}/comparison.csv" ]; then
        log "CRITICAL: Comparison failed or timed out."
        exit 1
    fi
fi

log "Process completed for ${generator} on ${dataset_name}"
log "[gen] ${generator} ${network_id:-custom} ${clustering_id:-custom} ${run_id}" >> complete.log