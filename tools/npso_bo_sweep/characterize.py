"""Characterize the achievable global-ccoeff range of nPSO at fixed
(N, m, gamma, c, model) by sweeping a grid of T values with one
realisation each.

Output: a JSON file with one record per (T, seed) pair listing the
realised global ccoeff. Useful as input to ``sweep.py`` which picks
sensible target ccoeffs to optimise toward.

Run:
    PATH=/home/vltanh/miniconda3/envs/nwbench/bin:$PATH \
    PYTHONPATH=src \
    python tools/npso_bo_sweep/characterize.py \
        --N 500 --m 6 --gamma 2.5 --c 5 --model nPSO2 \
        --mixing-proportions 0.2,0.2,0.2,0.2,0.2 \
        --t-min 0.05 --t-max 0.99 --t-points 10 \
        --seeds 1,2,3 \
        --out tools/npso_bo_sweep/characterize_n500.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "npso"))

import gen  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, required=True)
    ap.add_argument("--m", type=int, required=True)
    ap.add_argument("--gamma", type=float, required=True)
    ap.add_argument("--c", type=int, required=True)
    ap.add_argument("--model", type=str, default="nPSO2")
    ap.add_argument("--mixing-proportions", type=str, default="")
    ap.add_argument("--t-min", type=float, default=0.05)
    ap.add_argument("--t-max", type=float, default=0.99)
    ap.add_argument("--t-points", type=int, default=10)
    ap.add_argument("--seeds", type=str, default="1,2,3")
    ap.add_argument("--n-threads", type=int, default=1)
    ap.add_argument("--npso-dir", type=str, default=str(REPO / "externals" / "npso"))
    ap.add_argument("--out", type=str, required=True)
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    Ts = list(np.linspace(args.t_min, args.t_max, args.t_points))
    mixing = [float(x) for x in args.mixing_proportions.split(",") if x.strip()]
    npso_dir_abs = Path(args.npso_dir).resolve()
    matlab_wrapper_dir = REPO / "src" / "npso" / "matlab"

    runner = gen.make_runner(args.n_threads, npso_dir_abs, str(matlab_wrapper_dir))
    records = []
    try:
        with tempfile.TemporaryDirectory(prefix="npso_chrz_") as scratch_str:
            scratch = Path(scratch_str)
            for seed in seeds:
                for T in Ts:
                    t0 = time.perf_counter()
                    prefix = scratch / f"s{seed}_T{T:.4f}_"
                    res = runner.run_iter(
                        args.N, args.m, float(T), args.gamma, args.c,
                        args.model, mixing, prefix, seed,
                    )
                    dt = time.perf_counter() - t0
                    if res is None:
                        records.append({"seed": seed, "T": T, "ccoeff": None, "wallclock": dt})
                        print(f"seed={seed} T={T:.4f} FAILED ({dt:.1f}s)")
                        continue
                    edge_df, _ = res
                    cc = gen._ccoeff_from_edges(edge_df)
                    records.append({"seed": seed, "T": T, "ccoeff": cc, "wallclock": dt})
                    print(f"seed={seed} T={T:.4f} cc={cc:.4f} ({dt:.1f}s)")
    finally:
        runner.close()

    summary = {
        "config": {
            "N": args.N, "m": args.m, "gamma": args.gamma, "c": args.c,
            "model": args.model, "mixing_proportions": mixing,
        },
        "Ts": Ts,
        "seeds": seeds,
        "records": records,
    }
    with open(args.out, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
