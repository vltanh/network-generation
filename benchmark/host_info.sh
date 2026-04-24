#!/usr/bin/env bash
# Dump host + toolchain info to stdout. Hardware / OS versions + the
# Python / Julia / MATLAB Engine packages the pipelines depend on.
# No absolute paths, no git state, no working-tree listing.
#
# Usage:
#   benchmark/host_info.sh                  # defaults: cpu_pin/mem_cap/sample_interval unset
#   benchmark/host_info.sh > snapshot.txt
#
# Env overrides (shown verbatim under "Run config" if set):
#   CPU_LIST               taskset pin description.
#   MEM_CAP                memory cap string.
#   SAMPLE_INTERVAL_S      cgroup sampler interval.
set -euo pipefail

CPU_LIST="${CPU_LIST:-}"
MEM_CAP="${MEM_CAP:-}"
SAMPLE_INTERVAL_S="${SAMPLE_INTERVAL_S:-}"

probe_version() {
    # Usage: probe_version <label> <cmd...>; prints "label version" or
    # "label missing". Picks the last numeric-looking token from stdout so
    # outputs like "julia version 1.12.6" or "2.98 (commit c96a6bf3, )"
    # land as a bare version.
    local label="$1"; shift
    local out
    if out="$("$@" 2>/dev/null)"; then
        local ver
        ver="$(printf '%s\n' "$out" | awk '{
            for (i = NF; i >= 1; i--)
                if ($i ~ /^[0-9]+(\.[0-9]+)*/) { print $i; exit }
            print $1
        }')"
        printf '%s %s\n' "$label" "${ver:-unknown}"
    else
        printf '%s missing\n' "$label"
    fi
}

echo "=== Timestamp ==="
date -u -Iseconds

if [ -n "$CPU_LIST$MEM_CAP$SAMPLE_INTERVAL_S" ]; then
    echo
    echo "=== Run config ==="
    [ -n "$CPU_LIST" ]          && echo "cpu_pin=$CPU_LIST"
    [ -n "$MEM_CAP" ]           && echo "mem_cap=$MEM_CAP"
    [ -n "$SAMPLE_INTERVAL_S" ] && echo "sample_interval_s=$SAMPLE_INTERVAL_S"
fi

echo
echo "=== CPU ==="
lscpu | grep -E "Model name|Thread|Core|Socket|NUMA|CPU max MHz|^CPU\(s\)"

echo
echo "=== Memory ==="
free -h

echo
echo "=== OS ==="
head -3 /etc/os-release
uname -rsm

echo
echo "=== Toolchain ==="
probe_version "python"        python -c 'import sys; print(sys.version.split()[0])'
probe_version "graph-tool"    python -c 'import graph_tool; print(graph_tool.__version__)'
probe_version "numpy"         python -c 'import numpy; print(numpy.__version__)'
probe_version "pandas"        python -c 'import pandas; print(pandas.__version__)'
probe_version "networkit"     python -c 'import networkit; print(networkit.__version__)'
probe_version "powerlaw"      python -c 'import powerlaw; print(powerlaw.__version__)'
probe_version "matlab.engine" python -c 'from importlib.metadata import version; print(version("matlabengine"))'
probe_version "julia"         julia --version
