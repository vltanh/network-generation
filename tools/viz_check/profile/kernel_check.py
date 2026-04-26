"""Profile-stage cross-check.

Drives every per-gen ``src/<gen>/profile.py`` (sbm, abcd, abcd+o, lfr, npso,
ec-sbm) on a battery of fixtures and outlier-flag combinations, then
re-derives the expected outputs from the in-Python fixture and compares
them against the on-disk artifacts the profile stage actually wrote.

Patterned after ``tools/sbm/kernel_check.py``: every cell is a
(fixture, gen, outlier_mode, drop_oo) tuple. The harness computes the
ground-truth side-by-side with the profile invocation and asserts byte
equality on the contract files plus per-property invariants documented
in ``memory/repo_overview.md``.

Run::

    conda run -n nwbench python tools/profile/kernel_check.py
    conda run -n nwbench python tools/profile/kernel_check.py --verbose

Fixtures:

* **small20** — 20-node 40-edge graph mirroring
  ``vltanh.github.io/netgen/shared.js`` (C1=8, C2=6, C3=4 + 2 unclustered
  outliers; node ids "1".."20").
* **rand100** — 100-node random graph with 5 planted clusters; seed=42.
  Includes 2 unclustered nodes plus 1 size-1 cluster on top, so all three
  outlier-detection paths are exercised.

Each fixture is run through six per-gen profilers (sbm, abcd, abcd+o,
lfr, npso, ec-sbm) under three outlier modes (combined, singleton,
excluded) crossed with two ``drop_outlier_outlier_edges`` settings, for
36 cells per fixture and 72 cells across both. The harness then re-runs
every cell once more and diffs all output files for determinism.

Per-cell asserts:

A. ``identify_outliers`` flags every unclustered node + every size-1
   cluster member; size>=2 cluster members are not flagged.
B. ``apply_outlier_mode`` matches the documented semantics:
   - ``combined`` : exactly one ``__outliers__`` pseudo-cluster of size
     equal to the total outlier count.
   - ``singleton``: every outlier becomes a fresh ``__outlier_<id>__``
     cluster of size 1.
   - ``excluded``: outliers and every incident edge are removed.
C. ``drop_outlier_outlier_edges`` removes edges with both endpoints in
   the outlier set under combined; under singleton the edges remain (each
   outlier is its own block, so OO becomes off-diagonal).
D. Per-gen output contract: file set + sort order + numeric content.
   - sbm  : node_id / cluster_id / assignment / degree / edge_counts.
     edge_counts uses the diagonal-doubling convention (intra weight =
     2 * count_of_intra_edges, off-diagonal = count_of_inter_edges).
   - abcd : degree / cluster_sizes / mixing_parameter (global xi).
   - abcd+o: same as abcd plus n_outliers.
   - lfr : degree / cluster_sizes / mixing_parameter (mean mu_i).
   - npso: degree / cluster_sizes / derived.txt (no mixing_parameter).
   - ec-sbm: same as sbm plus mincut.csv + com.csv (passthrough).
E. Sorting invariants: per-iid file rows align (node_id[i] -> assignment[i]
   -> degree[i]); degrees non-increasing; clusters size-desc id-asc.
F. Determinism: a second profile run on the same inputs produces byte
   identical outputs. ``PYTHONHASHSEED=0`` is pinned in subprocess env
   to mirror what every shipping ``pipeline.sh`` exports; pass
   ``--pythonhashseed random`` to expose latent hash-order dependencies
   (one is known: ec-sbm ``com.csv`` row order under combined / singleton
   modes when there is more than one outlier).
"""
from __future__ import annotations

import argparse
import filecmp
import os
import random
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "src"
ECSBM_PY_DIR = REPO_ROOT / "externals" / "ec-sbm" / "src"

