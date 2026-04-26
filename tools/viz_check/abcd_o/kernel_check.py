"""ABCD+o kernel cross-check.

Drives the Julia ABCD sampler with `n_outliers > 0` (the same `graph_sampler.jl`
that `src/abcd+o/gen.py` invokes), checks the additional contract from
``memory/gen_abcd_o.md`` on top of the base ABCD invariants:

  * **Outlier mega-cluster is cluster_id=1** in raw sampler output, with
    exactly `n_outliers` members.
  * **Non-outlier degree sequence still exact** — every non-outlier
    node's output degree equals its input degree (the contract is the
    same as base ABCD; outliers also have their stub count preserved
    in the sampler but rewiring can perturb).
  * **Real cluster size multiset exact** (ignoring the outlier block).
  * **OO edges permitted** — outlier-outlier edges are NOT forbidden by
    the sampler. The harness counts them and reports without failing.
  * **Simple graph** — no self-loops, no parallels in `edge.csv` after
    `simplify_edges` runs.
  * **Global ξ within tolerance** — computed treating outlier-incident
    edges as cross (matches profile's `drop_oo=True` semantics).
  * **Outlier-lift warning detection** — when the sampler emits the
    "outlier nodes form a community" warning, the post-processed
    `com.csv` keeps cluster_id=1; otherwise it strips them. The harness
    reports the path taken.

Run:

    conda run -n nwbench python tools/abcd_o/kernel_check.py
    conda run -n nwbench python tools/abcd_o/kernel_check.py --seeds 1 2 3

Fixtures:
  * **small20_o2**     — 20 nodes / 4 clusters, 2 outliers (mirrors shared.js).
  * **rand100_5c_o5** — 100 nodes / 5 planted clusters, 5 outliers.

PASS for a (fixture, seed) row means: every invariant above held. The
bottom-line `OVERALL: PASS` means every row passed.
"""
from __future__ import annotations

import argparse
import logging
import random
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
ABCD_DIR = REPO_ROOT / "externals" / "abcd"
ABCD_SAMPLER = ABCD_DIR / "utils" / "graph_sampler.jl"
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))


