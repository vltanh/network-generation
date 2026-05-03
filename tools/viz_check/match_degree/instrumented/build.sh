#!/usr/bin/env bash
# Build the standalone match-degree CP-rewire reference. Output binary
# lives at /tmp/md_cprewire_kernel_check; the harness reads --cpp-binary
# from there.
#
# The reference uses std::mt19937, not graph-tool's pcg64_k1024. It
# mirrors the matcher.html JS spec (which the canonical Python's
# match_missing_degrees_cluster_preserving_rewire shares modulo gt's RNG):
# per-bp weighted-random stub pair sample + Fisher-Yates shuffle + 2-opt
# 10-pass repair via cluster_preserving_2opt_rewire's algorithm.
#
# The JS replay at tools/viz_check/match_degree/kernel_check.mjs consumes
# THIS binary's trace and reproduces edges byte-for-byte. Byte-equality
# vs gt.generate_sbm itself is out of scope — gt's pcg64_k1024 +
# std::uniform_int_distribution combo is not portable across libc++ /
# libstdc++ versions and is documented as such on the matcher page.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
g++ -std=c++17 -O2 -o /tmp/md_cprewire_kernel_check "$HERE/cprewire_kernel_check.cpp"
echo "built: /tmp/md_cprewire_kernel_check"
