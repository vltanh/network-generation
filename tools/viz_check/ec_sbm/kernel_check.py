"""EC-SBM v1 + v2 verification harness.

Drives ``src/ec-sbm/pipeline.sh`` end-to-end on at least two fixtures
and at least five seeds per version, then verifies the documented
guarantees against the artifacts under ``--keep-state``.

Common to v1 + v2:

* Per-cluster edge connectivity ≥ k(C) where k(C) is the input's
  per-cluster min cut (Nagamochi-Ono-Ibaraki via ``pymincut``).
* Output ``com.csv`` matches the input clustering exactly for every
  clustered node. Outlier handling differs: v1 emits singleton outlier
  ids (``__outlier_<id>__``), v2 folds outliers into the combined
  ``__outliers__`` cluster. Both versions force ``outlier_mode=excluded``
  at profile time so neither emits outliers as members of any input
  cluster.
* ``edge.csv`` is a simple graph (no parallel rows under (min, max)
  dedup, no self-loops).
* The constructive K_{k+1} clique on the top-(k+1) input-degree nodes
  per cluster is present in the final ``edge.csv``. Phase 1 places
  these edges before any other stage runs, and no later stage removes
  them.

v1-specific:

* The greedy-attach matcher (stage 4a) leaves no degree deficit:
  output per-node degree ≥ input per-node degree for every node in
  ``degree.csv`` (the matcher may overshoot; that's expected).

v2-specific:

* The block-preserving 2-opt rewire in stage 3a is invariant on
  block-pair counts: e_rs of the SBM sample (post-rewire, pre
  parallel/self-loop removal) is ≤ e_rs of the SBM sample (pre-rewire)
  per block-pair — rewire never moves an edge across a (min_block,
  max_block) bucket. Implemented by re-running ``synthesize_residual_subnetwork``
  in-process with the kept stage-3a inputs at ``edge_correction='rewire'``
  vs ``edge_correction='none'`` from the same seeded RNG state.

Usage::

    conda run -n nwbench python tools/ec_sbm/kernel_check.py
    conda run -n nwbench python tools/ec_sbm/kernel_check.py --seeds 1 2 3
    conda run -n nwbench python tools/ec_sbm/kernel_check.py --verbose

Pattern follows ``tools/sbm/kernel_check.py``: build the fixture in
memory, drive the pipeline as a subprocess, then assert invariants
from the on-disk artifacts.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE = REPO_ROOT / "src" / "ec-sbm" / "pipeline.sh"
EC_SBM_PACKAGE = REPO_ROOT / "externals" / "ec-sbm"
EC_SBM_PY = EC_SBM_PACKAGE / "src"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def small20_fixture() -> dict:
    """20-node, 40-edge fixture from ``vltanh.github.io/netgen/shared.js``.

    Cluster sizes: C1=8, C2=6, C3=4, plus outliers {19, 20}. INTRA.C1 is
    a K_4 on {1,2,3,4} + diamond on {5..8} joined by 2 bridges (mincut
    = 2). INTRA.C2 is K_4 on {9..12} + tails 13, 14 (mincut = 2 by
    isolating either tail). INTRA.C3 is a triangle on {15,16,17} + leaf
    18 off 16 (mincut = 1).

    Trap: the *prior* shared.js had INTRA.C2 including (13,14) which
    pushed C2's true mincut to 3 — so the older trace would FAIL the
    invariant test on C2. The current fixture is consistent.
    """
    intra_c1 = [
        (1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4),   # K_4 on {1,2,3,4}
        (5, 6), (5, 7), (6, 7), (6, 8), (7, 8),            # diamond on {5..8}
        (1, 5), (4, 8),                                     # 2 bridges
    ]
    intra_c2 = [
        (9, 10), (9, 11), (9, 12), (9, 13),
        (10, 11), (10, 12), (10, 14),
        (11, 12), (11, 14),
        (12, 13),
    ]
    intra_c3 = [(15, 16), (15, 17), (16, 17), (16, 18)]
    inter = [
        (1, 9), (2, 10), (3, 11), (5, 12),
        (9, 15), (11, 16),
        (1, 15), (4, 17),
    ]
    out_edges = [(19, 1), (19, 9), (20, 5), (20, 16), (19, 20)]

    edges = intra_c1 + intra_c2 + intra_c3 + inter + out_edges
    cluster_of = {}
    for n in range(1, 9):    cluster_of[n] = "C1"
    for n in range(9, 15):   cluster_of[n] = "C2"
    for n in range(15, 19):  cluster_of[n] = "C3"
    return {
        "name": "small20",
        "edges": edges,
        "cluster_of": cluster_of,   # only clustered nodes; outliers absent
        "outliers": [19, 20],
        "expected_mincut": {"C1": 2, "C2": 2, "C3": 1},
    }


def synthetic100_fixture() -> dict:
    """5 clusters of 20, each a 4-regular circulant (mincut = 4 per cluster).

    A circulant C(n; {1, 2}) is the graph on 0..n-1 with edges (i, i+1
    mod n) and (i, i+2 mod n). For n ≥ 5 it is 4-edge-connected — the
    minimum vertex degree is 4, and the construction is enough to
    guarantee 4-edge-connectivity (every pair of nodes has 4
    edge-disjoint paths).

    On top: a sparse inter-cluster random graph (~10 edges total) so
    the profile sees a non-trivial block matrix without bleeding into
    each cluster's induced subgraph mincut. Plus 4 unclustered
    outliers connected to a few random nodes — enough to exercise both
    v1 (singleton mode) and v2 (combined mode) outlier handling.

    Mincuts on the induced subgraphs: every cluster = 4. Stage 1's
    K_{k+1} = K_5 builds the seed clique on the top-5 nodes by
    residual degree; phase 2 attaches the remaining 15 with k=4 edges
    each.
    """
    rng = np.random.default_rng(42)
    n_clusters = 5
    nodes_per_cluster = 20
    edges = []
    cluster_of = {}

    nid = 1
    for ci in range(n_clusters):
        cname = f"C{ci+1}"
        cluster_nodes = []
        for _ in range(nodes_per_cluster):
            cluster_of[nid] = cname
            cluster_nodes.append(nid)
            nid += 1
        n = len(cluster_nodes)
        for i in range(n):
            edges.append((cluster_nodes[i], cluster_nodes[(i + 1) % n]))
            edges.append((cluster_nodes[i], cluster_nodes[(i + 2) % n]))

    clustered = [n for n in cluster_of]
    inter_count = 12
    seen = set()
    placed = 0
    while placed < inter_count:
        u, v = rng.choice(clustered, size=2, replace=False)
        u, v = int(u), int(v)
        if cluster_of[u] == cluster_of[v]: continue
        key = (min(u, v), max(u, v))
        if key in seen: continue
        seen.add(key)
        edges.append((u, v))
        placed += 1

    outliers = [nid + i for i in range(4)]
    for o in outliers:
        for _ in range(2):
            partner = int(rng.choice(clustered))
            edges.append((o, partner))
    edges.append((outliers[0], outliers[1]))   # OO edge

    return {
        "name": "synth100_5c_circulant",
        "edges": edges,
        "cluster_of": cluster_of,
        "outliers": outliers,
        "expected_mincut": {f"C{i+1}": 4 for i in range(n_clusters)},
    }


# ---------------------------------------------------------------------------
# Disk + pipeline driving
# ---------------------------------------------------------------------------

def write_fixture(fx: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write the fixture as edge.csv + com.csv. Returns (edgelist, clustering)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    edgelist = out_dir / "edge.csv"
    clustering = out_dir / "com.csv"
    pd.DataFrame(fx["edges"], columns=["source", "target"]).astype(str).to_csv(
        edgelist, index=False,
    )
    pd.DataFrame(
        [(str(n), c) for n, c in fx["cluster_of"].items()],
        columns=["node_id", "cluster_id"],
    ).to_csv(clustering, index=False)
    return edgelist, clustering


def run_pipeline(version: str, edgelist: Path, clustering: Path,
                 output_dir: Path, seed: int) -> subprocess.CompletedProcess:
    """Drive ``src/ec-sbm/pipeline.sh`` with ``--keep-state``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "1"
    cmd = [
        "bash", str(PIPELINE),
        "--input-edgelist", str(edgelist),
        "--input-clustering", str(clustering),
        "--output-dir", str(output_dir),
        "--package-dir", str(EC_SBM_PACKAGE),
        "--version", version,
        "--seed", str(seed),
        "--n-threads", "1",
        "--keep-state",
    ]
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Min-cut / connectivity helpers
# ---------------------------------------------------------------------------

