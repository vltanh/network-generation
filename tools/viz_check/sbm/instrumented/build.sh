#!/usr/bin/env bash
# Build the instrumented sbm canonical reference. Output binary lives at
# /tmp/sbm_kernel_check; kernel_check.py reads --cpp-binary from there.
#
# The reference uses std::mt19937, not graph-tool's pcg64_k1024. It mirrors
# the gen_sbm template's algorithm structure verbatim (UrnSampler<_, false>,
# row-major upper-triangle pair iteration, micro_ers + micro_degs path) but
# its specific edges differ from gt's because the PRNG family differs. The
# JS replay byte-equals THIS binary's output, not gt's.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
g++ -std=c++17 -O2 -o /tmp/sbm_kernel_check "$HERE/kernel_check.cpp"
echo "built: /tmp/sbm_kernel_check"
