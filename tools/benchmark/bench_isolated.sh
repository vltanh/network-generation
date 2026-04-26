#!/usr/bin/env bash
# Isolated benchmark harness:
#   * CPU pin (cores 0-3 by default) via taskset.
#   * Memory cap (16 GiB by default) via systemd-run --user --scope + MemoryMax.
#   * Per-run peak RSS via /usr/bin/time -v inside bench_gens.sh (already wired).
#   * Per-second cgroup memory.current sampler writes a time-series, tagged
#     with the currently-running gen so per-gen memory can be summarised.
#   * Final memory.peak recorded before the transient cgroup is cleaned up.
#   * Host snapshot (CPU / RAM / OS / package versions) via host_info.sh.
#   * Plots (wallclock / memory / byte-identity) via plot_results.py.
#
# True CPU exclusivity:
#   * taskset pins the bench process to the requested cores. Other userspace
#     tasks can still schedule on the same cores.
#   * --shield turns on best-effort exclusivity: real-time scheduling
#     (chrt SCHED_FIFO) + real-time IO class (ionice -c 1). Both require
#     RLIMIT_RTPRIO + CAP_SYS_NICE; on a desktop the per-user limits in
#     /etc/security/limits.d/ usually grant enough.
#   * For *guaranteed* exclusivity, boot the kernel with
#     "isolcpus=0-3 nohz_full=0-3 rcu_nocbs=0-3" (requires reboot + root)
#     or run `sudo cset shield --cpu=0-3 --kthread=on` before invoking.
#
# Env overrides:
#   MEM_CAP=16G              memory cap passed to systemd-run (e.g. 8G, 32G).
#   CPU_LIST=0-3             taskset -c list (e.g. 0, 0-3, 16-19).
#   SAMPLE_INTERVAL_S=1      memory sampler polling interval.
#   NW_ENV=...               forwarded to bench_gens.sh (conda env bin dir).
#   NW_NPSO_ENV=...          same, for the nPSO gen.
#
# Outputs (under examples/benchmark/):
#   host_snapshot.txt        host + toolchain at run start.
#   memory_timeline.csv      ts_s,rss_bytes,peak_bytes,gen per sample.
#   memory_peak.txt          last-observed cgroup memory.peak in bytes.
#   memory_peak_per_gen.csv  gen,peak_rss_bytes per generator block.
#   per_gen/results_<gen>.csv  per-gen run table (time, RSS, hashes).
#   plots/                   rendered plots (wallclock, memory timeline, byte-identity).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BENCH="$SCRIPT_DIR/bench_gens.sh"
OUT_DIR="$REPO_ROOT/examples/benchmark"

MEM_CAP="${MEM_CAP:-16G}"
CPU_LIST="${CPU_LIST:-0-3}"
SAMPLE_INTERVAL_S="${SAMPLE_INTERVAL_S:-1}"
SCOPE_NAME="nwbench-$(date +%Y%m%dT%H%M%S)-$$"
SHIELD=0
SKIP_PREFLIGHT=0
NW_ENV_NAME="${NW_ENV_NAME:-nwbench}"

# Resolve NW_ENV once, here, so preflight can interrogate it. Same chain
# as bench_gens.sh; pulled here too to make `--check` work standalone.
# A bin dir "works" if its python can import graph_tool (the canonical
# sentinel for the nwbench env). Falls through to the next candidate
# otherwise so an active base conda env doesn't shadow nwbench.
_nw_env_works() {
  [[ -n "$1" && -x "$1/python" ]] || return 1
  "$1/python" -c "import graph_tool" 2>/dev/null
}
resolve_nw_env() {
  if [[ -n "${NW_ENV:-}" ]] && _nw_env_works "$NW_ENV"; then
    echo "$NW_ENV"; return
  fi
  if [[ -n "${CONDA_PREFIX:-}" ]] && _nw_env_works "$CONDA_PREFIX/bin"; then
    echo "$CONDA_PREFIX/bin"; return
  fi
  if command -v conda >/dev/null 2>&1; then
    local p
    p=$(conda env list 2>/dev/null | awk -v n="$NW_ENV_NAME" '$1==n {print $NF}')
    if [[ -n "$p" ]] && _nw_env_works "$p/bin"; then
      echo "$p/bin"; return
    fi
  fi
  echo ""
}

# Pull --shield, --check, --skip-preflight out of $@; everything else
# passes through to bench_gens.sh. We need to know the gen list at
# preflight time to pick which deps to verify, so we shadow --gens
# without consuming it from PASS_ARGS.
PASS_ARGS=()
GENS_REQ=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --shield) SHIELD=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --check) SKIP_PREFLIGHT=0; CHECK_ONLY=1; shift ;;
    --gens) GENS_REQ="$2"; PASS_ARGS+=("$1" "$2"); shift 2 ;;
    *) PASS_ARGS+=("$1"); shift ;;
  esac
