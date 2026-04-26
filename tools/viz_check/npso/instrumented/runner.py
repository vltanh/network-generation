"""Drive the instrumented MATLAB nPSO sampler via the matlab.engine module.

CLI: reads ``{N, m, T, gamma, C, model, weights, seed, npso_dir}`` JSON on
stdin, writes the instrumented_npso() JSON blob on stdout.

The first call pays a one-time MATLAB engine startup cost (~5-10s). For
batch usage, prefer driving instrumented_npso() through a long-lived
engine in-process; this CLI shape is just a convenience wrapper that
mirrors the harness pattern from ABCD's instrumented driver.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _start_engine():
    import matlab.engine  # type: ignore
    return matlab.engine.start_matlab("-singleCompThread -nodisplay -nosplash -nodesktop")


def run_one(eng, job: dict) -> dict:
    inst_dir = Path(__file__).parent
    eng.addpath(str(inst_dir), nargout=0)
    with tempfile.TemporaryDirectory() as td:
        job_path = Path(td) / "job.json"
        out_path = Path(td) / "out.json"
        with open(job_path, "w") as f:
            json.dump(job, f)
        eng.instrumented_npso(str(job_path), str(out_path), nargout=0)
        with open(out_path) as f:
            return json.load(f)


def main():
    job = json.loads(sys.stdin.read())
    eng = _start_engine()
    try:
        out = run_one(eng, job)
    finally:
        eng.exit()
    sys.stdout.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
