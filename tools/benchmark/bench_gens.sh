#!/usr/bin/env bash
# Benchmark generators: N runs per (gen, seed), discard first K warmup runs,
# report mean/std/min/max and byte-identical check over kept runs.
#
# Each (gen, seed) block runs inside ONE shell so subprocess-level warmups
# (MATLAB engine, graph-tool import, etc.) get amortized across runs.
#
# Usage:
#   tools/benchmark/bench_gens.sh
#   tools/benchmark/bench_gens.sh --gens sbm,lfr --seeds "1 42" --runs 5 --warmup 1

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

GENS_DEFAULT="sbm,ec-sbm-v1,ec-sbm-v2,abcd,abcd+o,lfr,npso"
SEEDS_DEFAULT="1 2 3 4 5 6 7 8 9 10"
RUNS=10
WARMUP=2  # warmup runs are executed and recorded, but reported separately
OUT_BASE="/tmp/bench_gens_out"
RESULTS_FILE="$REPO_ROOT/examples/benchmark/results.csv"
INPUT_EDGELIST="examples/input/empirical_networks/networks/dnc/dnc.csv"
INPUT_CLUSTERING="examples/input/reference_clusterings/clusterings/sbm-flat-best+cc/dnc/com.csv"
# Env bin dirs prepended to PATH inside the per-gen inner shell. Default to
# the active conda env's bin so a plain `conda activate <env> && bench_gens.sh`
# works out of the box. Override by exporting NW_ENV (all gens except npso) or
# NW_NPSO_ENV (npso only) when you need separate envs.
# NW_ENV resolution chain (no machine-specific hardcodes):
#   1. caller-supplied $NW_ENV
#   2. active $CONDA_PREFIX
#   3. canonical conda env named via $NW_ENV_NAME (default "nwbench")
#      looked up via `conda env list`.
# Bench refuses to start unless the resolved bin has python with the
# required modules; see preflight() in bench_isolated.sh.
NW_ENV_NAME="${NW_ENV_NAME:-nwbench}"
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
NW_ENV="$(resolve_nw_env)"
NW_NPSO_ENV="${NW_NPSO_ENV:-$NW_ENV}"

GENS="$GENS_DEFAULT"
SEEDS="$SEEDS_DEFAULT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gens) GENS="$2"; shift 2 ;;
    --seeds) SEEDS="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    --warmup) WARMUP="$2"; shift 2 ;;
    --out-base) OUT_BASE="$2"; shift 2 ;;
    --results-file) RESULTS_FILE="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT_BASE" "$(dirname "$RESULTS_FILE")"
# Preserve rows for gens not in this run. Atomic via temp + rename so a
# Ctrl-C / kill mid-bench leaves the previous results.csv intact.
RESULTS_HEADER="gen,seed,phase,run,time_s,peak_rss_kb,edge_sha256,com_sha256"
RESULTS_TMP="$RESULTS_FILE.tmp.$$"
trap 'rm -f "$RESULTS_TMP"' EXIT
if [[ -f "$RESULTS_FILE" ]]; then
  # Drop only the rows whose gen is requested for this run; keep the rest.
  awk -F, -v gens="$GENS" '
    BEGIN { n = split(gens, a, ","); for (i=1; i<=n; i++) keep[a[i]] = 1 }
    NR == 1 { print; next }
    !($1 in keep) { print }
  ' "$RESULTS_FILE" > "$RESULTS_TMP"
else
  echo "$RESULTS_HEADER" > "$RESULTS_TMP"
fi
mv "$RESULTS_TMP" "$RESULTS_FILE"
trap - EXIT