def edge_connectivity_of_subgraph(nodes: list[int], edges: list[tuple]) -> int:
    """Edge connectivity of the induced subgraph via pymincut (NOI).

    Falls back to brute-force max-flow when pymincut isn't on PYTHONPATH
    (it is in the nwbench env). Mirrors ``profile.compute_mincut``: same
    library, same ``"noi"`` / ``"bqueue"`` settings.

    Important: pymincut treats supplied edges as directed. The profile's
    ``compute_mincut`` walks each undirected edge from both endpoints
    (``for u in nodes: for v in neighbors[u]``), feeding pymincut both
    directions. We do the same here so our reported k matches what
    ``mincut.csv`` stores. Otherwise pymincut undercounts by 2x on the
    canonical/dedup-only edge list.
    """
    if len(nodes) <= 1:
        return 0
    n_set = set(nodes)
    sub_edges = [(u, v) for (u, v) in edges if u in n_set and v in n_set]
    if not sub_edges:
        return 0
    nodes_idx = list(nodes)
    iid_of = {n: i for i, n in enumerate(nodes_idx)}
    try:
        from pymincut.pygraph import PyGraph
        e_iid = []
        for u, v in sub_edges:
            iu, iv = iid_of[u], iid_of[v]
            e_iid.append((iu, iv))
            e_iid.append((iv, iu))
        g = PyGraph(list(range(len(nodes_idx))), e_iid)
        return int(g.mincut("noi", "bqueue", False)[2])
    except Exception:
        return _brute_min_cut(nodes_idx, sub_edges)


