"""nPSO kernel cross-check.

Two cross-cutting verifications on the small20 fixture (and an optional
60-node fixture):

1. The shipped nPSO generator (`src/npso/gen.py` + the MATLAB wrapper)
   produces edge.csv satisfying nPSO's hard contract:
   - simple graph (no self-loops, no parallel edges).
   - exact node count N (every arrival 1..N appears in at least one edge).
   - per non-seed-K_{m+1} arrival t (i.e. t > m+1), exactly m predecessor
     edges; for t in [2, m+1] (the seed K_{m+1}), exactly t-1.
   - every emitted edge respects connection-by-distance: at the
     converged T the Fermi-Dirac probability upper bound (using the
     best-case h_lb = |r_i - r_j|) stays above a small floor — a
     sanity check that the converged T isn't pathologically degenerate.
2. The Node port of the impl-3 walker (`tools/npso/kernel_check.mjs`)
   satisfies the same per-arrival m + simple-graph invariants on the
   same fixture (with deterministic LCG, d3.randomLcg-equivalent), and
   the JS port driven in `replay` mode (consuming a previously-emitted
   per-arrival U_NODE trace) produces bit-identical edges to the
   original rng-driven run.

Per `memory/gen_contract_divergences.md`:
- Item 4 (cluster count): nPSO output com.csv may carry cluster_ids
  outside [1..c]. We log the observed unique-cluster count vs c but
  do not gate on equality.

Run:

    conda run -n nwbench python tools/npso/kernel_check.py
    conda run -n nwbench python tools/npso/kernel_check.py --include-larger
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
NPSO_DIR = REPO_ROOT / "externals" / "npso"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def small20_fixture() -> dict:
    """20-node fixture mirroring vltanh.github.io/netgen/shared.js exactly."""
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


def _build_fixture(name: str, nodes, edges, cluster_of) -> dict:
    """Compute per-fixture profile-equivalent scalars (N, m, gamma, c, T*, rho)."""
    nodes = list(nodes)
    edges = [(min(u, v), max(u, v)) for u, v in edges]
    N = len(nodes)
    deg = {n: 0 for n in nodes}
    for u, v in edges:
        deg[u] += 1
        deg[v] += 1
    mean_deg = sum(deg.values()) / N if N > 0 else 0.0
    m = max(1, int(round(mean_deg / 2)))

    # Mirror profile.py: powerlaw.Fit(degrees, discrete=True).power_law.alpha
    # floored at 2.0. Profile uses the python `powerlaw` package.
    import powerlaw
    alpha = powerlaw.Fit(np.array(list(deg.values())), discrete=True, verbose=False).power_law.alpha
    gamma = float(max(alpha, 2.0))

    # Cluster sizes per profile_common's outlier_mode=singleton: each input
    # outlier becomes its own 1-node cluster.
    sizes = {}
    for n in nodes:
        c_id = cluster_of[n]
        if c_id == "OUT":
            c_id = f"__outlier_{n}__"
        sizes[c_id] = sizes.get(c_id, 0) + 1
    c = len(sizes)
    sizes_sorted = sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(s for _, s in sizes_sorted)
    rho = [s / total for _, s in sizes_sorted]

    # Target global clustering coefficient. Use networkit like profile.py.
    import networkit as nk
    g = nk.graph.Graph(n=0, weighted=False, directed=False)
    idx = {n: i for i, n in enumerate(nodes)}
    for _ in range(N):
        g.addNode()
    for u, v in edges:
        g.addEdge(idx[u], idx[v])
    g.removeMultiEdges()
    g.removeSelfLoops()
    target_cc = float(nk.globals.ClusteringCoefficient.exactGlobal(g))

    return {
        "name": name,
        "N": N,
        "m": m,
        "gamma": gamma,
        "c": c,
        "target_cc": target_cc,
        "mixing_proportions": rho,
        "edges": edges,
        "nodes": nodes,
        "cluster_of": cluster_of,
    }


def run_actual_npso(fixture: dict, seed: int, scratch_dir: Path,
                    n_threads: int = 1, model: str = "nPSO2",
                    max_iters: int = 30):
    """Invoke the shipped generator end to end and return parsed outputs."""
    import npso.gen as gen_mod
    out_dir = scratch_dir / f"seed_{seed}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    gen_mod.run_npso_generation(
        N=fixture["N"],
        m=fixture["m"],
        gamma=fixture["gamma"],
        c=fixture["c"],
        target_global_ccoeff=fixture["target_cc"],
        mixing_proportions=fixture["mixing_proportions"],
        npso_dir=str(NPSO_DIR),
        output_dir=str(out_dir),
        seed=seed,
        n_threads=n_threads,
        model=model,
        search_max_iters=max_iters,
    )
    elapsed = time.time() - t0

    edge_df = pd.read_csv(out_dir / "edge.csv")
    com_df = pd.read_csv(out_dir / "com.csv")

    # `simplify_edges` canonicalises rows to (min, max) and drops dupes.
    # Since MATLAB emits 1-based node ids 1..N where the id IS the arrival
    # index t, "max(source, target)" remains the arrival and "min" the
    # predecessor — that's all we need for the per-arrival-m check.
    log_path = out_dir / "search_log.json"
    iters = []
    best_T = None
    best_cc = None
    if log_path.exists():
        with log_path.open() as f:
            iters = json.load(f).get("iters", [])
        if iters:
            target = fixture["target_cc"]
            best = min(iters, key=lambda r: abs(r["ccoeff"] - target))
            best_T = best["T"]
            best_cc = best["ccoeff"]

    return {
        "edge_df": edge_df,
        "com_df": com_df,
        "raw_edge_df": edge_df,  # canonicalised rows are sufficient here
        "iters": iters,
        "best_T": best_T,
        "best_cc": best_cc,
        "elapsed": elapsed,
        "out_dir": out_dir,
    }


def check_per_arrival_m(raw_edge_df: pd.DataFrame, N: int, m: int) -> dict:
    """Per non-seed-K_{m+1} arrival t, expect exactly m predecessor edges.

    The MATLAB sampler emits arrivals 1..N. For t <= m+1 (the seed K_{m+1}),
    arrival t connects to all t-1 earlier nodes. For t > m+1, exactly m.
    Edges are emitted as (t, j) with j < t in the raw output.
    """
    # Group by arrival = max(source, target) and count.
    src = raw_edge_df["source"].to_numpy()
    tgt = raw_edge_df["target"].to_numpy()
    arrival = np.maximum(src, tgt)
    predecessor = np.minimum(src, tgt)

    by_arrival = {}
    for a, p in zip(arrival, predecessor):
        if int(a) == int(p):
            continue  # self-loop (shouldn't happen but be defensive)
        by_arrival.setdefault(int(a), set()).add(int(p))

    bad = []
    for t in range(2, N + 1):
        expected = (t - 1) if t <= m + 1 else m
        actual = len(by_arrival.get(t, set()))
        if actual != expected:
            bad.append((t, expected, actual))
    return {
        "ok": len(bad) == 0,
        "bad": bad,
        "total_arrivals_checked": N - 1,
    }


def check_simple_graph(raw_edge_df: pd.DataFrame) -> dict:
    """No self-loops, no parallel edges (after canonicalising to (min,max))."""
    src = raw_edge_df["source"].to_numpy()
    tgt = raw_edge_df["target"].to_numpy()
    self_loops = int((src == tgt).sum())
    pairs = list(zip(np.minimum(src, tgt).tolist(), np.maximum(src, tgt).tolist()))
    seen = set()
    parallels = 0
    for p in pairs:
        if p in seen:
            parallels += 1
        else:
            seen.add(p)
    return {
        "ok": self_loops == 0 and parallels == 0,
        "self_loops": self_loops,
        "parallels": parallels,
        "edges_total": len(pairs),
        "unique_pairs": len(seen),
    }


def check_node_count(raw_edge_df: pd.DataFrame, N: int) -> dict:
    """Every t in 1..N must be incident to >= 1 edge (at least one arrival)."""
    src = raw_edge_df["source"].to_numpy()
    tgt = raw_edge_df["target"].to_numpy()
    seen = set(src.tolist()) | set(tgt.tolist())
    missing = [t for t in range(1, N + 1) if t not in seen]
    return {
        "ok": len(missing) == 0,
        "max_id": int(max(seen)) if seen else 0,
        "missing": missing,
    }


def check_cluster_count(com_df: pd.DataFrame, c: int) -> dict:
    """Per gen_contract_divergences.md: not strict. Log only."""
    unique = int(com_df["cluster_id"].nunique())
    return {
        "expected": c,
        "actual": unique,
        "matches": unique == c,
    }


def _R_of_T(T: float, m: int, N: int, gamma: float) -> float:
    """Same closed form as the JS port + run_npso.m's β=1 / general branches."""
    beta = 1.0 / (gamma - 1.0)
    log_N = math.log(N)
    s = math.sin(math.pi * T)
    if s <= 0:
        return float("inf")
    if abs(beta - 1.0) < 1e-9:
        return 2 * log_N - 2 * math.log((2 * T * log_N) / (s * m))
    num = 2 * T * (1 - math.exp(-(1 - beta) * log_N))
    den = s * m * (1 - beta)
    return 2 * log_N - 2 * math.log(num / den)