done
set -- "${PASS_ARGS[@]+"${PASS_ARGS[@]}"}"
GENS_REQ="${GENS_REQ:-sbm,ec-sbm-v1,ec-sbm-v2,abcd,abcd+o,lfr,npso}"

# ---------- Preflight ----------
preflight_fail() {
  echo "preflight FAIL: $1" >&2
  echo "" >&2
  echo "fix: see tools/benchmark/BENCHMARK.md" >&2
  exit 3
}

preflight() {
  local nw_env nw_npso_env
  nw_env="$(NW_ENV="${NW_ENV:-}" CONDA_PREFIX="${CONDA_PREFIX:-}" \
           NW_ENV_NAME="$NW_ENV_NAME" resolve_nw_env)"
  if [[ -z "$nw_env" ]]; then
    preflight_fail "could not resolve NW_ENV. set NW_ENV=/path/to/conda/env/bin or activate the '$NW_ENV_NAME' conda env"
  fi
  echo "[preflight] NW_ENV=$nw_env"
  nw_npso_env="${NW_NPSO_ENV:-$nw_env}"

  command -v systemd-run >/dev/null 2>&1 || preflight_fail "systemd-run not on PATH"
  command -v taskset     >/dev/null 2>&1 || preflight_fail "taskset not on PATH"
  command -v /usr/bin/time >/dev/null 2>&1 || preflight_fail "/usr/bin/time missing (apt install time)"

  IFS=',' read -ra _GENS_ARR <<< "$GENS_REQ"
  for g in "${_GENS_ARR[@]}"; do
    case "$g" in
      sbm)
        "$nw_env/python" -c "import graph_tool, numpy, pandas, scipy" 2>/dev/null \
          || preflight_fail "$g: $nw_env/python missing graph_tool/numpy/pandas/scipy"
        ;;
      ec-sbm-v1|ec-sbm-v2)
        "$nw_env/python" -c "import graph_tool, numpy, pandas, pymincut" 2>/dev/null \
          || preflight_fail "$g: $nw_env/python missing graph_tool/numpy/pandas/pymincut"
        ;;
      abcd|abcd+o)
        command -v julia >/dev/null 2>&1 \
          || preflight_fail "$g: julia binary not on PATH"
        ;;
      lfr)
        local lfr_bin="$REPO_ROOT/externals/lfr/unweighted_undirected/benchmark"
        [[ -x "$lfr_bin" ]] \
          || preflight_fail "$g: LFR binary not built at $lfr_bin (cd externals/lfr/unweighted_undirected && make)"
        ;;
      npso)
        "$nw_npso_env/python" -c "import networkit" 2>/dev/null \
          || preflight_fail "$g: $nw_npso_env/python missing networkit"
        # MATLAB engine OR matlab on PATH for subprocess fallback.
        if ! "$nw_npso_env/python" -c "import matlab.engine" 2>/dev/null \
            && ! command -v matlab >/dev/null 2>&1; then
          preflight_fail "$g: neither matlab.engine in $nw_npso_env nor matlab on PATH"
        fi
        ;;
      *)
        preflight_fail "unknown gen '$g'"
        ;;
    esac
    echo "[preflight] $g OK"
  done

  # Input fixture present.
  [[ -f "$REPO_ROOT/examples/input/empirical_networks/networks/dnc/dnc.csv" ]] \
    || preflight_fail "input edgelist missing under examples/input/empirical_networks/"
  [[ -f "$REPO_ROOT/examples/input/reference_clusterings/clusterings/sbm-flat-best+cc/dnc/com.csv" ]] \
    || preflight_fail "input clustering missing under examples/input/reference_clusterings/"

  # Export resolved NW_ENV / NW_NPSO_ENV so bench_gens.sh inherits them.
  export NW_ENV="$nw_env"
  export NW_NPSO_ENV="$nw_npso_env"
  echo "[preflight] all checks passed"
}

if [[ "$SKIP_PREFLIGHT" != "1" ]]; then
  preflight
fi
if [[ "${CHECK_ONLY:-0}" == "1" ]]; then
  exit 0
fi

HOST_SNAPSHOT="$OUT_DIR/host_snapshot.txt"
MEM_TIMELINE="$OUT_DIR/memory_timeline.csv"
MEM_PEAK="$OUT_DIR/memory_peak.txt"
MEM_PER_GEN="$OUT_DIR/memory_peak_per_gen.csv"
# bench_gens.sh writes the current generator name here at the start of each
# block so the sampler can tag rows. Pre-create empty so the sampler does
# not block on missing file.
GEN_MARKER="/tmp/nwbench_current_gen"
: > "$GEN_MARKER"
export NW_BENCH_GEN_MARKER="$GEN_MARKER"

mkdir -p "$OUT_DIR"
: > "$MEM_PEAK"
echo "ts_s,rss_bytes,peak_bytes,gen" > "$MEM_TIMELINE"