OUTLIER_LIFT_WARNING = "outlier nodes form a community"
SAMPLER_FLAGS = [
    ("very_hard", "Very hard graph"),
    ("xi_biased", "Resulting ξ might be slightly biased"),
    ("unresolved", "Unresolved_collisions:"),
    ("outlier_lift", OUTLIER_LIFT_WARNING),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def small20_o2_fixture() -> dict:
    """20-node, 40-edge fixture mirroring vltanh.github.io/netgen/shared.js
    with nodes 19, 20 marked as 2 outliers (the shared.js OUT block).

    With `drop_outlier_outlier_edges=True`, edges (19,20) is excluded
    from the input degree counts. That matches what the abcd+o profile
    would produce with default flags.
    """
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
    for n in OUT: cluster_of[n] = "__OUT__"
    return _build_fixture("small20_o2", nodes, edges, cluster_of, OUT,
                          drop_oo=True)


def random_o_fixture(name: str, n: int, k: int, n_outliers: int, seed: int,
                     p_intra: float = 0.30, p_inter: float = 0.02) -> dict:
    """Random fixture: n nodes, k planted clusters + `n_outliers` background.

    The first `n_outliers` of the n node IDs are marked outliers (they
    have cross-cluster Bernoulli-only edges, no intra-cluster prior).
    """
    rng = random.Random(seed)
    sizes = [(n - n_outliers) // k] * k
    for i in range((n - n_outliers) - sum(sizes)):
        sizes[i] += 1
    nodes = list(range(1, n + 1))
    cluster_of = {}
    cur = 0
    OUT = list(nodes[:n_outliers])
    for nid in OUT:
        cluster_of[nid] = "__OUT__"
        cur += 1
    for ci in range(k):
        cname = f"C{ci+1}"
        for _ in range(sizes[ci]):
            cluster_of[nodes[cur]] = cname
            cur += 1
    edges = set()
    for i in range(n):
        for j in range(i + 1, n):
            u, v = nodes[i], nodes[j]
            cu, cv = cluster_of[u], cluster_of[v]
            if cu == "__OUT__" and cv == "__OUT__":
                # Outlier-outlier — generate at the inter rate.
                p = p_inter
            elif cu == "__OUT__" or cv == "__OUT__":
                p = p_inter
            else:
                same = cu == cv
                p = p_intra if same else p_inter
            if rng.random() < p:
                edges.add((u, v))
    return _build_fixture(name, nodes, sorted(edges), cluster_of, OUT,
                          drop_oo=True)


def _build_fixture(name: str, nodes: list, edges: list, cluster_of: dict,
                   outlier_nodes: list, drop_oo: bool) -> dict:
    """Compute degree sequence + real cluster sizes + global ξ + n_outliers.

    Mirrors what `src/abcd+o/profile.py` does with default flags
    (singleton + drop_oo=True):
      - Build per-node degree, with OO edges optionally dropped.
      - Sort nodes by descending degree, ascending id on ties.
      - Build real cluster sizes (only true clusters, NOT the outlier
        block) sorted descending.
      - ξ = Σ_out / Σ_total over the same edge set used for degree.
      - n_outliers = |outlier_nodes|.
    """
    outlier_set = set(outlier_nodes)
    if drop_oo:
        edges_for_profile = [
            (u, v) for u, v in edges
            if not (u in outlier_set and v in outlier_set)
        ]
    else:
        edges_for_profile = list(edges)

    degree = {n: 0 for n in nodes}
    for u, v in edges_for_profile:
        degree[u] += 1
        degree[v] += 1
    nodes_sorted = sorted(nodes, key=lambda n: (-degree[n], n))
    degree_seq = [degree[n] for n in nodes_sorted]

    # Real cluster sizes only (skip __OUT__).
    real_counter = Counter(
        cluster_of[n] for n in nodes if cluster_of[n] != "__OUT__"
    )
    real_sizes_sorted = sorted(
        real_counter.items(), key=lambda kv: (-kv[1], kv[0]),
    )
    cluster_sizes = [sz for _, sz in real_sizes_sorted]

    out_count = 0
    total = 0
    for u, v in edges_for_profile:
        if cluster_of[u] != cluster_of[v]:
            out_count += 2
        total += 2
    xi = out_count / total if total else 0.0

    return {
        "name": name,
        "nodes": nodes_sorted,        # iid 1..N -> node id (descending-degree order)
        "degree_seq": degree_seq,
        "cluster_of": cluster_of,
        "cluster_sizes": cluster_sizes,  # real-cluster sizes only, descending
        "xi": xi,
        "n_outliers": len(outlier_nodes),
        "outlier_nodes": outlier_nodes,
        "edges_input": list(edges),
        "edges_for_profile": edges_for_profile,
        "drop_oo": drop_oo,
    }


# ---------------------------------------------------------------------------
# Sampler invocation (same as ABCD's, with n_outliers passed)
# ---------------------------------------------------------------------------


def _profile_inputs(fx: dict, work: Path) -> tuple[Path, Path, float, int]:
    """Write deg.tsv + cs_with_outliers.tsv (outlier block prepended)."""
    deg_path = work / "deg.tsv"
    cs_path = work / "cs_with_outliers.tsv"
    pd.DataFrame(fx["degree_seq"]).to_csv(deg_path, sep="\t",
                                          header=False, index=False)
    cs_rows: list = []
    n_outliers = fx["n_outliers"]
    if n_outliers > 0:
        cs_rows.append([n_outliers])
    cs_rows.extend([[s] for s in fx["cluster_sizes"]])
    pd.DataFrame(cs_rows).to_csv(cs_path, sep="\t", header=False, index=False)
    return deg_path, cs_path, fx["xi"], n_outliers


def _run_julia_sampler(deg_path: Path, cs_path: Path, xi: float, seed: int,
                       n_outliers: int, work: Path) -> tuple[Path, Path, str]:
    edge_tsv = work / "edge.tsv"
    com_tsv = work / "com.tsv"
    cmd = [
        "julia", str(ABCD_SAMPLER),
        str(edge_tsv), str(com_tsv),
        str(deg_path), str(cs_path),
        "xi", str(xi), "false", "false", str(seed), str(n_outliers),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Julia sampler failed (rc={proc.returncode}):\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return edge_tsv, com_tsv, proc.stderr


def _post_process(edge_tsv: Path, com_tsv: Path, n_outliers: int,
                  outliers_lifted: bool, out_dir: Path) -> tuple[Path, Path]:
    """Apply gen.py's post-processing: simplify_edges, optional cluster_id=1
    strip when outliers are not lifted, then drop_singleton_clusters."""
    from pipeline_common import drop_singleton_clusters, simplify_edges

    out_dir.mkdir(parents=True, exist_ok=True)
    edge_df = pd.read_csv(edge_tsv, sep="\t", header=None,
                          names=["source", "target"])
    com_df = pd.read_csv(com_tsv, sep="\t", header=None,
                         names=["node_id", "cluster_id"])
    if n_outliers > 0 and not outliers_lifted:
        com_df = com_df[com_df["cluster_id"] != 1]
    logging.disable(logging.CRITICAL)
    try:
        edge_df = simplify_edges(edge_df)
        com_df = drop_singleton_clusters(com_df)
    finally:
        logging.disable(logging.NOTSET)
    edge_csv = out_dir / "edge.csv"
    com_csv = out_dir / "com.csv"
    edge_df.to_csv(edge_csv, index=False)
    com_df.to_csv(com_csv, index=False)
    return edge_csv, com_csv


# ---------------------------------------------------------------------------
# Invariant checks
# ---------------------------------------------------------------------------


def _xi_tol_for(fx: dict, base_tol: float) -> float:
    n_edges = len(fx["edges_for_profile"])
    if n_edges < 100:
        return max(base_tol, 0.15)
    return base_tol


def _check_invariants(fx: dict, edge_tsv: Path, com_tsv: Path,
                      edge_csv: Path, com_csv: Path,
                      outliers_lifted: bool, xi_tol: float) -> tuple[bool, dict]:
    """Verify the abcd+o contract on (a) raw sampler output and (b) post-processed CSV."""
    raw_edges = pd.read_csv(edge_tsv, sep="\t", header=None,
                            names=["source", "target"])
    raw_com = pd.read_csv(com_tsv, sep="\t", header=None,
                          names=["node_id", "cluster_id"])
    final_edges = pd.read_csv(edge_csv)
    final_com = pd.read_csv(com_csv)

    # 1. Outlier mega-cluster: exactly n_outliers nodes have cluster_id=1
    #    in the raw output. (Post-strip, this may be 0.)
    raw_outliers = set(
        raw_com.loc[raw_com["cluster_id"] == 1, "node_id"].astype(int)
    )
    n_outliers_expected = fx["n_outliers"]
    outliers_count_ok = (len(raw_outliers) == n_outliers_expected)

    # 2. Per-node deg-exact for ALL nodes (sampler preserves stub counts;
    #    rewiring may slightly perturb). We use the RAW edge.tsv since
    #    simplify_edges drops parallels which would otherwise hide the
    #    fact that a stub got allocated.
    raw_deg: Counter = Counter()
    for u, v in zip(raw_edges["source"], raw_edges["target"]):
        raw_deg[int(u)] += 1
        raw_deg[int(v)] += 1
    expected_deg = {i + 1: d for i, d in enumerate(fx["degree_seq"])}
    deg_mismatches = []
    for nid, exp in expected_deg.items():
        got = raw_deg.get(nid, 0)
        if got != exp:
            deg_mismatches.append((nid, exp, got))
    deg_ok_all = len(deg_mismatches) == 0

    # 2b. Same check restricted to NON-outlier nodes (in case the sampler
    #     differentially perturbs outlier degrees when cleaning up; per
    #     memory, sampler treats all stubs uniformly but rewiring can
    #     touch outliers more often).
    non_outlier_mismatches = [
        (nid, exp, got) for nid, exp, got in deg_mismatches
        if nid not in raw_outliers
    ]
    deg_ok_non_outlier = len(non_outlier_mismatches) == 0

    # 3. Real cluster size multiset (ignore outlier block) matches input.
    expected_real_sizes = sorted(
        [s for s in fx["cluster_sizes"] if s > 1], reverse=True,
    )
    raw_real_sizes = sorted(
        [c for cid, c in Counter(raw_com["cluster_id"]).items() if cid != 1],
        reverse=True,
    )
    sizes_ok = raw_real_sizes == expected_real_sizes

    # 4. OO edge count (informational; permitted by the sampler).
    oo_edges = sum(
        1 for u, v in zip(raw_edges["source"], raw_edges["target"])
        if int(u) in raw_outliers and int(v) in raw_outliers
    )

    # 5. Simple graph postcondition on the FINAL edge.csv.
    self_loops = int((final_edges["source"] == final_edges["target"]).sum())
    pair_keys = list(zip(
        final_edges[["source", "target"]].min(axis=1),
        final_edges[["source", "target"]].max(axis=1),
    ))
    parallels = len(pair_keys) - len(set(pair_keys))
    simple_ok = (self_loops == 0) and (parallels == 0)

    # 6. Global ξ on the RAW edge set, with raw cluster_id from com.tsv
    #    (so cluster_id=1 still classifies outliers as a unit). This matches
    #    the profile's xi computation when drop_oo=True (we already
    #    applied that in the fixture builder).
    raw_com_map = dict(zip(raw_com["node_id"].astype(int),
                            raw_com["cluster_id"]))
    out_sum = 0
    total = 0
    for u, v in zip(raw_edges["source"], raw_edges["target"]):
        cu = raw_com_map.get(int(u))
        cv = raw_com_map.get(int(v))
        # Skip OO edges from xi (matches profile drop_oo=True).
        if int(u) in raw_outliers and int(v) in raw_outliers:
            continue
        if cu != cv:
            out_sum += 2
        total += 2
    measured_xi = out_sum / total if total else 0.0
    xi_delta = abs(measured_xi - fx["xi"])
    xi_ok = xi_delta <= xi_tol

    return (
        outliers_count_ok and deg_ok_all and sizes_ok and simple_ok and xi_ok,
        {
            "raw_edges": len(raw_edges),
            "final_edges": len(final_edges),
            "raw_outliers": len(raw_outliers),
            "outliers_count_ok": outliers_count_ok,
            "outliers_lifted": outliers_lifted,
            "deg_ok_all": deg_ok_all,
            "deg_ok_non_outlier": deg_ok_non_outlier,
            "n_deg_mismatches_all": len(deg_mismatches),
            "n_deg_mismatches_non_outlier": len(non_outlier_mismatches),
            "deg_mismatches_first5": deg_mismatches[:5],
            "sizes_ok": sizes_ok,
            "got_sizes_first5": raw_real_sizes[:5],
            "expected_sizes_first5": expected_real_sizes[:5],
            "oo_edges": oo_edges,
            "self_loops": self_loops,
            "parallels": parallels,
            "simple_ok": simple_ok,
            "measured_xi": measured_xi,
            "target_xi": fx["xi"],
            "xi_delta": xi_delta,
            "xi_ok": xi_ok,
        },
    )


def _summarise_stderr(stderr: str) -> str:
    flags = []
    for tag, needle in SAMPLER_FLAGS:
        if needle in stderr:
            flags.append(tag)
    return ",".join(flags) if flags else "-"


# ---------------------------------------------------------------------------
# Per-row driver
# ---------------------------------------------------------------------------


def _run_one(fx: dict, seed: int, base_xi_tol: float, verbose: bool) -> bool:
    xi_tol = _xi_tol_for(fx, base_xi_tol)
    print(f"\n  fixture={fx['name']:<14s} seed={seed:>3d}  "
          f"N={len(fx['nodes'])} E_input={len(fx['edges_input'])} "
          f"E_profile={len(fx['edges_for_profile'])} "
          f"n_outliers={fx['n_outliers']} "
          f"xi_target={fx['xi']:.4f}  xi_tol={xi_tol}")
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        deg_path, cs_path, xi, n_outliers = _profile_inputs(fx, work)
        edge_tsv, com_tsv, stderr = _run_julia_sampler(
            deg_path, cs_path, xi, seed, n_outliers, work,
        )
        outliers_lifted = bool(re.search(OUTLIER_LIFT_WARNING, stderr,
                                          re.IGNORECASE))
        edge_csv, com_csv = _post_process(
            edge_tsv, com_tsv, n_outliers, outliers_lifted, work / "out",
        )
        ok, info = _check_invariants(fx, edge_tsv, com_tsv,
                                     edge_csv, com_csv,
                                     outliers_lifted, xi_tol=xi_tol)
        flags = _summarise_stderr(stderr)
        status = "PASS" if ok else "FAIL"
        print(f"    {status}: raw_edges={info['raw_edges']:>4d} "
              f"final_edges={info['final_edges']:>4d} "
              f"raw_cid1_count={info['raw_outliers']} "
              f"(expected {n_outliers}, ok={info['outliers_count_ok']})  "
              f"oo_edges={info['oo_edges']}  "
              f"outliers_lifted={info['outliers_lifted']}")
        print(f"           simple={info['simple_ok']} "
              f"deg_exact_all={info['deg_ok_all']} "
              f"(non_outlier_only={info['deg_ok_non_outlier']}) "
              f"sizes_exact={info['sizes_ok']} "
              f"xi_measured={info['measured_xi']:.4f} "
              f"xi_delta={info['xi_delta']:.4f} -> xi_ok={info['xi_ok']}  "
              f"sampler_flags={flags}")
        if not ok:
            if not info["outliers_count_ok"]:
                print(f"      raw cluster_id=1 count mismatch: "
                      f"got {info['raw_outliers']}, expected {n_outliers}")
            if not info["deg_ok_all"]:
                print(f"      degree mismatches: {info['n_deg_mismatches_all']} node(s) "
                      f"({info['n_deg_mismatches_non_outlier']} non-outlier); "
                      f"first 5 (node, expected, got): "
                      f"{info['deg_mismatches_first5']}")
            if not info["sizes_ok"]:
                print(f"      real-cluster sizes mismatch:")
                print(f"        expected (top 5, desc): {info['expected_sizes_first5']}")
                print(f"        got      (top 5, desc): {info['got_sizes_first5']}")
            if not info["xi_ok"]:
                print(f"      xi out of tolerance: |Δ|={info['xi_delta']:.4f} > {xi_tol}")
            if not info["simple_ok"]:
                print(f"      simplify_edges postcondition violated: "
                      f"loops={info['self_loops']} parallels={info['parallels']}")
        if verbose and stderr:
            for line in stderr.splitlines():
                print(f"      [julia stderr] {line}")
        return ok


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, nargs="*", default=list(range(1, 6)),
                    help="Seeds to run per fixture (default: 1..5).")
    ap.add_argument("--xi-tol", type=float, default=0.05,
                    help="Tolerance on |measured xi - target xi| for "
                         "fixtures with E>=100 (default: 0.05). E<100 uses 0.15.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print Julia stderr for every row.")
    args = ap.parse_args()

    if not ABCD_SAMPLER.exists():
        print(f"FAIL: ABCD sampler not found at {ABCD_SAMPLER}", file=sys.stderr)
        sys.exit(2)

    fixtures = [
        small20_o2_fixture(),
        random_o_fixture("rand100_5c_o5", n=100, k=5, n_outliers=5, seed=42),
    ]

    print("=" * 76)
    print("ABCD+o kernel cross-check")
    print("=" * 76)
    print(f"  Sampler:   {ABCD_SAMPLER}")
    print(f"  Seeds:     {args.seeds}")
    print(f"  Fixtures:  {[fx['name'] for fx in fixtures]}")
    print(f"  xi tol:    {args.xi_tol} (auto-loosened to 0.15 for fixtures with E<100)")
    print()
    print("PASS criteria per (fixture, seed):")
    print("  * outliers_count_ok -> raw com.tsv has exactly n_outliers nodes with cluster_id=1")
    print("  * deg_exact_all     -> raw output per-node degree == input degree (all nodes)")
    print("  * sizes_exact       -> real-cluster size multiset matches input (size>=2, ignoring cluster_id=1)")
    print("  * simple            -> final edge.csv has no self-loops + no parallels")
    print("  * xi_ok             -> |measured xi - target xi| <= xi_tol")
    print()
    print("Reported but NOT a PASS criterion:")
    print("  * oo_edges          -> count of outlier-outlier edges (sampler permits these)")
    print("  * outliers_lifted   -> warning fired = keep cluster_id=1 in com.csv; silent = strip")
    print()
    print("sampler_flags reports stderr findings worth knowing about:")
    for tag, needle in SAMPLER_FLAGS:
        print(f"  {tag:<12s} -> matches '{needle}'")

    overall = True
    n_pass = 0
    n_total = 0
    for fx in fixtures:
        print(f"\n=== fixture: {fx['name']} ===")
        for seed in args.seeds:
            ok = _run_one(fx, seed, args.xi_tol, args.verbose)
            n_total += 1
            n_pass += int(ok)
            overall = overall and ok

    print()
    print("=" * 76)
    print(f"OVERALL: {'PASS' if overall else 'FAIL'}  ({n_pass}/{n_total} rows passed)")
    print("=" * 76)
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
