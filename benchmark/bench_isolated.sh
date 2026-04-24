#!/usr/bin/env bash
# Isolated benchmark harness:
#   * CPU pin (cores 0-3 by default) via taskset.
#   * Memory cap (16 GiB by default) via systemd-run --user --scope + MemoryMax.
#   * Per-run peak RSS via /usr/bin/time -v inside bench_gens.sh (already wired).
#   * Per-second cgroup memory.current sampler writes a time-series.
#   * Final memory.peak recorded before the transient cgroup is cleaned up.
#   * Host snapshot (CPU / RAM / OS / package versions) via host_info.sh.
#   * Plots (wallclock / memory / byte-identity) via plot_results.py.
#
# Env overrides:
#   MEM_CAP=16G              memory cap passed to systemd-run (e.g. 8G, 32G).
#   CPU_LIST=0-3             taskset -c list (e.g. 0, 0-3, 16-19).
#   SAMPLE_INTERVAL_S=1      memory sampler polling interval.
#   NW_ENV=...               forwarded to bench_gens.sh (conda env bin dir).
#   NW_NPSO_ENV=...          same, for the nPSO gen.
#
# Outputs (under benchmark/, alongside results.csv):
#   host_snapshot.txt        host + toolchain at run start.
#   memory_timeline.csv      ts_s,rss_bytes,peak_bytes (one row per sample).
#   memory_peak.txt          last-observed cgroup memory.peak in bytes.
#   results.csv              per-run time + RSS + hashes (produced by bench_gens.sh).
#   plots/                   rendered plots (wallclock, memory timeline, byte-identity).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BENCH="$SCRIPT_DIR/bench_gens.sh"
OUT_DIR="$REPO_ROOT/examples/benchmark"

MEM_CAP="${MEM_CAP:-16G}"
CPU_LIST="${CPU_LIST:-0-3}"
SAMPLE_INTERVAL_S="${SAMPLE_INTERVAL_S:-1}"
SCOPE_NAME="nwbench-$(date +%Y%m%dT%H%M%S)-$$"

HOST_SNAPSHOT="$OUT_DIR/host_snapshot.txt"
MEM_TIMELINE="$OUT_DIR/memory_timeline.csv"
MEM_PEAK="$OUT_DIR/memory_peak.txt"

mkdir -p "$OUT_DIR"
: > "$MEM_PEAK"
echo "ts_s,rss_bytes,peak_bytes" > "$MEM_TIMELINE"

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
  # Wait up to 10 s for the cgroup to appear.
  for _ in $(seq 1 50); do
    [ -d "$cg_dir" ] && break
    sleep 0.2
  done
  while [ -r "$cg_dir/memory.current" ]; do
    local mem peak now elapsed
    mem=$(cat "$cg_dir/memory.current" 2>/dev/null || echo "")
    peak=$(cat "$cg_dir/memory.peak" 2>/dev/null || echo "")
    if [ -z "$mem" ]; then break; fi
    if [ -n "$peak" ]; then last_peak="$peak"; fi
    now=$(date +%s.%N)
    elapsed=$(awk "BEGIN{printf \"%.3f\", $now - $start_ts}")
    echo "$elapsed,$mem,${peak:-0}" >> "$MEM_TIMELINE"
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
systemd-run --user --scope --collect --unit="$SCOPE_NAME" \
    -p MemoryMax="$MEM_CAP" \
    -- taskset -c "$CPU_LIST" "$BENCH" "$@" \
  || RC=$?

# Give the sampler a last chance to read the cgroup file before it vanishes.
sleep "$SAMPLE_INTERVAL_S"

# Kill sampler.
kill "$SAMPLER_PID" 2>/dev/null || true
wait "$SAMPLER_PID" 2>/dev/null || true

echo
echo "=== Done. RC=$RC ==="
echo "Host snapshot: $HOST_SNAPSHOT"
echo "Memory timeline: $MEM_TIMELINE"
echo "Memory peak (bytes): $(cat "$MEM_PEAK" 2>/dev/null)"
echo "Results: $OUT_DIR/results.csv"

# Render plots (wallclock / memory timeline / byte-identity).
if command -v python >/dev/null 2>&1; then
    python "$SCRIPT_DIR/plot_results.py" --bench-dir "$OUT_DIR" || true
fi

exit $RC