def _brute_min_cut(nodes: list[int], edges: list[tuple]) -> int:
    """Edmonds-Karp pairwise max-flow; only used as a fallback / sanity check."""
    n = len(nodes)
    if n < 2: return 0
    iid = {nd: i for i, nd in enumerate(nodes)}
    base = defaultdict(int)
    for u, v in edges:
        base[(iid[u], iid[v])] += 1
        base[(iid[v], iid[u])] += 1

    def maxflow(s, t):
        cap = defaultdict(int)
        for k, val in base.items(): cap[k] = val
        flow = 0
        while True:
            parent = {s: None}
            q = deque([s])
            while q and t not in parent:
                u = q.popleft()
                for v in range(n):
                    if v in parent: continue
                    if cap[(u, v)] > 0:
                        parent[v] = u
                        q.append(v)
            if t not in parent: break
            v = t
            while parent[v] is not None:
                u = parent[v]
                cap[(u, v)] -= 1
                cap[(v, u)] += 1
                v = u
            flow += 1
        return flow

    best = float("inf")
    s = 0
    for t in range(1, n):
        val = maxflow(s, t)
        if val < best:
            best = val
            if best == 0: break
    return int(best)


# ---------------------------------------------------------------------------
# Invariant checks
# ---------------------------------------------------------------------------

def assert_simple_graph(edges: list[tuple]) -> tuple[int, int]:
    """Return (parallels, loops). Both must be 0 for a simple graph."""
    seen = set()
    parallels = 0
    loops = 0
    for u, v in edges:
        if u == v: loops += 1
        key = (u, v) if u <= v else (v, u)
        if key in seen: parallels += 1
        else: seen.add(key)
    return parallels, loops


