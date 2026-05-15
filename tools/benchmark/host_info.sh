#!/usr/bin/env bash
# Dump host + toolchain info to stdout. Hardware / OS versions + the
# Python / Julia / MATLAB Engine packages the pipelines depend on.
# No absolute paths, no git state, no working-tree listing.
#
# Usage:
#   tools/benchmark/host_info.sh                  # defaults: cpu_pin/mem_cap/sample_interval unset
#   tools/benchmark/host_info.sh > snapshot.txt
#
# Env overrides (shown verbatim under "Run config" if set):
#   CPU_LIST               taskset pin description.
#   MEM_CAP                memory cap string.
#   SAMPLE_INTERVAL_S      cgroup sampler interval.
set -euo pipefail

CPU_LIST="${CPU_LIST:-}"
MEM_CAP="${MEM_CAP:-}"
SAMPLE_INTERVAL_S="${SAMPLE_INTERVAL_S:-}"
NW_ENV="${NW_ENV:-}"
NW_NPSO_ENV="${NW_NPSO_ENV:-$NW_ENV}"

resolve_python() {
    local bin_dir="$1"
    if [ -n "${bin_dir}" ] && [ -x "${bin_dir}/python" ]; then
        printf '%s\n' "${bin_dir}/python"
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        command -v python
        return 0
    fi
    return 1
}

PYTHON_BIN="$(resolve_python "${NW_ENV}" || true)"
NPSO_PYTHON_BIN="$(resolve_python "${NW_NPSO_ENV}" || true)"

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
if [ -n "${PYTHON_BIN}" ]; then
    probe_version "python"        "${PYTHON_BIN}" -c 'import sys; print(sys.version.split()[0])'
    probe_version "graph-tool"    "${PYTHON_BIN}" -c 'import graph_tool; print(graph_tool.__version__)'
    probe_version "numpy"         "${PYTHON_BIN}" -c 'import numpy; print(numpy.__version__)'
    probe_version "pandas"        "${PYTHON_BIN}" -c 'import pandas; print(pandas.__version__)'
    probe_version "powerlaw"      "${PYTHON_BIN}" -c 'import powerlaw; print(powerlaw.__version__)'
else
    echo "python missing"
    echo "graph-tool missing"
    echo "numpy missing"
    echo "pandas missing"
    echo "powerlaw missing"
fi

if [ -n "${NPSO_PYTHON_BIN}" ]; then
    probe_version "networkit"     "${NPSO_PYTHON_BIN}" -c 'import networkit; print(networkit.__version__)'
    probe_version "matlab.engine" "${NPSO_PYTHON_BIN}" -c 'from importlib.metadata import version; print(version("matlabengine"))'
else
    echo "networkit missing"
    echo "matlab.engine missing"
fi
probe_version "julia"         julia --version