def check_connection_probability_floor(raw_edge_df: pd.DataFrame, N: int, m: int,
                                       gamma: float, T: float,
                                       prob_floor: float = 1e-6) -> dict:
    """Sanity check: every emitted edge has a non-trivial best-case
    Fermi-Dirac probability under the converged T.

    We don't own the angular embedding (MATLAB does), so we use the
    distance lower bound h_ij >= |r_i - r_j| (achieved when nodes are
    at the same angle). With h floor we get a per-edge probability
    upper bound; if that upper bound is below `prob_floor` the edge
    is provably out of reach under the model, which would indicate
    a bug. Conversely, if p_upper > prob_floor for every edge, all
    emitted edges respect the connection-by-distance rule in the
    sense that they sit within the model's reachable envelope.
    """
    R = _R_of_T(T, m, N, gamma)
    if not math.isfinite(R) or T <= 0:
        return {"ok": False, "reason": f"R or T degenerate (T={T}, R={R})"}

    beta = 1.0 / (gamma - 1.0)
    log_N = math.log(N)
    src = raw_edge_df["source"].to_numpy()
    tgt = raw_edge_df["target"].to_numpy()
    a = np.minimum(src, tgt).astype(float)
    b = np.maximum(src, tgt).astype(float)
    # Final radial coords after popularity fading: r_t = 2β·ln(t) +
    # 2(1-β)·ln(N). Best-case (smallest) d is |r_a - r_b|.
    r_a = 2 * beta * np.log(a) + 2 * (1 - beta) * log_N
    r_b = 2 * beta * np.log(b) + 2 * (1 - beta) * log_N
    h_lb = np.abs(r_a - r_b)
    p_upper = 1 / (1 + np.exp((h_lb - R) / (2 * T)))
    n_below = int((p_upper < prob_floor).sum())
    return {
        "ok": n_below == 0,
        "T": T,
        "R": R,
        "prob_floor": prob_floor,
        "edges_below_floor": n_below,
        "min_p_upper": float(p_upper.min()) if p_upper.size else None,
    }