def check_cluster_assignment(out_com: pd.DataFrame, fx: dict, version: str) -> tuple[bool, str]:
    """Output cluster assignment matches input for every clustered node.

    v1: singleton outliers carry ``__outlier_<id>__`` labels.
    v2: outliers carry the combined ``__outliers__`` label.

    Both: profile forces ``outlier_mode=excluded``, so non-outlier
    cluster membership is preserved verbatim.
    """
    out_map = dict(zip(out_com["node_id"].astype(str), out_com["cluster_id"].astype(str)))
    in_cluster_of = {str(n): c for n, c in fx["cluster_of"].items()}
    bad = []
    for nid_str, cid_str in out_map.items():
        if nid_str in in_cluster_of:
            if in_cluster_of[nid_str] != cid_str:
                bad.append((nid_str, in_cluster_of[nid_str], cid_str))
    if bad:
        sample = ", ".join(f"{n}: {ic}->{oc}" for n, ic, oc in bad[:5])
        return False, f"clustered-node mismatches: {sample}"

    out_set = set(out_map.keys())
    in_set = set(in_cluster_of.keys())
    missing_clustered = in_set - out_set
    if missing_clustered:
        return False, f"input-clustered nodes missing from com.csv: {sorted(missing_clustered)[:5]}"
    return True, ""


def check_kclique_present(edge_pairs: set, deg_csv: Path, assign_csv: Path,
                          mincut_csv: Path, fx: dict) -> tuple[bool, list[str]]:
    """For each cluster, the K_{k+1} on top-(k+1) by input degree must exist
    in ``edge.csv``. Top-(k+1) is taken from ``degree.csv`` (the profile's
    own ranking — what gen_kec_core actually sees as the seed clique).
    """
    if not (deg_csv.exists() and assign_csv.exists() and mincut_csv.exists()):
        return False, [f"missing profile artifact under .state/: {deg_csv.parent}"]

    node_id_csv = deg_csv.parent / "node_id.csv"
    node_iid2id = pd.read_csv(node_id_csv, header=None, dtype=str)[0].tolist()
    deg = pd.read_csv(deg_csv, header=None)[0].tolist()
    assign = pd.read_csv(assign_csv, header=None)[0].tolist()
    mc = pd.read_csv(mincut_csv, header=None)[0].tolist()

    cluster_to_iids = defaultdict(list)
    for iid, c_iid in enumerate(assign):
        if c_iid != -1:
            cluster_to_iids[c_iid].append(iid)

    issues = []
    for c_iid, iids in cluster_to_iids.items():
        k = int(mc[c_iid])
        if k <= 0: continue
        n = len(iids)
        if n <= 1: continue
        k = min(k, n - 1)
        ranked = sorted(iids, key=lambda i: (-int(deg[i]), i))
        top = ranked[: k + 1]
        top_orig = [node_iid2id[i] for i in top]
        for i in range(len(top_orig)):
            for j in range(i + 1, len(top_orig)):
                u, v = top_orig[i], top_orig[j]
                key = (u, v) if u <= v else (v, u)
                if key not in edge_pairs:
                    issues.append(
                        f"cluster c_iid={c_iid} k={k} missing core edge "
                        f"({u},{v}) in K_{{{k+1}}}"
                    )
    return len(issues) == 0, issues


def check_v1_no_residual_deficit(out_edges_pairs: set, deg_csv: Path,
                                 node_id_csv: Path) -> tuple[bool, str]:
    """Stage-4a (greedy attach) tops up degrees so output ≥ input per node.

    The matcher may overshoot (stage 2's K_{k+1} + attach can already
    inflate). Undershoot is the bug we're looking for.

    Note: the profile's ``degree.csv`` is computed *post-exclusion* —
    outlier-incident edges don't contribute. We compare against the
    same per-iid input degree used by the matcher.
    """
    node_iid2id = pd.read_csv(node_id_csv, header=None, dtype=str)[0].tolist()
    in_deg = pd.read_csv(deg_csv, header=None)[0].tolist()
    out_deg = defaultdict(int)
    for u, v in out_edges_pairs:
        out_deg[u] += 1
        out_deg[v] += 1
    deficit_nodes = []
    for iid, orig in enumerate(node_iid2id):
        d_in = int(in_deg[iid])
        d_out = int(out_deg[orig])
        if d_out < d_in:
            deficit_nodes.append((orig, d_in, d_out))
    if deficit_nodes:
        sample = ", ".join(f"{o}: in={i} out={oo}" for o, i, oo in deficit_nodes[:5])
        return False, (
            f"{len(deficit_nodes)} node(s) with output degree below input "
            f"(matcher undershoot). first 5: {sample}"
        )
    return True, ""


