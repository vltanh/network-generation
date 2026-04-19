#!/bin/bash
# Install external tool dependencies for abcd, abcd+o, lfr, npso generators.
#
# Run this once after cloning. Assumes julia (for ABCD), make + a C++ compiler
# (for LFR), and matlab (for nPSO) are available on PATH. On campus cluster:
#     module load matlab
# On systems without MATLAB, the nPSO generator will not work; the others are
# independent and can still be used.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "${SCRIPT_DIR}"

# No global `set -e` — the julia/make steps are designed to degrade
# gracefully when optional toolchains are missing.  Instead, each step
# checks its own exit code and either aborts (submodule init) or reports
# the failure and continues (julia, make).
echo "=== [1/3] Initializing external submodules ==="
if ! git submodule update --init --recursive externals/abcd externals/lfr externals/npso; then
    echo "Error: submodule init failed — aborting." >&2
    exit 1
fi

echo "=== [2/3] Registering ABCD with Julia (Pkg.develop) ==="
if command -v julia &> /dev/null; then
    if ! julia -e 'using Pkg; Pkg.develop(path="externals/abcd"); Pkg.instantiate()'; then
        echo "  Julia step failed — abcd/abcd+o generators may not work." >&2
    fi
else
    echo "  julia not found — skipping. abcd/abcd+o generators will be unavailable."
fi

echo "=== [3/3] Building LFR benchmark ==="
if command -v make &> /dev/null; then
    if ! make -C externals/lfr/unweighted_undirected; then
        echo "  LFR build failed — lfr generator will be unavailable." >&2
    elif [ -x externals/lfr/unweighted_undirected/benchmark ]; then
        echo "  LFR binary: externals/lfr/unweighted_undirected/benchmark"
    else
        echo "  LFR build produced no benchmark binary — check compiler output." >&2
    fi
else
    echo "  make not found — skipping. lfr generator will be unavailable."
fi

echo
echo "=== Install complete ==="
echo "nPSO requires MATLAB on PATH at run time (module load matlab on campus cluster)."