def js_run(fixture: dict, seed: int, mjs: Path, replay=None) -> dict:
    """Invoke the JS impl-3 walker via node. `fixture` carries N, m, gamma."""
    payload = {
        "N": fixture["N"],
        "m": fixture["m"],
        "gamma": fixture["gamma"],
        "seed": int(seed),
    }
    if replay is not None:
        payload["replay"] = replay
    proc = subprocess.run(
        ["node", str(mjs)], input=json.dumps(payload),
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def check_js_invariants(js_out: dict, N: int, m: int) -> dict:
    """Re-verify the JS port's checks Python-side (independent confirmation).

    The JS port emits 0-based indices (arrival 0..N-1); we shift to 1-based
    and reuse the MATLAB-style checkers so the JS and MATLAB sides go
    through the same Python codepath.
    """
    edges = [(u + 1, v + 1) for u, v in js_out["edges"]]
    raw = pd.DataFrame(edges, columns=["source", "target"])
    arr = check_per_arrival_m(raw, N, m)
    sim = check_simple_graph(raw)
    nc = check_node_count(raw, N)
    return {
        "per_arrival_m": arr,
        "simple_graph": sim,
        "node_count": nc,
        "edges_total": len(edges),
        "js_self_check": {
            "per_arrival_m_ok": js_out["per_arrival_m_ok"],
            "simple_ok": js_out["simple_ok"],
        },
    }


def matlab_engine_available() -> bool:
    try:
        import matlab.engine  # noqa: F401
        return True
    except Exception:
        return False


def matlab_subprocess_available() -> bool:
    return shutil.which("matlab") is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=[1, 2, 3, 4, 5])
    ap.add_argument("--js-mjs", default=str(Path(__file__).with_name("npso_kernel_check.mjs")))
    ap.add_argument("--max-iters", type=int, default=30,
                    help="Max secant/midpoint iters per nPSO run (small20 normally"
                         " converges in ~5-15).")
    ap.add_argument("--include-larger", action="store_true",
                    help="Also run a 60-node random fixture (skipped by default;"
                         " adds MATLAB startup cost per seed).")
    ap.add_argument("--n-threads", type=int, default=1)
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    js_mjs = Path(args.js_mjs)
    if not js_mjs.exists():
        print(f"FAIL: JS port not at {js_mjs}", file=sys.stderr)
        sys.exit(2)

    # Build fixtures BEFORE probing MATLAB. Importing matlab.engine
    # contaminates the conda env's shared-lib path on this host (it
    # ships its own libstdc++ + libexpat), which then trips
    # later imports of matplotlib/powerlaw used to fit gamma. Order
    # matters: profile-equivalent fitting first, MATLAB engine second.
    fixtures = [small20_fixture()]
    if args.include_larger:
        # Build a 60-node fixture: 4 clusters + 2 outliers, dense intra,
        # sparse inter. Keeps the m, c, gamma stack comparable to
        # small20 but stretches MATLAB iter cost.
        import random
        rng = random.Random(2026)
        sizes = [16, 14, 14, 14, 2]  # sums to 60
        N_LARGER = sum(sizes)
        nodes = list(range(1, N_LARGER + 1))
        cluster_of = {}
        cur = 0
        for ci, s in enumerate(sizes):
            for _ in range(s):
                cluster_of[nodes[cur]] = f"C{ci+1}" if ci < len(sizes) - 1 else "OUT"
                cur += 1
        edges = set()
        for i in range(N_LARGER):
            for j in range(i + 1, N_LARGER):
                u, v = nodes[i], nodes[j]
                p = 0.30 if cluster_of[u] == cluster_of[v] and cluster_of[u] != "OUT" else 0.04
                if rng.random() < p:
                    edges.add((u, v))
        fixtures.append(_build_fixture("rand60", nodes, sorted(edges), cluster_of))

    matlab_status = (
        "engine available" if matlab_engine_available()
        else "engine missing; subprocess available" if matlab_subprocess_available()
        else "skipped: MATLAB unavailable"
    )
    matlab_ok = matlab_engine_available() or matlab_subprocess_available()
    print(f"MATLAB status: {matlab_status}")

    overall_ok = True
    js_results = []

    def _arr_tag(arr):
        return "OK" if arr["ok"] else "BAD " + str(arr["bad"][:3])

    def _sim_tag(sim):
        if sim["ok"]:
            return "OK"
        return f"BAD loops={sim['self_loops']} dups={sim['parallels']}"

    def _nc_tag(nc):
        return "OK" if nc["ok"] else "missing=" + str(nc["missing"][:3])

    def _pf_tag(pf):
        if pf.get("ok"):
            return "OK"
        if "reason" in pf:
            return "BAD " + pf["reason"]
        return f"BAD edges_below_floor={pf.get('edges_below_floor')} min_p={pf.get('min_p_upper')}"

    def _fmt_T(val):
        return f"{val:.5f}" if val is not None else "none"

    # ── JS-only invariants (always runs, MATLAB-independent) ──
    bar = "=" * 60
    print(f"\n{bar}")
    print("JS impl-3 walker invariants (MATLAB-independent)")
    print(bar)
    for fx in fixtures:
        print(
            f"\n--- fixture {fx['name']}: N={fx['N']} m={fx['m']} "
            f"gamma={fx['gamma']:.3f} c={fx['c']} target_cc={fx['target_cc']:.4f} ---"
        )
        for seed in args.seeds:
            js = js_run(fx, seed, js_mjs)
            js_inv = check_js_invariants(js, fx["N"], fx["m"])
            js_results.append((fx["name"], seed, js))
            arr = js_inv["per_arrival_m"]
            sim = js_inv["simple_graph"]
            nc = js_inv["node_count"]
            ok = arr["ok"] and sim["ok"] and nc["ok"]
            overall_ok = overall_ok and ok
            print(
                f"  seed={seed}: edges={js_inv['edges_total']} "
                f"per_arrival_m={_arr_tag(arr)} "
                f"simple={_sim_tag(sim)} "
                f"nodes_seen={_nc_tag(nc)}"
            )

        # JS replay: take seed-1 trace, feed it back into a fresh JS run, expect
        # bit-for-bit edge equality.
        ref_name, ref_seed, ref_js = js_results[-len(args.seeds)]
        replay = ref_js["U_NODE"]
        rep = js_run(fx, ref_seed, js_mjs, replay=replay)
        rep_inv = check_js_invariants(rep, fx["N"], fx["m"])
        edges_match = (rep["edges"] == ref_js["edges"])
        rep_ok = (
            rep_inv["per_arrival_m"]["ok"]
            and rep_inv["simple_graph"]["ok"]
            and edges_match
        )
        overall_ok = overall_ok and rep_ok
        rep_arr_tag = "OK" if rep_inv["per_arrival_m"]["ok"] else "BAD"
        rep_sim_tag = "OK" if rep_inv["simple_graph"]["ok"] else "BAD"
        print(
            f"  replay (seed={ref_seed}): "
            f"per_arrival_m={rep_arr_tag} simple={rep_sim_tag} "
            f"edges_eq_replayed_seed_run={edges_match}"
        )

    # ── Actual nPSO via MATLAB ──
    if not matlab_ok:
        print(f"\n{bar}")
        print("Actual-generator stage skipped: MATLAB unavailable.")
        print(bar)
        print("\nOVERALL: " + ("PASS (JS-only)" if overall_ok else "FAIL"))
        sys.exit(0 if overall_ok else 1)

    print(f"\n{bar}")
    print("Actual nPSO generator end-to-end")
    print(bar)
    with tempfile.TemporaryDirectory(prefix="npso_kernel_") as scratch_str:
        scratch_dir = Path(scratch_str)
        for fx in fixtures:
            print(f"\n--- fixture {fx['name']}: N={fx['N']} m={fx['m']} c={fx['c']} ---")
            for seed in args.seeds:
                try:
                    res = run_actual_npso(
                        fx, seed, scratch_dir,
                        n_threads=args.n_threads,
                        max_iters=args.max_iters,
                    )
                except Exception as exc:
                    print(f"  seed={seed}: FAIL ({type(exc).__name__}: {exc})")
                    overall_ok = False
                    continue

                raw = res["raw_edge_df"]
                arr = check_per_arrival_m(raw, fx["N"], fx["m"])
                sim = check_simple_graph(raw)
                nc = check_node_count(raw, fx["N"])
                cc_check = check_cluster_count(res["com_df"], fx["c"])
                if res["best_T"] is not None:
                    pf = check_connection_probability_floor(
                        raw, fx["N"], fx["m"], fx["gamma"], res["best_T"],
                    )
                else:
                    pf = {"ok": False, "reason": "no best_T"}

                ok = arr["ok"] and sim["ok"] and nc["ok"] and pf["ok"]
                overall_ok = overall_ok and ok

                best_cc = res["best_cc"] if res["best_cc"] is not None else float("nan")
                residual = (
                    abs(res["best_cc"] - fx["target_cc"])
                    if res["best_cc"] is not None else float("nan")
                )
                print(
                    f"  seed={seed}: edges_raw={len(raw)} elapsed={res['elapsed']:.1f}s "
                    f"best_T={_fmt_T(res['best_T'])} best_cc={best_cc:.4f} "
                    f"target={fx['target_cc']:.4f} residual={residual:.4f}"
                )
                print(
                    f"           per_arrival_m={_arr_tag(arr)} simple={_sim_tag(sim)} "
                    f"node_count={_nc_tag(nc)} prob_floor={_pf_tag(pf)}"
                )
                tag = (
                    "EXACT" if cc_check["matches"]
                    else "DIVERGES (per memo gen_contract_divergences.md item 4)"
                )
                print(
                    f"           cluster_count: expected c={cc_check['expected']} "
                    f"actual_unique={cc_check['actual']} {tag}"
                )

    print(f"\n{bar}")
    print("OVERALL: " + ("PASS" if overall_ok else "FAIL"))
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