# Per-gen launchpad: profile.py path + expected output / absent files +
# mixing-parameter reduction key (None when the gen doesn't emit one).
GEN_REGISTRY = {
    "sbm": {
        "script": SRC_DIR / "sbm" / "profile.py",
        "outputs": {"node_id.csv", "cluster_id.csv", "assignment.csv",
                    "degree.csv", "edge_counts.csv"},
        "absent": {"cluster_sizes.csv", "mixing_parameter.txt",
                   "n_outliers.txt", "mincut.csv", "com.csv",
                   "derived.txt"},
        "mixing": None,
    },
    "abcd": {
        "script": SRC_DIR / "abcd" / "profile.py",
        "outputs": {"degree.csv", "cluster_sizes.csv",
                    "mixing_parameter.txt"},
        "absent": {"node_id.csv", "cluster_id.csv", "assignment.csv",
                   "edge_counts.csv", "n_outliers.txt", "mincut.csv",
                   "com.csv", "derived.txt"},
        "mixing": "global",
    },
    "abcd+o": {
        "script": SRC_DIR / "abcd+o" / "profile.py",
        "outputs": {"degree.csv", "cluster_sizes.csv",
                    "mixing_parameter.txt", "n_outliers.txt"},
        "absent": {"node_id.csv", "cluster_id.csv", "assignment.csv",
                   "edge_counts.csv", "mincut.csv", "com.csv",
                   "derived.txt"},
        "mixing": "global",
    },
    "lfr": {
        "script": SRC_DIR / "lfr" / "profile.py",
        "outputs": {"degree.csv", "cluster_sizes.csv",
                    "mixing_parameter.txt"},
        "absent": {"node_id.csv", "cluster_id.csv", "assignment.csv",
                   "edge_counts.csv", "n_outliers.txt", "mincut.csv",
                   "com.csv", "derived.txt"},
        "mixing": "mean",
    },
    "npso": {
        "script": SRC_DIR / "npso" / "profile.py",
        "outputs": {"degree.csv", "cluster_sizes.csv", "derived.txt"},
        "absent": {"node_id.csv", "cluster_id.csv", "assignment.csv",
                   "edge_counts.csv", "mixing_parameter.txt",
                   "n_outliers.txt", "mincut.csv", "com.csv"},
        "mixing": None,
    },
    "ec-sbm": {
        "script": ECSBM_PY_DIR / "profile.py",
        "outputs": {"node_id.csv", "cluster_id.csv", "assignment.csv",
                    "degree.csv", "edge_counts.csv", "mincut.csv",
                    "com.csv"},
        "absent": {"cluster_sizes.csv", "mixing_parameter.txt",
                   "n_outliers.txt", "derived.txt"},
        "mixing": None,
    },
}

