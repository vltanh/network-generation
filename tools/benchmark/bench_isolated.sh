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
# Outputs (under examples/benchmark/, alongside results.csv):
#   host_snapshot.txt        host + toolchain at run start.
#   memory_timeline.csv      ts_s,rss_bytes,peak_bytes,gen per sample.
#   memory_peak.txt          last-observed cgroup memory.peak in bytes.
#   memory_peak_per_gen.csv  gen,peak_rss_bytes per generator block.
#   results.csv              per-run time + RSS + hashes (produced by bench_gens.sh).
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

# Pull --shield out of $@; everything else passes through to bench_gens.sh.
PASS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --shield) SHIELD=1; shift ;;
    *) PASS_ARGS+=("$1"); shift ;;
  esac
done
set -- "${PASS_ARGS[@]+"${PASS_ARGS[@]}"}"

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
echo "Results:              $OUT_DIR/results.csv"

# Render plots (wallclock / memory timeline / byte-identity).
if command -v python >/dev/null 2>&1; then
    python "$SCRIPT_DIR/plot_results.py" --bench-dir "$OUT_DIR" || true
fi

rm -f "$GEN_MARKER"
exit $RC