# ---------- Host snapshot ----------
# Delegate to host_info.sh so the snapshot format stays consistent with
# standalone invocation. Env vars propagate via export.
CPU_LIST="$CPU_LIST" MEM_CAP="$MEM_CAP" SAMPLE_INTERVAL_S="$SAMPLE_INTERVAL_S" \
    "$SCRIPT_DIR/host_info.sh" > "$HOST_SNAPSHOT"

# ---------- Memory sampler (background) ----------
cg_dir="/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/app.slice/$SCOPE_NAME.scope"

sampler() {
  local start_ts
  start_ts=$(date +%s.%N)
  local last_peak=0
  for _ in $(seq 1 50); do
    [ -d "$cg_dir" ] && break
    sleep 0.2
  done
  while [ -r "$cg_dir/memory.current" ]; do
    local mem peak now elapsed gen
    mem=$(cat "$cg_dir/memory.current" 2>/dev/null || echo "")
    peak=$(cat "$cg_dir/memory.peak" 2>/dev/null || echo "")
    if [ -z "$mem" ]; then break; fi
    if [ -n "$peak" ]; then last_peak="$peak"; fi
    now=$(date +%s.%N)
    elapsed=$(awk "BEGIN{printf \"%.3f\", $now - $start_ts}")
    gen=$(cat "$GEN_MARKER" 2>/dev/null | head -1)
    echo "$elapsed,$mem,${peak:-0},${gen:-_}" >> "$MEM_TIMELINE"
    sleep "$SAMPLE_INTERVAL_S"
  done
  echo "$last_peak" > "$MEM_PEAK"
}

sampler &
SAMPLER_PID=$!

# ---------- Scope + bench ----------
# systemd-run --scope runs synchronously in the caller shell.
# --collect removes the unit immediately on exit; we rely on the sampler to
# record the final peak just before that happens (polled every SAMPLE_INTERVAL_S).
RC=0
SHIELD_PREFIX=()
if [[ "$SHIELD" == "1" ]]; then
  if command -v chrt >/dev/null 2>&1 && command -v ionice >/dev/null 2>&1; then
    # SCHED_FIFO at the lowest real-time priority preempts normal user tasks
    # on the pinned cores; ionice RT class avoids IO contention. Both need
    # RLIMIT_RTPRIO + CAP_SYS_NICE on the user. Probe by trying the chrt
    # call without committing to the bench scope so we can fall back cleanly.
    if chrt --fifo 1 true >/dev/null 2>&1; then
      SHIELD_PREFIX=(chrt --fifo 1 ionice -c 1 -n 0)
      echo "[shield] real-time priority + RT IO class active"
    else
      echo "[shield] RLIMIT_RTPRIO too low for SCHED_FIFO; skipping shield." >&2
      echo "[shield] Raise via /etc/security/limits.d/ + 'ulimit -r 99' or run with sudo." >&2
    fi
  else
    echo "[shield] chrt or ionice missing; skipping shield" >&2
  fi
fi

systemd-run --user --scope --collect --unit="$SCOPE_NAME" \
    -p MemoryMax="$MEM_CAP" \
    -p AllowedCPUs="$CPU_LIST" \
    -- "${SHIELD_PREFIX[@]}" taskset -c "$CPU_LIST" "$BENCH" "$@" \
  || RC=$?

# Give the sampler a last chance to read the cgroup file before it vanishes.
sleep "$SAMPLE_INTERVAL_S"

# Kill sampler.
kill "$SAMPLER_PID" 2>/dev/null || true
wait "$SAMPLER_PID" 2>/dev/null || true

# Per-gen peak: walk the timeline, take max(rss_bytes) per gen tag, drop
# the no-gen prologue/epilogue rows tagged "_".
{
  echo "gen,peak_rss_bytes"
  awk -F, 'NR>1 && $4!="_" && $4!="" {
    if ($2+0 > peak[$4]) peak[$4]=$2+0
  } END {
    for (g in peak) print g","peak[g]
  }' "$MEM_TIMELINE" | sort
} > "$MEM_PER_GEN"

echo
echo "=== Done. RC=$RC ==="
echo "Host snapshot:        $HOST_SNAPSHOT"
echo "Memory timeline:      $MEM_TIMELINE"
echo "Memory peak (bytes):  $(cat "$MEM_PEAK" 2>/dev/null)"
echo "Memory per-gen peak:  $MEM_PER_GEN"
echo "Per-gen results:      $OUT_DIR/per_gen/results_*.csv"

# Render plots (wallclock / memory timeline / byte-identity).
if command -v python >/dev/null 2>&1; then
    python "$SCRIPT_DIR/plot_results.py" --bench-dir "$OUT_DIR" || true
fi

rm -f "$GEN_MARKER"
exit $RC