# Run all (seed, run) combos for one gen inside a single shell so env init and
# subprocess warmups are amortized.
run_gen_block() {
  local gen="$1"
  local env_path="$NW_ENV"
  local matlab_setup=""
  if [[ "$gen" == "npso" ]]; then
    env_path="$NW_NPSO_ENV"
    # Cluster environments commonly expose MATLAB via lmod. If `module` is
    # present, try to load R2024a; otherwise assume `matlab` is already on PATH.
    matlab_setup='command -v module >/dev/null 2>&1 && module load matlab/R2024a >/dev/null 2>&1; unset LD_PRELOAD; unset LD_LIBRARY_PATH'
  fi

  zsh -l <<ZSH
set -u
cd "$REPO_ROOT"
${matlab_setup:+$matlab_setup ||:;}
export PATH="$env_path:\$PATH"
export OMP_NUM_THREADS=1
export PYTHONHASHSEED=0

TOTAL=\$(( $WARMUP + $RUNS ))
for seed in $SEEDS; do
  for r in \$(seq 1 \$TOTAL); do
    if [[ \$r -le $WARMUP ]]; then
      phase="warmup"
    else
      phase="kept"
    fi
    out_dir="$OUT_BASE/$gen"
    rm -rf "\$out_dir"
    rss_log="/tmp/bench_rss.log"
    start=\$(date +%s.%N)
    /usr/bin/time -v -o "\$rss_log" \
      ./run_generator.sh \
        --generator "$gen" \
        --network dnc \
        --clustering-id sbm-flat-best+cc \
        --run-id 0 \
        --seed \$seed \
        --input-edgelist   "$INPUT_EDGELIST" \
        --input-clustering "$INPUT_CLUSTERING" \
        --output-dir       "\$out_dir" \
        >/tmp/bench_last.log 2>&1
    rc=\$?
    end=\$(date +%s.%N)
    elapsed=\$(awk "BEGIN{printf \"%.3f\", \$end - \$start}")
    # /usr/bin/time -v measures the run_generator.sh process's peak RSS.
    # Per-stage Python children are shorter-lived, so this is a lower bound
    # on true per-gen peak memory; cgroup memory.peak (captured at the outer
    # driver) is the accurate whole-tree peak.
    peak_rss_kb=\$(awk '/Maximum resident set size/ {print \$NF}' "\$rss_log" 2>/dev/null)
    : "\${peak_rss_kb:=0}"
    edge="\$out_dir/networks/$gen/sbm-flat-best+cc/dnc/0/edge.csv"
    com="\$out_dir/networks/$gen/sbm-flat-best+cc/dnc/0/com.csv"
    if [[ \$rc -ne 0 || ! -f "\$edge" ]]; then
      echo "$gen,\$seed,\$phase,\$r,FAIL,\$peak_rss_kb,," >> "$RESULTS_FILE"
      echo "FAIL: gen=$gen seed=\$seed phase=\$phase run=\$r rc=\$rc" >&2
      tail -20 /tmp/bench_last.log >&2
      continue
    fi
    edge_h=\$(sha256sum "\$edge" | awk '{print \$1}')
    com_h=\$(sha256sum "\$com" | awk '{print \$1}')
    echo "$gen,\$seed,\$phase,\$r,\$elapsed,\$peak_rss_kb,\$edge_h,\$com_h" >> "$RESULTS_FILE"
    printf "%s\tseed=%s\t%s\trun=%d\ttime=%s\trss=%sK\tedge=%s\tcom=%s\n" \
      "$gen" "\$seed" "\$phase" "\$r" "\$elapsed" "\$peak_rss_kb" "\${edge_h:0:12}" "\${com_h:0:12}"
  done
done
ZSH
}

