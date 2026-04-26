#!/usr/bin/env bash
# Build the instrumented LFR binary used by tools/viz_check/lfr/kernel_check.py
# for byte-equality replay of the degree-sampler stage.
set -euo pipefail
cd "$(dirname "$0")"
make