# ---------------------------------------------------------------------------
# v2-specific: pre/post rewire e_rs invariant
# ---------------------------------------------------------------------------

def check_v2_rewire_block_preserving(state_dir: Path, fx: dict,
                                     seed: int) -> tuple[bool, str]:
    """v2 stage 3a's rewire is supposed to be block-pair preserving.

    Re-run ``synthesize_residual_subnetwork`` in-process on the kept
    stage-3a inputs at ``edge_correction='none'`` and 'rewire'. Both
    seed all three RNGs at ``seed + 1`` (matches the pipeline). Compute
    e_rs by (min_block, max_block) bucket on the post-correction edge
    list. The 'rewire' run's e_rs must be ≤ the 'none' run's e_rs per
    block-pair: rewire never moves an edge across block-pairs; it only
    converts in-bucket invalid edges into in-bucket valid ones (or
    drops if unresolved).
    """
    if str(EC_SBM_PY) not in sys.path:
        sys.path.insert(0, str(EC_SBM_PY))
    if str(REPO_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "src"))
    import gen_outlier

    profile_dir = state_dir / "profile"
    gen_clustered_dir = state_dir / "gen_clustered"
    if not (profile_dir / "node_id.csv").exists():
        return False, f"v2 rewire test: profile state missing under {state_dir}"
    if not (gen_clustered_dir / "edge.csv").exists():
        return False, f"v2 rewire test: gen_clustered state missing under {state_dir}"

    df_orig = pd.DataFrame(fx["edges"], columns=["source", "target"]).astype(str)
    df_clust = pd.DataFrame(
        [(str(n), c) for n, c in fx["cluster_of"].items()],
        columns=["node_id", "cluster_id"],
    )
    try:
        df_exist = pd.read_csv(gen_clustered_dir / "edge.csv", dtype=str)
    except pd.errors.EmptyDataError:
        df_exist = pd.DataFrame(columns=["source", "target"])

    node2cluster_str = dict(zip(df_clust["node_id"], df_clust["cluster_id"]))

    # v2 preset: scope=all + outlier_mode=combined.
    b, probs, out_degs, _ = gen_outlier.prepare_sbm_inputs(
        df_orig, df_exist, node2cluster_str,
        outlier_mode="combined", scope="all",
    )

    def per_pair_ers(edges_iid):
        bp = defaultdict(int)
        for u, v in edges_iid:
            a, c = int(b[u]), int(b[v])
            key = (min(a, c), max(a, c))
            bp[key] += 1
        return bp

    import random
    import numpy as _np
    import graph_tool.all as gt

    random.seed(seed + 1); _np.random.seed(seed + 1); gt.seed_rng(seed + 1)
    edges_none = gen_outlier.synthesize_residual_subnetwork(
        b, probs, out_degs, "none",
    )
    bp_none = per_pair_ers(edges_none)

    random.seed(seed + 1); _np.random.seed(seed + 1); gt.seed_rng(seed + 1)
    edges_rewire = gen_outlier.synthesize_residual_subnetwork(
        b, probs, out_degs, "rewire",
    )
    bp_rewire = per_pair_ers(edges_rewire)

    bad = []
    for key, val in bp_rewire.items():
        if val > bp_none.get(key, 0):
            bad.append((key, bp_none.get(key, 0), val))
    if bad:
        sample = ", ".join(f"{k}: none={n}->rewire={r}" for k, n, r in bad[:5])
        return False, (
            f"rewire moved edges ACROSS block-pairs (rewire > none). first 5: {sample}"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Per-fixture-per-seed runner
# ---------------------------------------------------------------------------

def run_one(version: str, fx: dict, seed: int, work_root: Path,
            verbose: bool, keep_tmp: bool = False) -> dict:
    """Run pipeline.sh for one (version, fixture, seed). Returns a dict with
    pass/fail per-check + diagnostics. work_root is wiped after the run."""
    fx_dir = work_root / f"in_{fx['name']}"
    edgelist, clustering = write_fixture(fx, fx_dir)
    out_dir = work_root / f"out_{fx['name']}_{version}_seed{seed}"

    proc = run_pipeline(version, edgelist, clustering, out_dir, seed)
    if proc.returncode != 0:
        return {
            "name": f"{fx['name']}/{version}/seed{seed}",
            "pipeline_ok": False,
            "stderr_tail": proc.stderr[-400:],
            "stdout_tail": proc.stdout[-400:],
        }

    edge_csv = out_dir / "edge.csv"
    com_csv = out_dir / "com.csv"
    state_dir = out_dir / ".state"
    profile_dir = state_dir / "profile"

    if not edge_csv.exists() or not com_csv.exists():
        return {
            "name": f"{fx['name']}/{version}/seed{seed}",
            "pipeline_ok": False,
            "msg": f"missing edge.csv or com.csv under {out_dir}",
        }

    edges_df = pd.read_csv(edge_csv, dtype=str)
    out_edges = list(zip(edges_df["source"], edges_df["target"]))
    out_pairs = set((u, v) if u <= v else (v, u) for (u, v) in out_edges)
    out_com_df = pd.read_csv(com_csv, dtype=str)

    res: dict = {
        "name": f"{fx['name']}/{version}/seed{seed}",
        "pipeline_ok": True,
        "n_nodes_out": out_com_df["node_id"].nunique(),
        "n_edges_out": len(out_pairs),
    }

    parallels, loops = assert_simple_graph(out_edges)
    res["simple_ok"] = (parallels == 0 and loops == 0)
    res["simple_diag"] = f"parallels={parallels} loops={loops}"

    cluster_ok, cluster_msg = check_cluster_assignment(out_com_df, fx, version)
    res["cluster_ok"] = cluster_ok
    res["cluster_diag"] = cluster_msg

    edges_int = [(int(u), int(v)) for (u, v) in fx["edges"]]
    expected = fx["expected_mincut"]
    inv_cluster_of = defaultdict(list)
    for nid, c in fx["cluster_of"].items():
        inv_cluster_of[c].append(nid)
    in_kc = {}
    for c, members in inv_cluster_of.items():
        in_kc[c] = edge_connectivity_of_subgraph(members, edges_int)

    out_edges_int = [(int(u), int(v)) for (u, v) in out_edges]
    out_kc = {}
    cluster_to_orig = defaultdict(list)
    for _, row in out_com_df.iterrows():
        cid = row["cluster_id"]
        try: nid = int(row["node_id"])
        except ValueError: continue
        cluster_to_orig[cid].append(nid)

    kc_issues = []
    for c, members in inv_cluster_of.items():
        members_int = [int(n) for n in members]
        kc_out = edge_connectivity_of_subgraph(members_int, out_edges_int)
        out_kc[c] = kc_out
        k_target = int(in_kc[c])
        if kc_out < k_target:
            kc_issues.append(f"{c}: input-k={k_target} output-k={kc_out}")
    res["kconnect_ok"] = len(kc_issues) == 0
    res["kconnect_diag"] = (
        "; ".join(kc_issues) if kc_issues
        else f"k_in={in_kc} k_out={out_kc} (expected_doc={expected})"
    )

    deg_csv = profile_dir / "degree.csv"
    assign_csv = profile_dir / "assignment.csv"
    mincut_csv = profile_dir / "mincut.csv"
    node_id_csv = profile_dir / "node_id.csv"
    kk_ok, kk_issues = check_kclique_present(out_pairs, deg_csv, assign_csv,
                                             mincut_csv, fx)
    res["kclique_ok"] = kk_ok
    res["kclique_diag"] = ("; ".join(kk_issues[:3]) if kk_issues
                           else "all per-cluster K_{k+1} cores intact")

    if version == "v1":
        v1_ok, v1_msg = check_v1_no_residual_deficit(out_pairs, deg_csv, node_id_csv)
        res["v1_match_deficit_ok"] = v1_ok
        res["v1_match_deficit_diag"] = v1_msg
    else:
        try:
            v2_ok, v2_msg = check_v2_rewire_block_preserving(state_dir, fx, seed)
            res["v2_rewire_ok"] = v2_ok
            res["v2_rewire_diag"] = v2_msg
        except Exception as exc:
            res["v2_rewire_ok"] = False
            res["v2_rewire_diag"] = f"check raised {type(exc).__name__}: {exc}"

    if verbose:
        print(f"\n   [{res['name']}] proc returncode={proc.returncode}")
        print(f"     edges_out={res['n_edges_out']} nodes_out={res['n_nodes_out']}")
        print(f"     in_kc={in_kc} out_kc={out_kc}")

    if not keep_tmp:
        shutil.rmtree(out_dir, ignore_errors=True)
    return res


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def fmt_check(label: str, ok: bool, diag: str) -> str:
    tag = "PASS" if ok else "FAIL"
    return f"  {label:>22s}: {tag}  {diag}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seeds", type=int, nargs="*", default=[1, 2, 3, 4, 5])
    ap.add_argument("--versions", nargs="*", default=["v1", "v2"],
                    choices=["v1", "v2"])
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--keep-tmp", action="store_true",
                    help="leave the per-run output trees on disk")
    args = ap.parse_args()

    if not PIPELINE.exists():
        print(f"FAIL: pipeline.sh missing at {PIPELINE}", file=sys.stderr)
        sys.exit(2)
    if not EC_SBM_PY.exists():
        print(f"FAIL: ec-sbm package missing at {EC_SBM_PACKAGE}", file=sys.stderr)
        sys.exit(2)

    fixtures = [small20_fixture(), synthetic100_fixture()]

    overall = True
    summary: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="ec_sbm_check_") as tmp:
        work_root = Path(tmp)
        if args.keep_tmp:
            work_root = Path(tempfile.mkdtemp(prefix="ec_sbm_check_keep_"))
            print(f"keeping tmp at {work_root}")

        for fx in fixtures:
            print(f"\n=== fixture: {fx['name']} (N={len(fx['cluster_of']) + len(fx['outliers'])} "
                  f"edges={len(fx['edges'])} expected_mincut={fx['expected_mincut']}) ===")
            for version in args.versions:
                for seed in args.seeds:
                    res = run_one(version, fx, seed, work_root, args.verbose,
                                  keep_tmp=args.keep_tmp)
                    summary.append(res)
                    print(f"\n[{res['name']}]")
                    if not res.get("pipeline_ok", False):
                        print(f"  PIPELINE FAILED")
                        if "stderr_tail" in res:
                            print(f"    stderr: {res['stderr_tail']}")
                        elif "msg" in res:
                            print(f"    msg: {res['msg']}")
                        overall = False
                        continue

                    print(fmt_check("simple graph", res["simple_ok"], res["simple_diag"]))
                    print(fmt_check("cluster assignment", res["cluster_ok"], res["cluster_diag"]))
                    print(fmt_check("k-edge-connectivity", res["kconnect_ok"], res["kconnect_diag"]))
                    print(fmt_check("K_{k+1} core present", res["kclique_ok"], res["kclique_diag"]))
                    if version == "v1":
                        print(fmt_check("v1 matcher residual",
                                        res["v1_match_deficit_ok"],
                                        res["v1_match_deficit_diag"]))
                    else:
                        print(fmt_check("v2 rewire block-preserving",
                                        res["v2_rewire_ok"],
                                        res["v2_rewire_diag"]))

                    seed_ok = (
                        res["simple_ok"] and res["cluster_ok"]
                        and res["kconnect_ok"] and res["kclique_ok"]
                        and (res.get("v1_match_deficit_ok", True))
                        and (res.get("v2_rewire_ok", True))
                    )
                    overall = overall and seed_ok

    print("\n" + "=" * 60)
    n_runs = len(summary)
    n_pipeline_ok = sum(1 for r in summary if r.get("pipeline_ok"))
    n_all_ok = sum(
        1 for r in summary if r.get("pipeline_ok")
        and r.get("simple_ok") and r.get("cluster_ok")
        and r.get("kconnect_ok") and r.get("kclique_ok")
        and r.get("v1_match_deficit_ok", True)
        and r.get("v2_rewire_ok", True)
    )
    print(f"runs: {n_runs}, pipeline-ok: {n_pipeline_ok}, all-checks-ok: {n_all_ok}")
    print("OVERALL: " + ("PASS" if overall else "FAIL"))
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