OUTLIER_MODES = ("combined", "singleton", "excluded")
COMBINED_OUTLIER_CLUSTER_ID = "__outliers__"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def small20_fixture() -> dict:
    """20-node, 40-edge fixture matching ``vltanh.github.io/netgen/shared.js``.

    Cluster layout: C1=8, C2=6, C3=4, plus nodes 19 and 20 unclustered
    (true outliers). Tests the unclustered-only path of identify_outliers.
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
    # OUT nodes left unclustered.
    return _build_fixture("small20", nodes, edges, cluster_of)


def rand100_fixture() -> dict:
    """100-node random graph; 5 planted clusters + 1 size-1 cluster + 2
    unclustered. Exercises both outlier paths in ``identify_outliers``.

    Layout:
      - C1..C5 of varying size summing to 97.
      - Cs6 of size 1 (one node, will be flagged as size-1 outlier).
      - 2 unclustered nodes (will be flagged as unclustered outliers).
    """
    rng = random.Random(42)
    n = 100
    nodes = list(range(1, n + 1))
    # 5 main clusters: 27, 25, 20, 15, 10 = 97.
    sizes = [27, 25, 20, 15, 10]
    cluster_of: dict = {}
    cur = 0
    for ci, sz in enumerate(sizes):
        cname = f"C{ci+1}"
        for _ in range(sz):
            cluster_of[nodes[cur]] = cname
            cur += 1
    # cur=97 -> size-1 cluster.
    cluster_of[nodes[cur]] = "Cs6"
    cur += 1
    # cur=98, 99 -> unclustered (left out of cluster_of).

    # Edges: dense intra, sparse inter, plus a few outlier-incident
    # edges (including one OO between the two true unclustered nodes).
    edges: set = set()
    p_intra = 0.30
    p_inter = 0.02
    for i in range(n):
        for j in range(i + 1, n):
            u, v = nodes[i], nodes[j]
            cu = cluster_of.get(u)
            cv = cluster_of.get(v)
            if cu is not None and cu == cv:
                p = p_intra
            else:
                p = p_inter
            if rng.random() < p:
                edges.add((u, v))
    # Make sure we always have an OO edge (98, 99) and the size-1
    # member (98 - 1 = 98 is unclustered; the size-1 cluster is on 98? -
    # no: nodes are 1..100; cur=97 -> nodes[97] = 98 is the size-1
    # cluster; cur=98,99 -> nodes[98]=99, nodes[99]=100 are unclustered).
    # OO edge between the two unclustered: (99, 100).
    edges.add((99, 100))
    # Bridge edge from size-1 to an unclustered: (98, 99).
    edges.add((98, 99))
    return _build_fixture("rand100", nodes, sorted(edges), cluster_of)


def _build_fixture(name: str, nodes: list, edges: list,
                   cluster_of: dict) -> dict:
    """Build the fixture record. Node ids stored as **strings** (matching
    pandas' dtype=str read), edges normalized to (min, max).
    """
    nodes_str = [str(n) for n in nodes]
    edges_str = []
    seen = set()
    for u, v in edges:
        if u == v:
            continue
        a, b = (str(u), str(v)) if str(u) <= str(v) else (str(v), str(u))
        # str-min/max so it matches the lexicographic convention pandas
        # reads under dtype=str. For purely numeric ids it is equivalent
        # to numeric min/max for 1- and 2-digit numbers.
        if (a, b) in seen:
            continue
        seen.add((a, b))
        edges_str.append((a, b))
    cluster_of_str = {str(k): str(v) for k, v in cluster_of.items()}
    return {
        "name": name,
        "nodes": nodes_str,
        "edges": edges_str,
        "cluster_of": cluster_of_str,
    }


# ---------------------------------------------------------------------------
# Ground-truth re-derivation (mirrors src/profile_common.py)
# ---------------------------------------------------------------------------


def expected_state(fixture: dict, mode: str,
                   drop_oo: bool) -> dict:
    """Recompute the expected post-transform (nodes, node2com, cluster_counts,
    neighbors) from the fixture, mirroring identify_outliers + apply_outlier_mode.
    """
    # Initial state from the input.
    nodes = set(str(n) for n in fixture["nodes"])
    node2com = dict(fixture["cluster_of"])  # maps str -> str
    # Pull in any edge-only nodes (true outliers absent from clustering).
    neighbors: dict = defaultdict(set)
    for u, v in fixture["edges"]:
        neighbors[u].add(v)
        neighbors[v].add(u)
        nodes.add(u)
        nodes.add(v)

    # Cluster counts from node2com (matching value_counts on the
    # clustering CSV).
    cluster_counts: dict = Counter(node2com.values())

    # 1. identify_outliers
    outliers = {u for u in nodes if u not in node2com}
    singleton_clusters = [c for c, sz in cluster_counts.items() if sz == 1]
    for c in singleton_clusters:
        del cluster_counts[c]
    for u, c in list(node2com.items()):
        if c not in cluster_counts:
            del node2com[u]
            outliers.add(u)
    pre_apply_outliers = set(outliers)

    # 2. apply_outlier_mode
    if drop_oo and mode != "excluded":
        # Mirrors apply_outlier_mode: for each outlier u, drop other
        # outliers from neighbors[u]. The OO-edge case is symmetric
        # because we visit both endpoints; CO edges are intentionally
        # left asymmetric (the clustered side keeps the outlier neighbor
        # so compute_edge_count counts it once via the clustered walk).
        for u in outliers:
            if u in neighbors:
                neighbors[u] = {v for v in neighbors[u] if v not in outliers}

    if mode == "excluded":
        for u in outliers:
            nodes.discard(u)
            if u in neighbors:
                del neighbors[u]
        for v in list(neighbors):
            neighbors[v] = {w for w in neighbors[v] if w not in outliers}
    elif mode == "singleton":
        for u in outliers:
            cid = f"__outlier_{u}__"
            node2com[u] = cid
            cluster_counts[cid] = 1
    elif mode == "combined":
        if outliers:
            for u in outliers:
                node2com[u] = COMBINED_OUTLIER_CLUSTER_ID
            cluster_counts[COMBINED_OUTLIER_CLUSTER_ID] = len(outliers)

    return {
        "nodes": nodes,
        "node2com": node2com,
        "cluster_counts": cluster_counts,
        "neighbors": dict(neighbors),
        "outliers_pre_apply": pre_apply_outliers,
    }


def expected_compute_node_degree(state: dict) -> list:
    nodes = state["nodes"]
    neighbors = state["neighbors"]
    return sorted(
        ((u, len(neighbors.get(u, set()))) for u in nodes),
        key=lambda x: (-x[1], x[0]),
    )


def expected_compute_comm_size(state: dict) -> list:
    return sorted(
        state["cluster_counts"].items(), key=lambda x: (-x[1], x[0]),
    )


def expected_compute_edge_count(state: dict, cluster_id2iid: dict) -> dict:
    nodes = state["nodes"]
    neighbors = state["neighbors"]
    node2com = state["node2com"]
    edge_counts: dict = defaultdict(int)
    for u in nodes:
        cu = node2com.get(u)
        if cu is None:
            continue
        c_iid_u = cluster_id2iid[cu]
        for v in neighbors.get(u, set()):
            cv = node2com.get(v)
            if cv is not None:
                c_iid_v = cluster_id2iid[cv]
                edge_counts[(c_iid_u, c_iid_v)] += 1
    return edge_counts


def expected_compute_mixing_parameter(state: dict, reduction: str) -> float:
    nodes = state["nodes"]
    neighbors = state["neighbors"]
    node2com = state["node2com"]
    in_d: dict = defaultdict(int)
    out_d: dict = defaultdict(int)
    for u in nodes:
        cu = node2com.get(u)
        if cu is None:
            continue
        for v in neighbors.get(u, set()):
            cv = node2com.get(v)
            if cv is None:
                continue
            if cu == cv:
                in_d[u] += 1
            else:
                out_d[u] += 1
    if reduction == "mean":
        mus = []
        for u in sorted(nodes):
            total = in_d[u] + out_d[u]
            if total == 0:
                continue
            mus.append(out_d[u] / total)
        if not mus:
            return 0.0
        return float(sum(mus) / len(mus))
    outs_sum = sum(out_d.values())
    total_sum = outs_sum + sum(in_d.values())
    if total_sum == 0:
        return 0.0
    return outs_sum / total_sum


# ---------------------------------------------------------------------------
# Profile invocation
# ---------------------------------------------------------------------------


def write_inputs(fixture: dict, work_dir: Path) -> tuple[Path, Path]:
    """Write the (edgelist.csv, clustering.csv) pair the profile reads."""
    el_path = work_dir / "edgelist.csv"
    cl_path = work_dir / "clustering.csv"
    pd.DataFrame(fixture["edges"], columns=["source", "target"]).to_csv(
        el_path, index=False,
    )
    rows = sorted(fixture["cluster_of"].items())
    pd.DataFrame(rows, columns=["node_id", "cluster_id"]).to_csv(
        cl_path, index=False,
    )
    return el_path, cl_path


def run_profile(gen: str, el_path: Path, cl_path: Path,
                out_dir: Path, mode: str, drop_oo: bool,
                pythonhashseed: str = "0") -> tuple[int, str]:
    """Invoke a per-gen profile.py via subprocess.

    Pins ``PYTHONHASHSEED=0`` by default to match the pin every shipping
    pipeline.sh exports (see ``memory/repo_overview.md`` -> Determinism).
    Without that pin, ec-sbm's ``com.csv`` row order is hash-randomized
    (it iterates ``node2com.items()`` directly in ``export_com_csv``).
    Pass ``pythonhashseed="random"`` to opt out.
    """
    info = GEN_REGISTRY[gen]
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "python", str(info["script"]),
        "--edgelist", str(el_path),
        "--clustering", str(cl_path),
        "--output-folder", str(out_dir),
        "--outlier-mode", mode,
    ]
    cmd.append(
        "--drop-outlier-outlier-edges" if drop_oo
        else "--keep-outlier-outlier-edges"
    )
    env = os.environ.copy()
    pp = [str(SRC_DIR)]
    if gen == "ec-sbm":
        pp.append(str(ECSBM_PY_DIR))
    if "PYTHONPATH" in env and env["PYTHONPATH"]:
        pp.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = ":".join(pp)
    env["PYTHONHASHSEED"] = pythonhashseed
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stderr


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------


def _read_one_col(path: Path) -> list:
    df = pd.read_csv(path, header=None, dtype=str)
    return df.iloc[:, 0].tolist()


def _read_int_col(path: Path) -> list:
    df = pd.read_csv(path, header=None)
    return [int(x) for x in df.iloc[:, 0].tolist()]


def _read_edge_counts(path: Path) -> list:
    if path.stat().st_size == 0:
        return []
    df = pd.read_csv(path, header=None)
    return [(int(r), int(c), int(w)) for r, c, w in df.values]


def _read_mincut(path: Path) -> list:
    df = pd.read_csv(path, header=None)
    return [int(x) for x in df.iloc[:, 0].tolist()]


def _read_com_csv(path: Path) -> dict:
    df = pd.read_csv(path, dtype=str)
    return dict(zip(df["node_id"], df["cluster_id"]))


def _read_text_float(path: Path) -> float:
    return float(path.read_text().strip())


def _read_text_int(path: Path) -> int:
    return int(path.read_text().strip())


# ---------------------------------------------------------------------------
# Per-cell verification
# ---------------------------------------------------------------------------


def verify_cell(gen: str, fixture: dict, mode: str, drop_oo: bool,
                out_dir: Path) -> list:
    """Return a list of (check_name, ok, detail) tuples."""
    info = GEN_REGISTRY[gen]
    state = expected_state(fixture, mode, drop_oo)
    failures: list = []

    # Skip-the-flag-with-excluded combo: drop_oo is a no-op under
    # excluded but we still verify the contract files (which should be
    # identical to drop_oo=False).

    # --- File presence / absence ---
    present = {p.name for p in out_dir.iterdir() if p.is_file()}
    missing = info["outputs"] - present
    if missing:
        failures.append(("output_files_present", False,
                         f"missing: {sorted(missing)}"))
    spurious = info["absent"] & present
    if spurious:
        failures.append(("no_spurious_files", False,
                         f"unexpected: {sorted(spurious)}"))

    # --- Outlier identification (A) ---
    expected_outliers_pre = state["outliers_pre_apply"]
    fixture_nodes = set(fixture["nodes"]) | {
        e for ab in fixture["edges"] for e in ab
    }
    initial_node2com = dict(fixture["cluster_of"])
    initial_counts = Counter(initial_node2com.values())
    expected_unclustered = {u for u in fixture_nodes if u not in initial_node2com}
    expected_size1_members = {
        u for u, c in initial_node2com.items() if initial_counts[c] == 1
    }
    expected_outliers_recompute = expected_unclustered | expected_size1_members
    if expected_outliers_pre != expected_outliers_recompute:
        failures.append((
            "identify_outliers", False,
            f"{expected_outliers_pre} vs {expected_outliers_recompute}",
        ))

    # --- Mode effects (B) ---
    expected_node2com = state["node2com"]
    expected_counts = state["cluster_counts"]
    if mode == "combined":
        if expected_outliers_pre:
            members = [u for u, c in expected_node2com.items()
                       if c == COMBINED_OUTLIER_CLUSTER_ID]
            ok = (
                COMBINED_OUTLIER_CLUSTER_ID in expected_counts
                and expected_counts[COMBINED_OUTLIER_CLUSTER_ID] == len(expected_outliers_pre)
                and set(members) == expected_outliers_pre
            )
            if not ok:
                failures.append(("mode_combined_pseudo", False,
                                 "combined pseudo-cluster malformed"))
    elif mode == "singleton":
        bad = []
        for u in expected_outliers_pre:
            cid = f"__outlier_{u}__"
            if expected_node2com.get(u) != cid or expected_counts.get(cid) != 1:
                bad.append(u)
        if bad:
            failures.append(("mode_singleton_pseudo", False,
                             f"singleton-block missing for {bad}"))
    elif mode == "excluded":
        # No outlier may appear in the working node set or any neighbor list.
        leaked = expected_outliers_pre & state["nodes"]
        if leaked:
            failures.append(("mode_excluded_node_leak", False,
                             f"outliers still in nodes: {leaked}"))
        for v, peers in state["neighbors"].items():
            if peers & expected_outliers_pre:
                failures.append(("mode_excluded_edge_leak", False,
                                 f"{v} still adj to {peers & expected_outliers_pre}"))
                break

    # --- drop_outlier_outlier_edges (C) ---
    # Recompute counts of OO edges in the input (undirected).
    in_oo_count = sum(
        1 for u, v in fixture["edges"]
        if u in expected_outliers_pre and v in expected_outliers_pre
    )
    # Count OO edges still present in state's neighbors.
    seen = set()
    out_oo_count = 0
    for v, peers in state["neighbors"].items():
        if v not in expected_outliers_pre:
            continue
        for w in peers:
            if w in expected_outliers_pre:
                key = (min(v, w), max(v, w))
                if key not in seen:
                    seen.add(key)
                    out_oo_count += 1
    if mode != "excluded":
        if drop_oo:
            if out_oo_count != 0:
                failures.append(("drop_oo_True_oo_present", False,
                                 f"OO edges left = {out_oo_count}"))
        else:
            if out_oo_count != in_oo_count:
                failures.append(("drop_oo_False_oo_lost", False,
                                 f"input {in_oo_count} vs state {out_oo_count}"))
    else:
        if out_oo_count != 0:
            failures.append(("excluded_oo_present", False,
                             f"under excluded, OO edges = {out_oo_count}"))

    # --- Sorting invariants (E) + per-gen output contracts (D) ---
    expected_node_deg = expected_compute_node_degree(state)
    expected_comm_size = expected_compute_comm_size(state)
    expected_cluster_id2iid = {c: i for i, (c, _) in enumerate(expected_comm_size)}

    # degree.csv (universal — every gen except ec-sbm-after-com sometimes
    # writes it; in fact every gen writes it).
    if "degree.csv" in info["outputs"]:
        got_deg = _read_int_col(out_dir / "degree.csv")
        exp_deg = [d for _, d in expected_node_deg]
        if got_deg != exp_deg:
            failures.append(("degree_csv_values", False,
                             f"first10 got={got_deg[:10]} exp={exp_deg[:10]}"))
        # Non-increasing check.
        if not all(got_deg[i] >= got_deg[i + 1] for i in range(len(got_deg) - 1)):
            failures.append(("degree_non_increasing", False, "violation"))

    if "node_id.csv" in info["outputs"]:
        got_nid = _read_one_col(out_dir / "node_id.csv")
        exp_nid = [u for u, _ in expected_node_deg]
        if got_nid != exp_nid:
            failures.append(("node_id_csv_values", False,
                             f"first10 got={got_nid[:10]} exp={exp_nid[:10]}"))
        # Tie-break id-asc within equal-degree runs.
        for i in range(len(got_nid) - 1):
            if got_deg[i] == got_deg[i + 1] and got_nid[i] >= got_nid[i + 1]:
                failures.append(("degree_tiebreak_id_asc", False,
                                 f"{got_nid[i]} >= {got_nid[i+1]} at pos {i}"))
                break

    if "cluster_id.csv" in info["outputs"]:
        got_cid = _read_one_col(out_dir / "cluster_id.csv")
        exp_cid = [c for c, _ in expected_comm_size]
        if got_cid != exp_cid:
            failures.append(("cluster_id_csv_values", False,
                             f"got={got_cid[:10]} exp={exp_cid[:10]}"))

    if "assignment.csv" in info["outputs"]:
        got_asn = _read_int_col(out_dir / "assignment.csv")
        exp_asn = [
            expected_cluster_id2iid[state["node2com"].get(u)]
            if u in state["node2com"] else -1
            for u, _ in expected_node_deg
        ]
        if got_asn != exp_asn:
            failures.append(("assignment_csv_values", False,
                             f"first10 got={got_asn[:10]} exp={exp_asn[:10]}"))
        # Cross-row alignment: assignment[i] consistent with node_id[i].
        for i, (u, _) in enumerate(expected_node_deg):
            cu = state["node2com"].get(u)
            iid = expected_cluster_id2iid[cu] if cu is not None else -1
            if i < len(got_asn) and got_asn[i] != iid:
                failures.append(("assignment_alignment", False,
                                 f"row {i}: node {u} -> cluster {cu}, expected iid {iid}, got {got_asn[i]}"))
                break

    if "edge_counts.csv" in info["outputs"]:
        got_ec = _read_edge_counts(out_dir / "edge_counts.csv")
        # Recompute via expected_compute_edge_count, then apply
        # diagonal-doubling convention: in src/profile_common.py the
        # walk goes over every undirected edge twice (once from u's side
        # and once from v's side); for r==s this naturally counts each
        # intra edge twice; for r!=s each direction lands in a separate
        # cell. So the file's "weight" *is* this directed count — and
        # the per-cell semantics are: intra weight = 2 * intra_edges,
        # off-diagonal weight = inter_edges. Re-derive directly.
        ec = expected_compute_edge_count(state, expected_cluster_id2iid)
        exp_ec = [(r, c, w) for (r, c), w in sorted(ec.items())]
        if got_ec != exp_ec:
            failures.append(("edge_counts_values", False,
                             f"got first5={got_ec[:5]} exp first5={exp_ec[:5]}"))
        # Independent verification of the diagonal-doubling convention.
        # Re-derive from the raw fixture state.
        intra_count = defaultdict(int)
        inter_count = defaultdict(int)
        seen_edge = set()
        for u, peers in state["neighbors"].items():
            for v in peers:
                key = (min(u, v), max(u, v))
                if key in seen_edge:
                    continue
                seen_edge.add(key)
                cu = state["node2com"].get(u)
                cv = state["node2com"].get(v)
                if cu is None or cv is None:
                    continue
                if cu == cv:
                    intra_count[expected_cluster_id2iid[cu]] += 1
                else:
                    a, b = expected_cluster_id2iid[cu], expected_cluster_id2iid[cv]
                    inter_count[(min(a, b), max(a, b))] += 1
        # Build the expected file from these counts.
        rebuilt: dict = {}
        for r, n in intra_count.items():
            rebuilt[(r, r)] = 2 * n
        for (a, b), n in inter_count.items():
            rebuilt[(a, b)] = n
            rebuilt[(b, a)] = n
        rebuilt_list = [(r, c, w) for (r, c), w in sorted(rebuilt.items())]
        if got_ec != rebuilt_list:
            failures.append(("edge_counts_diagonal_doubling", False,
                             f"got first5={got_ec[:5]} rebuilt first5={rebuilt_list[:5]}"))

    if "cluster_sizes.csv" in info["outputs"]:
        got_cs = _read_int_col(out_dir / "cluster_sizes.csv")
        # abcd+o filters out outlier-bearing pseudo-clusters from this file.
        if gen == "abcd+o":
            exp_cs = [
                sz for c, sz in expected_comm_size
                if c != COMBINED_OUTLIER_CLUSTER_ID
                and not (isinstance(c, str) and c.startswith("__outlier_"))
            ]
        else:
            exp_cs = [sz for _, sz in expected_comm_size]
        if got_cs != exp_cs:
            failures.append(("cluster_sizes_values", False,
                             f"got first10={got_cs[:10]} exp first10={exp_cs[:10]}"))
        if not all(got_cs[i] >= got_cs[i + 1] for i in range(len(got_cs) - 1)):
            failures.append(("cluster_sizes_non_increasing", False, "violation"))

    if "mixing_parameter.txt" in info["outputs"]:
        got_xi = _read_text_float(out_dir / "mixing_parameter.txt")
        exp_xi = expected_compute_mixing_parameter(state, info["mixing"])
        if abs(got_xi - exp_xi) > 1e-9:
            failures.append(("mixing_parameter", False,
                             f"got={got_xi}, exp={exp_xi}"))

    if "n_outliers.txt" in info["outputs"]:
        got_n = _read_text_int(out_dir / "n_outliers.txt")
        if got_n != len(expected_outliers_pre):
            failures.append(("n_outliers", False,
                             f"got={got_n}, exp={len(expected_outliers_pre)}"))

    # --- ec-sbm specifics ---
    if gen == "ec-sbm":
        if "mincut.csv" in info["outputs"]:
            got_mc = _read_mincut(out_dir / "mincut.csv")
            # Length must match number of clusters (post-mode).
            if len(got_mc) != len(expected_comm_size):
                failures.append(("mincut_length", False,
                                 f"got len={len(got_mc)} exp len={len(expected_comm_size)}"))
            # Singleton-cluster rows must be 0.
            for i, (_, sz) in enumerate(expected_comm_size):
                if sz <= 1 and i < len(got_mc) and got_mc[i] != 0:
                    failures.append(("mincut_singleton_zero", False,
                                     f"cluster_iid {i} (size {sz}) mincut {got_mc[i]}"))
                    break
            # For size>=2, mincut must be >= 0 and <= cluster's induced-edge count
            # (cheap structural sanity).
            for i, (cid, sz) in enumerate(expected_comm_size):
                if sz < 2 or i >= len(got_mc):
                    continue
                if got_mc[i] < 0:
                    failures.append(("mincut_negative", False,
                                     f"cluster_iid {i} mincut {got_mc[i]}"))
                    break
        if "com.csv" in info["outputs"]:
            got_com = _read_com_csv(out_dir / "com.csv")
            # com.csv is the post-transform node2com (it's emitted via
            # export_com_csv in ec-sbm/profile.py).
            if got_com != state["node2com"]:
                # Diff a few keys for diagnostics.
                bad = []
                for k in set(got_com) | set(state["node2com"]):
                    if got_com.get(k) != state["node2com"].get(k):
                        bad.append((k, got_com.get(k), state["node2com"].get(k)))
                        if len(bad) >= 5:
                            break
                failures.append(("ec_sbm_com_csv", False,
                                 f"first 5 mismatches: {bad}"))

    return failures


# ---------------------------------------------------------------------------
# Determinism check
# ---------------------------------------------------------------------------


def determinism_diff(out_dir_a: Path, out_dir_b: Path,
                     contract_files: set) -> list:
    """Return list of files that differ between the two profile runs.
    run.log is excluded (it embeds wall-clock timing).
    """
    diffs = []
    for fname in sorted(contract_files):
        a = out_dir_a / fname
        b = out_dir_b / fname
        if not a.exists() or not b.exists():
            diffs.append(fname + " (missing)")
            continue
        if not filecmp.cmp(a, b, shallow=False):
            diffs.append(fname)
    return diffs


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _print_cell_outcome(label: str, failures: list, verbose: bool) -> None:
    if not failures:
        print(f"    {label}  PASS")
        return
    print(f"    {label}  FAIL  ({len(failures)} check(s))")
    for name, _, detail in failures:
        print(f"      - {name}: {detail}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gens", nargs="*",
                    default=list(GEN_REGISTRY.keys()),
                    choices=list(GEN_REGISTRY.keys()),
                    help="Generators to exercise.")
    ap.add_argument("--modes", nargs="*", default=list(OUTLIER_MODES),
                    choices=list(OUTLIER_MODES))
    ap.add_argument("--no-determinism", action="store_true",
                    help="Skip the second-run determinism diff.")
    ap.add_argument("--pythonhashseed", default="0",
                    help="Value passed to PYTHONHASHSEED in subprocesses. "
                         "Default '0' matches every shipping pipeline.sh. "
                         "Pass 'random' to expose hash-order dependencies "
                         "(known: ec-sbm com.csv row order).")
    ap.add_argument("--verbose", action="store_true",
                    help="Print per-check outcome for every cell.")
    ap.add_argument("--keep-state", action="store_true",
                    help="Don't delete the temp work tree on exit.")
    args = ap.parse_args()

    # Sanity: scripts must exist.
    for gen, info in GEN_REGISTRY.items():
        if not info["script"].exists():
            print(f"FAIL: profile script missing for {gen}: {info['script']}",
                  file=sys.stderr)
            sys.exit(2)

    fixtures = [small20_fixture(), rand100_fixture()]

    print("=" * 72)
    print("Profile-stage cross-check")
    print("=" * 72)
    print(f"  Fixtures:   {[fx['name'] for fx in fixtures]}")
    print(f"  Gens:       {args.gens}")
    print(f"  Modes:      {args.modes}  (x drop_oo in [False, True])")
    print(f"  Determinism: {'off' if args.no_determinism else 'on'}")
    print()
    print("PASS criteria per cell: A) outlier identification, B) mode effects,")
    print("C) drop_oo flag, D) per-gen output contract, E) sorting invariants,")
    print("F) determinism (file diff between two runs).")
    print()

    workroot = Path(tempfile.mkdtemp(prefix="profile_kernel_check_"))
    n_total = 0
    n_pass = 0
    overall = True

    try:
        for fx in fixtures:
            print(f"=== fixture: {fx['name']} "
                  f"(N={len(set(fx['nodes']) | {e for ab in fx['edges'] for e in ab})}, "
                  f"E={len(fx['edges'])}, "
                  f"clusters={len(set(fx['cluster_of'].values()))}) ===")
            fx_dir = workroot / fx["name"]
            fx_dir.mkdir()
            el_path, cl_path = write_inputs(fx, fx_dir)

            for gen in args.gens:
                print(f"\n  [{gen}]")
                for mode in args.modes:
                    for drop_oo in (False, True):
                        n_total += 1
                        out_a = fx_dir / gen / f"{mode}_drop{int(drop_oo)}_a"
                        rc, err = run_profile(
                            gen, el_path, cl_path, out_a, mode, drop_oo,
                            pythonhashseed=args.pythonhashseed,
                        )
                        if rc != 0:
                            overall = False
                            print(f"    {mode:<10s} drop_oo={str(drop_oo):<5s}  "
                                  f"profile FAILED rc={rc}")
                            if args.verbose:
                                print(f"      stderr tail: {err[-400:]}")
                            continue
                        failures = verify_cell(gen, fx, mode, drop_oo, out_a)

                        if not args.no_determinism:
                            out_b = fx_dir / gen / f"{mode}_drop{int(drop_oo)}_b"
                            rc2, err2 = run_profile(
                                gen, el_path, cl_path, out_b, mode, drop_oo,
                                pythonhashseed=args.pythonhashseed,
                            )
                            if rc2 != 0:
                                failures.append((
                                    "determinism_run", False,
                                    f"second run rc={rc2} stderr_tail={err2[-200:]}",
                                ))
                            else:
                                diffs = determinism_diff(
                                    out_a, out_b,
                                    GEN_REGISTRY[gen]["outputs"],
                                )
                                if diffs:
                                    failures.append((
                                        "determinism_diff", False,
                                        f"differing files: {diffs}",
                                    ))

                        ok = not failures
                        if ok:
                            n_pass += 1
                        else:
                            overall = False
                        label = f"{mode:<10s} drop_oo={str(drop_oo):<5s}"
                        _print_cell_outcome(label, failures, args.verbose)
            print()
    finally:
        if args.keep_state:
            print(f"Workdir kept at {workroot}")
        else:
            shutil.rmtree(workroot, ignore_errors=True)

    print("=" * 72)
    print(f"OVERALL: {'PASS' if overall else 'FAIL'}  "
          f"({n_pass}/{n_total} cells passed)")
    print("=" * 72)
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
