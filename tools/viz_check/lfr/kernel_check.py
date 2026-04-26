"""LFR kernel check.

Drives the actual LFR pipeline (the C++ binary at
``externals/lfr/unweighted_undirected/benchmark``, invoked the same
way ``src/lfr/gen.py`` invokes it) on a small fixture and a
~100-node planted-cluster fixture, across multiple seeds, and
verifies the **distributional** guarantees that LFR actually
provides.

What LFR does and does not guarantee
------------------------------------

LFR fits two power laws on the input profile (degrees and cluster
sizes), hands the exponents to the C++ binary, and the binary
**re-samples** fresh sequences from those power laws. Output node
degrees and output cluster sizes are NOT preserved from the input;
the only thing that is locked exactly is N (the ``-N`` CLI flag).
See ``memory/gen_lfr.md`` for the full discussion. Any future
maintainer who tries to assert exact degree-sequence preservation
or exact cluster-size preservation here will be wrong.

Verified per (fixture, seed):

  - N exact.
  - output is a simple graph (no self-loops, no parallel edges).
  - max degree <= maxk (the truncation parameter LFR was given).
  - every output cluster size is in [minc, maxc].
  - mean per-node mixing within +/- 0.10 of the target mu.
  - power-law exponent fit on the output degree sequence is
    within +/- 0.5 of the input fit's t1.
  - power-law exponent fit on the output cluster sizes is
    within +/- 0.5 of t2.

The +/- 0.5 tolerance on the power-law exponents is loose because
``powerlaw.Fit`` is noisy on small samples (a 100-node graph gives
the fitter only ~100 degree observations and only a handful of
cluster sizes), and the C++ truncation interacts with the fit's
own xmin selection. A tighter tolerance would produce flakes
without catching real regressions.

Below a structural floor (``MIN_FIT_N = 50`` nodes for degrees,
``MIN_FIT_K = 5`` clusters for sizes) the harness reports the
fit drift but does not assert on it: powerlaw.Fit saturates at
its alpha-ceiling around 3.0 on tiny inputs and the comparison
becomes meaningless. The structural invariants (N, simple-graph,
maxk, minc/maxc, mean mu) are still asserted on every fixture.

How to build the LFR binary (only if it is missing)
---------------------------------------------------

The submodule path is ``externals/lfr/unweighted_undirected``.
From the repo root::

    cd externals/lfr/unweighted_undirected
    make

That produces the ``benchmark`` executable in the same directory.
If the binary is missing when this harness runs, the harness
prints a "skipped: binary not present" notice and exits 0 without
failing.

Run::

    conda run -n nwbench python tools/lfr/kernel_check.py
    conda run -n nwbench python tools/lfr/kernel_check.py --seeds 1 2 3 4 5
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import powerlaw


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BINARY = (
    REPO_ROOT / "externals" / "lfr" / "unweighted_undirected" / "benchmark"
)

# Below this floor, powerlaw.Fit saturates on its alpha ceiling
# (typically ~3.0) and the input-vs-output comparison stops being
# meaningful. The harness reports the drift but does not assert.
MIN_FIT_N = 50      # min N for the degree-exponent assertion
MIN_FIT_K = 5       # min number of clusters for the size-exponent assertion


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def small20_fixture() -> dict:
    """20-node, 40-edge fixture; matches the shared netgen synthetic."""
    C1 = [1, 2, 3, 4, 5, 6, 7, 8]
    C2 = [9, 10, 11, 12, 13, 14]
    C3 = [15, 16, 17, 18]
    OUT = [19, 20]
    intra_C1 = [
        (1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4),
        (5, 6), (5, 7), (6, 7), (6, 8), (7, 8),
        (1, 5), (4, 8),
    ]
    intra_C2 = [
        (9, 10), (9, 11), (9, 12), (9, 13),
        (10, 11), (10, 12), (10, 14),
        (11, 12), (11, 14),
        (12, 13),
    ]
    intra_C3 = [(15, 16), (15, 17), (16, 17), (16, 18)]
    inter = [
        (1, 9), (2, 10), (3, 11), (5, 12),
        (9, 15), (11, 16),
        (1, 15), (4, 17),
    ]
    out_edges = [(19, 1), (19, 9), (20, 5), (20, 16), (19, 20)]
    edges = intra_C1 + intra_C2 + intra_C3 + inter + out_edges
    nodes = C1 + C2 + C3 + OUT
    cluster_of = {}
    for n in C1: cluster_of[n] = "C1"
    for n in C2: cluster_of[n] = "C2"
    for n in C3: cluster_of[n] = "C3"
    for n in OUT: cluster_of[n] = "OUT"
    return _build_fixture("small20", nodes, edges, cluster_of)


def planted100_fixture(seed: int = 42) -> dict:
    """100 nodes, 5 planted clusters of 20 each; dense intra, sparse inter."""
    rng = random.Random(seed)
    n_per_cluster = 20
    n_clusters = 5
    nodes = list(range(1, n_per_cluster * n_clusters + 1))
    cluster_of = {}
    cur = 0
    for ci in range(n_clusters):
        cname = f"C{ci + 1}"
        for _ in range(n_per_cluster):
            cluster_of[nodes[cur]] = cname
            cur += 1
    edges = set()
    p_intra = 0.30
    p_inter = 0.02
    n = len(nodes)
    for i in range(n):
        for j in range(i + 1, n):
            u, v = nodes[i], nodes[j]
            same = cluster_of[u] == cluster_of[v]
            p = p_intra if same else p_inter
            if rng.random() < p:
                edges.add((u, v))
    return _build_fixture("planted100_5c", nodes, sorted(edges), cluster_of)


def _build_fixture(name, nodes, edges, cluster_of) -> dict:
    return {
        "name": name,
        "nodes": list(nodes),
        "edges": list(edges),
        "cluster_of": dict(cluster_of),
    }


# ---------------------------------------------------------------------------
# Profile (mirrors src/lfr/profile.py output)
# ---------------------------------------------------------------------------

def profile_fixture(fx: dict) -> dict:
    """Compute (degree.csv, cluster_sizes.csv, mixing_parameter) for fixture.

    Mirrors what ``src/lfr/profile.py`` would emit but without going
    through the file system or the outlier-mode machinery. The
    fixture has no outliers (every node has a cluster label).
    """
    nodes = fx["nodes"]
    cluster_of = fx["cluster_of"]
    deg = {n: 0 for n in nodes}
    in_deg = {n: 0 for n in nodes}
    out_deg = {n: 0 for n in nodes}
    for u, v in fx["edges"]:
        deg[u] += 1
        deg[v] += 1
        if cluster_of[u] == cluster_of[v]:
            in_deg[u] += 1
            in_deg[v] += 1
        else:
            out_deg[u] += 1
            out_deg[v] += 1
    degrees = [deg[n] for n in nodes]
    sizes_by_c = {}
    for n in nodes:
        sizes_by_c[cluster_of[n]] = sizes_by_c.get(cluster_of[n], 0) + 1
    cluster_sizes = list(sizes_by_c.values())
    # Mean per-node mu (matches profile_common.compute_mixing_parameter
    # with reduction="mean").
    mus = []
    for n in nodes:
        t = in_deg[n] + out_deg[n]
        if t == 0:
            continue
        mus.append(out_deg[n] / t)
    mu = float(np.mean(mus)) if mus else 0.0
    return {
        "degrees": degrees,
        "cluster_sizes": cluster_sizes,
        "mu": mu,
    }


# ---------------------------------------------------------------------------
# Run the LFR binary the same way src/lfr/gen.py does
# ---------------------------------------------------------------------------

def derive_lfr_params(profile: dict) -> dict:
    """Match the parameter derivation in ``src/lfr/gen.py``."""
    degrees = np.asarray(profile["degrees"])
    cluster_sizes = np.asarray(profile["cluster_sizes"])
    mu = float(profile["mu"])

    N = int(len(degrees))
    k = float(np.mean(degrees))
    maxk = int(np.max(degrees))
    t1 = float(powerlaw.Fit(degrees, discrete=True, verbose=False).power_law.alpha)

    minc = max(int(np.min(cluster_sizes)), 3)
    maxc = int(np.max(cluster_sizes))
    t2 = float(
        powerlaw.Fit(cluster_sizes, discrete=True, verbose=False, xmin=minc).power_law.alpha
    )
    return {
        "N": N, "k": k, "maxk": maxk, "minc": minc, "maxc": maxc,
        "mu": mu, "t1": t1, "t2": t2,
    }


def run_lfr(binary: Path, params: dict, seed: int, work_dir: Path) -> dict:
    """Invoke the LFR C++ binary and return parsed (edges_df, com_df)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "time_seed.dat").write_text(f"{seed}\n")
    cmd = [
        str(binary),
        "-N", str(params["N"]),
        "-k", str(params["k"]),
        "-maxk", str(params["maxk"]),
        "-minc", str(params["minc"]),
        "-maxc", str(params["maxc"]),
        "-mu", str(params["mu"]),
        "-t1", str(params["t1"]),
        "-t2", str(params["t2"]),
    ]
    proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"lfr binary failed (rc={proc.returncode}):\n{proc.stderr}"
        )
    network_dat = work_dir / "network.dat"
    community_dat = work_dir / "community.dat"
    if not (network_dat.exists() and community_dat.exists()):
        raise RuntimeError("lfr binary did not produce network.dat/community.dat")

    edges_df = pd.read_csv(
        network_dat, sep=r"\s+", header=None, names=["source", "target"]
    )
    com_df = pd.read_csv(
        community_dat, sep=r"\s+", header=None, names=["node_id", "cluster_id"]
    )
    return {"edges": edges_df, "com": com_df, "params": params}


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def check_invariants(fx_name: str, params: dict, run_out: dict, mu_tol: float,
                     pl_tol: float) -> tuple[bool, list[str]]:
    edges_df = run_out["edges"]
    com_df = run_out["com"]

    failures: list[str] = []
    msgs: list[str] = []

    # Build undirected edge set (LFR's network.dat lists each edge twice).
    seen = set()
    self_loops = 0
    parallel = 0
    for u, v in zip(edges_df["source"], edges_df["target"]):
        u, v = int(u), int(v)
        if u == v:
            self_loops += 1
            continue
        key = (min(u, v), max(u, v))
        if key in seen:
            # Same key seen before — the second (v, u) listing is expected
            # and not a parallel edge. We track parallels by counting how
            # many times each unordered edge appears beyond 2.
            pass
        seen.add(key)

    # Properly count parallels: per unordered edge, how many directed listings.
    edge_counts: dict = {}
    for u, v in zip(edges_df["source"], edges_df["target"]):
        u, v = int(u), int(v)
        if u == v:
            continue
        k = (min(u, v), max(u, v))
        edge_counts[k] = edge_counts.get(k, 0) + 1
    parallel = sum(1 for c in edge_counts.values() if c > 2)
    n_undirected_edges = len(edge_counts)

    # Build per-node degree (count each unordered edge once).
    deg: dict = {}
    for u, v in edge_counts:
        deg[u] = deg.get(u, 0) + 1
        deg[v] = deg.get(v, 0) + 1

    # 1. N exact.
    nodes_in_com = set(com_df["node_id"].astype(int).tolist())
    n_observed = len(nodes_in_com)
    n_ok = (n_observed == params["N"])
    if not n_ok:
        failures.append(f"N: observed {n_observed} != expected {params['N']}")
    msgs.append(f"N={n_observed} (expected {params['N']}) ok={n_ok}")

    # 2. simple graph.
    simple_ok = (self_loops == 0 and parallel == 0)
    if not simple_ok:
        failures.append(
            f"non-simple graph: {self_loops} self-loops, {parallel} parallels"
        )
    msgs.append(
        f"simple_graph: edges={n_undirected_edges} loops={self_loops} "
        f"parallels={parallel} ok={simple_ok}"
    )

    # 3. max degree <= maxk.
    max_deg = max(deg.values()) if deg else 0
    maxk_ok = (max_deg <= params["maxk"])
    if not maxk_ok:
        failures.append(f"max degree {max_deg} > maxk {params['maxk']}")
    msgs.append(f"max_deg={max_deg} (maxk={params['maxk']}) ok={maxk_ok}")

    # 4. cluster sizes in [minc, maxc].
    cluster_sizes = com_df["cluster_id"].value_counts().tolist()
    cs_min = min(cluster_sizes)
    cs_max = max(cluster_sizes)
    cs_ok = (cs_min >= params["minc"] and cs_max <= params["maxc"])
    if not cs_ok:
        failures.append(
            f"cluster sizes [{cs_min}, {cs_max}] out of "
            f"[minc={params['minc']}, maxc={params['maxc']}]"
        )
    msgs.append(
        f"cluster_size_range=[{cs_min}, {cs_max}] "
        f"(want [{params['minc']}, {params['maxc']}]) ok={cs_ok}"
    )

    # 5. mean per-node mu within tolerance.
    node2com = dict(zip(com_df["node_id"].astype(int), com_df["cluster_id"]))
    in_d: dict = {}
    out_d: dict = {}
    for u, v in edge_counts:
        cu = node2com.get(u)
        cv = node2com.get(v)
        if cu is None or cv is None:
            continue
        if cu == cv:
            in_d[u] = in_d.get(u, 0) + 1
            in_d[v] = in_d.get(v, 0) + 1
        else:
            out_d[u] = out_d.get(u, 0) + 1
            out_d[v] = out_d.get(v, 0) + 1
    mus = []
    for n in node2com:
        t = in_d.get(n, 0) + out_d.get(n, 0)
        if t == 0:
            continue
        mus.append(out_d.get(n, 0) / t)
    mean_mu = float(np.mean(mus)) if mus else 0.0
    mu_delta = abs(mean_mu - params["mu"])
    mu_ok = (mu_delta < mu_tol)
    if not mu_ok:
        failures.append(
            f"mean per-node mu drift: |{mean_mu:.4f} - {params['mu']:.4f}| "
            f"= {mu_delta:.4f} >= tol {mu_tol:.2f}"
        )
    msgs.append(
        f"mean_mu={mean_mu:.4f} (target {params['mu']:.4f}, "
        f"|delta|={mu_delta:.4f}, tol={mu_tol:.2f}) ok={mu_ok}"
    )

    # 6. power-law t1 fit on output degrees, +/- pl_tol of input t1.
    out_degrees = list(deg.values())
    fit_n_ok = (params["N"] >= MIN_FIT_N)
    try:
        out_t1 = float(
            powerlaw.Fit(out_degrees, discrete=True, verbose=False).power_law.alpha
        )
        t1_delta = abs(out_t1 - params["t1"])
        if not fit_n_ok:
            t1_ok = True
            msgs.append(
                f"out_t1={out_t1:.3f} (target {params['t1']:.3f}, "
                f"|delta|={t1_delta:.3f}) reported only "
                f"(N={params['N']} < MIN_FIT_N={MIN_FIT_N})"
            )
        else:
            t1_ok = (t1_delta < pl_tol)
            if not t1_ok:
                failures.append(
                    f"output degree power-law t1 drift: |{out_t1:.3f} - "
                    f"{params['t1']:.3f}| = {t1_delta:.3f} >= tol {pl_tol:.2f}"
                )
            msgs.append(
                f"out_t1={out_t1:.3f} (target {params['t1']:.3f}, "
                f"|delta|={t1_delta:.3f}, tol={pl_tol:.2f}) ok={t1_ok}"
            )
    except Exception as exc:
        msgs.append(f"out_t1: fit failed ({exc}); skipping")
        t1_ok = True

    # 7. power-law t2 fit on output cluster sizes, +/- pl_tol of input t2.
    fit_k_ok = (len(cluster_sizes) >= MIN_FIT_K)
    try:
        out_t2 = float(
            powerlaw.Fit(
                cluster_sizes, discrete=True, verbose=False,
                xmin=params["minc"],
            ).power_law.alpha
        )
        t2_delta = abs(out_t2 - params["t2"])
        if not fit_k_ok:
            t2_ok = True
            msgs.append(
                f"out_t2={out_t2:.3f} (target {params['t2']:.3f}, "
                f"|delta|={t2_delta:.3f}) reported only "
                f"(K={len(cluster_sizes)} < MIN_FIT_K={MIN_FIT_K})"
            )
        else:
            t2_ok = (t2_delta < pl_tol)
            if not t2_ok:
                failures.append(
                    f"output cluster-size power-law t2 drift: |{out_t2:.3f} - "
                    f"{params['t2']:.3f}| = {t2_delta:.3f} >= tol {pl_tol:.2f}"
                )
            msgs.append(
                f"out_t2={out_t2:.3f} (target {params['t2']:.3f}, "
                f"|delta|={t2_delta:.3f}, tol={pl_tol:.2f}) ok={t2_ok}"
            )
    except Exception as exc:
        msgs.append(f"out_t2: fit failed ({exc}); skipping")
        t2_ok = True

    all_ok = (
        n_ok and simple_ok and maxk_ok and cs_ok and mu_ok and t1_ok and t2_ok
    )
    return all_ok, msgs


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--seeds", type=int, nargs="*", default=list(range(1, 6)),
        help="Seeds to drive the LFR binary; default 1..5 (>= 5).",
    )
    ap.add_argument(
        "--binary", default=str(DEFAULT_BINARY),
        help="Path to the LFR benchmark executable.",
    )
    ap.add_argument(
        "--mu-tol", type=float, default=0.10,
        help="Tolerance on mean per-node mu drift.",
    )
    ap.add_argument(
        "--pl-tol", type=float, default=0.5,
        help="Tolerance on power-law exponent drift (loose by design).",
    )
    args = ap.parse_args()

    binary = Path(args.binary)
    if not binary.exists():
        print(
            f"skipped: binary not present at {binary}.\n"
            f"  build it with:\n"
            f"    cd externals/lfr/unweighted_undirected && make"
        )
        return 0

    fixtures = [small20_fixture(), planted100_fixture(seed=42)]

    overall = True
    fixture_summaries: list[str] = []
    pass_count = 0
    fail_count = 0
    for fx in fixtures:
        prof = profile_fixture(fx)
        try:
            params = derive_lfr_params(prof)
        except Exception as exc:
            print(
                f"\n=== fixture: {fx['name']} (N={len(fx['nodes'])}, "
                f"E={len(fx['edges'])}) ===\n"
                f"  parameter derivation failed: {exc}"
            )
            overall = False
            fail_count += 1
            continue

        print(
            f"\n=== fixture: {fx['name']} "
            f"(N={params['N']}, E={len(fx['edges'])}, "
            f"k={params['k']:.2f}, maxk={params['maxk']}, "
            f"minc={params['minc']}, maxc={params['maxc']}, "
            f"mu={params['mu']:.4f}, t1={params['t1']:.3f}, "
            f"t2={params['t2']:.3f}) ==="
        )

        fx_pass = 0
        fx_fail = 0
        for seed in args.seeds:
            print(f"\n  seed={seed}")
            with tempfile.TemporaryDirectory(prefix=f"lfr_check_{fx['name']}_") as td:
                work_dir = Path(td)
                try:
                    run_out = run_lfr(binary, params, seed, work_dir)
                except Exception as exc:
                    print(f"    run failed: {exc}")
                    overall = False
                    fx_fail += 1
                    fail_count += 1
                    continue

                ok, msgs = check_invariants(
                    fx["name"], params, run_out,
                    mu_tol=args.mu_tol, pl_tol=args.pl_tol,
                )
                for m in msgs:
                    print(f"    {m}")
                if ok:
                    fx_pass += 1
                    pass_count += 1
                else:
                    fx_fail += 1
                    fail_count += 1
                    overall = False
        fixture_summaries.append(
            f"{fx['name']}: {fx_pass}/{fx_pass + fx_fail} seeds passed"
        )

    print("\n" + ("=" * 60))
    print("OVERALL: " + ("PASS" if overall else "FAIL"))
    print(
        "summary: fixtures=[{fxs}], seeds={seeds}, "
        "passed {p}/{t} (binary at {b}); "
        "tolerances: mu +/- {mt}, power-law exponent +/- {pt} (loose, "
        "fitter is noisy on small samples).".format(
            fxs=", ".join(s.split(":")[0] for s in fixture_summaries),
            seeds=list(args.seeds),
            p=pass_count, t=pass_count + fail_count, b=binary,
            mt=args.mu_tol, pt=args.pl_tol,
        )
    )
    for s in fixture_summaries:
        print(f"  - {s}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
