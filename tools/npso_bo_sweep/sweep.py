"""Sweep nPSO T-search strategies (bayesian vs secant) across (target,
samples_per_T, max_iters, seed). Records best_cc, |diff|, iters used,
wall clock per run. Output: a JSON list ready for ``analyze.py``.

Designed for small N (default 500) so MATLAB realisations stay below
~0.2s each via the persistent engine. The sweep stays under ~20 min
for the default grid.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from itertools import product
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "npso"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=500)
    ap.add_argument("--m", type=int, default=6)
    ap.add_argument("--gamma", type=float, default=2.5)
    ap.add_argument("--c", type=int, default=5)
    ap.add_argument("--model", type=str, default="nPSO2")
    ap.add_argument("--mixing-proportions", type=str, default="0.2,0.2,0.2,0.2,0.2")
    ap.add_argument("--targets", type=str, default="0.18,0.15,0.12")
    ap.add_argument("--strategies", type=str, default="bayesian,secant")
    ap.add_argument("--samples-per-T", type=str, default="1,3")
    ap.add_argument("--max-iters", type=str, default="20,50")
    ap.add_argument("--seeds", type=str, default="1,2,3")
    ap.add_argument("--initial-points", type=int, default=5)
    ap.add_argument("--n-threads", type=int, default=1)
    ap.add_argument("--diff-tol", type=float, default=0.001)
    ap.add_argument("--step-tol", type=float, default=1e-5)
    ap.add_argument("--t-min", type=float, default=0.005)
    ap.add_argument("--out", type=str, required=True)
    args = ap.parse_args()

    # Suppress per-iter INFO; we want a clean per-run log.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    targets = [float(x) for x in args.targets.split(",") if x.strip()]
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    samples_list = [int(x) for x in args.samples_per_T.split(",") if x.strip()]
    iters_list = [int(x) for x in args.max_iters.split(",") if x.strip()]
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    mixing = [float(x) for x in args.mixing_proportions.split(",") if x.strip()]

    grid = list(product(targets, strategies, samples_list, iters_list, seeds))
    print(f"sweep: {len(grid)} runs")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Re-import gen for each run to wipe its module-scope state and get a
    # fresh search_log path. To save MATLAB engine startup cost (~10s) we
    # keep one engine alive across runs by importing once and reusing the
    # high-level run_npso_generation API.
    import gen

    results = []
    sweep_root = Path(tempfile.mkdtemp(prefix="npso_sweep_"))
    try:
        for i, (target, strategy, samples, max_iters, seed) in enumerate(grid):
            run_id = f"t{target:.3f}_{strategy}_s{samples}_it{max_iters}_se{seed}"
            run_dir = sweep_root / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            print(f"[{i+1:>3}/{len(grid)}] {run_id} ", end="", flush=True)
            t0 = time.perf_counter()
            try:
                gen.run_npso_generation(
                    N=args.N, m=args.m, gamma=args.gamma, c=args.c,
                    target_global_ccoeff=target,
                    mixing_proportions=mixing,
                    npso_dir=str(REPO / "externals" / "npso"),
                    output_dir=str(run_dir),
                    seed=seed,
                    n_threads=args.n_threads,
                    model=args.model,
                    search_strategy=strategy,
                    search_initial_points=args.initial_points,
                    search_samples_per_T=samples,
                    search_max_iters=max_iters,
                    search_diff_tol=args.diff_tol,
                    search_step_tol=args.step_tol,
                    search_t_min=args.t_min,
                )
                ok = True
                err = None
            except Exception as exc:
                ok = False
                err = str(exc)
            wall = time.perf_counter() - t0
            log_path = run_dir / "search_log.json"
            if log_path.exists():
                with log_path.open() as f:
                    doc = json.load(f)
                iters = doc.get("iters", [])
            else:
                iters = []
            best_cc = None
            best_T = None
            best_diff = None
            n_iters = len(iters)
            for r in iters:
                d = abs(float(r["ccoeff"]) - target)
                if best_diff is None or d < best_diff:
                    best_diff = d
                    best_cc = float(r["ccoeff"])
                    best_T = float(r["T"])
            n_matlab_calls = sum(
                len(r.get("samples") or []) if r.get("samples") else 1
                for r in iters
            )
            results.append({
                "run_id": run_id,
                "ok": ok, "err": err, "wallclock": wall,
                "target": target, "strategy": strategy,
                "samples_per_T": samples, "max_iters": max_iters, "seed": seed,
                "n_iters": n_iters, "n_matlab_calls": n_matlab_calls,
                "best_T": best_T, "best_cc": best_cc, "best_diff": best_diff,
            })
            print(f"diff={best_diff} iters={n_iters} matlab={n_matlab_calls} wall={wall:.1f}s")
            with out_path.open("w") as f:
                json.dump({
                    "config": vars(args),
                    "results": results,
                }, f, indent=2)
    finally:
        shutil.rmtree(sweep_root, ignore_errors=True)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
