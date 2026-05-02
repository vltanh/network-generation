"""Aggregate ``sweep.py`` output into a comparison table.

Per (target, samples_per_T, max_iters) cell, summarise both strategies
across seeds: median best_diff, p25/p75, mean wallclock, mean total
MATLAB calls. Print a markdown-flavoured table to stdout.

Usage:
    python tools/npso_bo_sweep/analyze.py tools/npso_bo_sweep/sweep_n500.json
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict


def fmt(x, fmt_spec=".4f"):
    if x is None:
        return "  n/a"
    return f"{x:{fmt_spec}}"


def main():
    path = sys.argv[1]
    with open(path) as f:
        doc = json.load(f)
    rows = doc["results"]

    # Group by (target, samples_per_T, max_iters, strategy) → list of best_diff, wallclock, calls
    cells = defaultdict(lambda: {"diffs": [], "walls": [], "calls": [], "iters": []})
    for r in rows:
        if not r.get("ok", False):
            continue
        key = (r["target"], r["samples_per_T"], r["max_iters"], r["strategy"])
        cells[key]["diffs"].append(r["best_diff"])
        cells[key]["walls"].append(r["wallclock"])
        cells[key]["calls"].append(r["n_matlab_calls"])
        cells[key]["iters"].append(r["n_iters"])

    # Pivot for printing: per (target, samples, iters), bayesian vs secant.
    triplets = sorted({(t, s, mi) for (t, s, mi, _) in cells})
    print()
    print(f"{'target':>7} {'samples':>7} {'iters':>5}   "
          f"{'BO diff (med, p25-p75)':>30}   {'secant diff (med, p25-p75)':>30}   "
          f"{'BO calls':>9}   {'sec calls':>9}   "
          f"{'BO wall':>9}   {'sec wall':>9}")
    print("-" * 150)
    for t, s, mi in triplets:
        bo = cells.get((t, s, mi, "bayesian"), {})
        sc = cells.get((t, s, mi, "secant"), {})

        def stats(d):
            if not d.get("diffs"):
                return None, None, None, None, None
            ds = d["diffs"]
            med = statistics.median(ds)
            ds_sorted = sorted(ds)
            n = len(ds_sorted)
            p25 = ds_sorted[max(0, n // 4)]
            p75 = ds_sorted[min(n - 1, (3 * n) // 4)]
            wall = statistics.mean(d["walls"])
            calls = statistics.mean(d["calls"])
            return med, p25, p75, wall, calls

        bm, bp25, bp75, bw, bc = stats(bo)
        sm, sp25, sp75, sw, sc_calls = stats(sc)
        print(
            f"{t:7.3f} {s:7d} {mi:5d}   "
            f"{fmt(bm)} ({fmt(bp25)}-{fmt(bp75)})   "
            f"{fmt(sm)} ({fmt(sp25)}-{fmt(sp75)})   "
            f"{fmt(bc, '9.1f')}   {fmt(sc_calls, '9.1f')}   "
            f"{fmt(bw, '9.1f')}   {fmt(sw, '9.1f')}"
        )

    # Headline: who wins at default budget (max_iters max, samples=1)?
    max_iters_max = max(mi for (_, _, mi) in triplets)
    print()
    print(f"=== headline at iters={max_iters_max}, samples=1 ===")
    for t, s, mi in triplets:
        if mi != max_iters_max or s != 1:
            continue
        bo = cells.get((t, s, mi, "bayesian"), {})
        sc = cells.get((t, s, mi, "secant"), {})
        if not bo.get("diffs") or not sc.get("diffs"):
            continue
        bm = statistics.median(bo["diffs"])
        sm = statistics.median(sc["diffs"])
        winner = "BO" if bm < sm else ("secant" if sm < bm else "tie")
        print(f"  target={t:.3f}: BO median diff={bm:.4f}  secant={sm:.4f}  winner={winner}")


if __name__ == "__main__":
    main()
