#!/usr/bin/env bash
# Isolated benchmark harness:
#   * CPU pin (cores 0-3 by default) via taskset.
#   * Memory cap (16 GiB by default) via systemd-run --user --scope + MemoryMax.
#   * Per-run peak RSS via /usr/bin/time -v inside bench_gens.sh (already wired).
#   * Per-second cgroup memory.current sampler writes a time-series.
#   * Final memory.peak recorded before the transient cgroup is cleaned up.
#   * Host snapshot (CPU / RAM / OS / package versions / git HEAD) saved.
#
# Env overrides:
#   MEM_CAP=16G              memory cap passed to systemd-run (e.g. 8G, 32G).
#   CPU_LIST=0-3             taskset -c list (e.g. 0, 0-3, 16-19).
#   SAMPLE_INTERVAL_S=1      memory sampler polling interval.
#   NW_ENV=...               forwarded to bench_gens.sh (conda env bin dir).
#   NW_NPSO_ENV=...          same, for the nPSO gen.
#
# Outputs (under scripts/benchmark/, alongside results.csv):
#   host_snapshot.txt        host + toolchain at run start.
#   memory_timeline.csv      ts_s,rss_bytes,peak_bytes (one row per sample).
#   memory_peak.txt          last-observed cgroup memory.peak in bytes.
#   results.csv              per-run time + RSS + hashes (produced by bench_gens.sh).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BENCH="$REPO_ROOT/scripts/benchmark/bench_gens.sh"
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
{
  echo "=== Timestamp ==="; date -u -Iseconds
  echo
  echo "=== CPU pin ==="; echo "$CPU_LIST"
  echo "=== Mem cap ==="; echo "$MEM_CAP"
  echo "=== Sample interval (s) ==="; echo "$SAMPLE_INTERVAL_S"
  echo
  echo "=== lscpu (summary) ==="
  lscpu | grep -E "Model name|Thread|Core|Socket|NUMA|CPU max MHz|^CPU\(s\)"
  echo
  echo "=== free -h ==="
  free -h
  echo
  echo "=== /etc/os-release ==="
  head -3 /etc/os-release
  echo
  echo "=== Kernel ==="
  uname -a
  echo
  echo "=== git HEAD ==="
  (cd "$REPO_ROOT" && git rev-parse HEAD) 2>/dev/null
  (cd "$REPO_ROOT" && git status --short) 2>/dev/null
  echo
  echo "=== Toolchain ==="
  which python 2>&1; python --version 2>&1
  python -c "import graph_tool; print('graph-tool', graph_tool.__version__)" 2>&1 || true
  python -c "import numpy, pandas; print('numpy', numpy.__version__); print('pandas', pandas.__version__)" 2>&1 || true
  python -c "import networkit, powerlaw; print('networkit', networkit.__version__); print('powerlaw', powerlaw.__version__)" 2>&1 || true
  python -c "import matlab.engine; print('matlabengine ok')" 2>&1 || true
  julia --version 2>&1 || true
  matlab -batch "disp(version); quit" 2>&1 | head -1 || true
} > "$HOST_SNAPSHOT"

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
exit $RC