summarize() {
  python3 - "$RESULTS_FILE" <<'PY'
import csv, sys, math
from collections import defaultdict

fp = sys.argv[1]
warmup = defaultdict(list)  # (gen, seed) -> [time_s]
kept = defaultdict(list)
edge_hashes = defaultdict(set)
com_hashes = defaultdict(set)

with open(fp) as f:
    for row in csv.DictReader(f):
        key = (row["gen"], row["seed"])
        if row["time_s"] == "FAIL":
            print(f"FAIL: {key} phase={row['phase']} run={row['run']}")
            continue
        t = float(row["time_s"])
        if row["phase"] == "warmup":
            warmup[key].append(t)
        else:
            kept[key].append(t)
        edge_hashes[key].add(row["edge_sha256"])
        com_hashes[key].add(row["com_sha256"])

def stats(ts):
    if not ts:
        return None, None, None, None
    m = sum(ts) / len(ts)
    s = math.sqrt(sum((t - m) ** 2 for t in ts) / (len(ts) - 1)) if len(ts) > 1 else 0.0
    return m, s, min(ts), max(ts)

def fmt(x):
    return f"{x:7.3f}" if x is not None else "   -   "

# Per (gen, seed)
print()
print(f"{'gen':<12} {'seed':<5} {'warmN':>5} {'warm_mean':>10} {'keptN':>5} {'kept_mean':>10} {'kept_std':>8} {'kept_min':>8} {'kept_max':>8} {'byte':>5}  edge_sha256(12)")
print("-" * 130)
keys = sorted(set(list(warmup.keys()) + list(kept.keys())), key=lambda k: (k[0], int(k[1])))
for key in keys:
    gen, seed = key
    wm, _, _, _ = stats(warmup[key])
    km, ks, kmin, kmax = stats(kept[key])
    edge_bi = "OK" if len(edge_hashes[key]) == 1 else f"NO{len(edge_hashes[key])}"
    eh = next(iter(edge_hashes[key]))[:12] if len(edge_hashes[key]) == 1 else "MIXED"
    print(f"{gen:<12} {seed:<5} {len(warmup[key]):>5} {fmt(wm)}    {len(kept[key]):>5} {fmt(km)}    {fmt(ks)} {fmt(kmin)} {fmt(kmax)} {edge_bi:>5}  {eh}")

# Per gen (aggregate across all seeds)
print()
print(f"{'gen':<12} {'warm_mean':>10} {'warm_n':>7} {'kept_mean':>10} {'kept_std':>8} {'kept_n':>7}  all_seeds_byte_identical")
print("-" * 95)
per_gen_warm = defaultdict(list)
per_gen_kept = defaultdict(list)
per_gen_edge_sets = defaultdict(lambda: defaultdict(set))
for key, ts in warmup.items():
    per_gen_warm[key[0]].extend(ts)
for key, ts in kept.items():
    per_gen_kept[key[0]].extend(ts)
for key, h in edge_hashes.items():
    per_gen_edge_sets[key[0]][key[1]] = h
for gen in sorted(set(list(per_gen_warm) + list(per_gen_kept))):
    wm, _, _, _ = stats(per_gen_warm[gen])
    km, ks, _, _ = stats(per_gen_kept[gen])
    # byte identity within each seed
    all_ok = all(len(v) == 1 for v in per_gen_edge_sets[gen].values())
    bi_str = "yes" if all_ok else "NO"
    print(f"{gen:<12} {fmt(wm)}    {len(per_gen_warm[gen]):>7} {fmt(km)}    {fmt(ks)} {len(per_gen_kept[gen]):>7}  {bi_str}")
PY
}

echo "=== benchmark: gens=[$GENS] seeds=[$SEEDS] runs=$RUNS warmup=$WARMUP ==="
IFS=',' read -ra GEN_ARR <<< "$GENS"
# bench_isolated.sh sets NW_BENCH_GEN_MARKER so the cgroup sampler can tag
# each timeline row with the active gen. Direct invocations of bench_gens.sh
# get a stub marker that does nothing useful.
GEN_MARKER="${NW_BENCH_GEN_MARKER:-/dev/null}"
for gen in "${GEN_ARR[@]}"; do
  echo "--- gen=$gen ---"
  echo "$gen" > "$GEN_MARKER" 2>/dev/null || true
  run_gen_block "$gen"
done
# Reset marker so the prologue/epilogue rows in memory_timeline.csv read "_".
echo "" > "$GEN_MARKER" 2>/dev/null || true

echo ""
echo "=== summary (warmup=$WARMUP runs reported separately from kept=$RUNS) ==="
summarize
