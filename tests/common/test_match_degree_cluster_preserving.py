"""Tests for the cluster-preserving variants of ``src/match_degree.py``.

Covers C2 surface (direct mode only): block assignment, per-bp budget
construction, the five ``cluster_preserving_*`` matcher functions.
Remap-mode coverage lives in C3's tests once that path lands.
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from match_degree import (  # noqa: E402
    build_block_assignment,
    build_bp_budget_direct,
    build_bp_budget_remap,
    load_reference_topologies,
    load_remap_topologies,
    match_missing_degrees_cluster_preserving_greedy,
    match_missing_degrees_cluster_preserving_random_greedy,
    match_missing_degrees_cluster_preserving_rewire,
    match_missing_degrees_cluster_preserving_true_greedy,
    subtract_existing_edges,
)


def _write_csv(path: Path, rows: list[tuple[str, str]], cols: tuple[str, str]) -> None:
    pd.DataFrame(rows, columns=list(cols)).to_csv(path, index=False)


@pytest.fixture
def two_cluster_fixture(tmp_path):
    """6 nodes split 3-3 across two clusters, no outliers.

    - Ref edgelist: dense within both clusters and one inter edge.
      Per-bp counts: (0,0)=3, (1,1)=3, (0,1)=1.
    - Input edgelist: a single intra edge in cluster 0.
      Per-bp counts: (0,0)=1.
    - Direct mode budget: (0,0)=2, (1,1)=3, (0,1)=1.
    """
    cluster_fp = tmp_path / "com.csv"
    _write_csv(
        cluster_fp,
        [("n0", "0"), ("n1", "0"), ("n2", "0"), ("n3", "1"), ("n4", "1"), ("n5", "1")],
        ("node_id", "cluster_id"),
    )

    ref_fp = tmp_path / "ref.csv"
    _write_csv(
        ref_fp,
        [
            ("n0", "n1"), ("n1", "n2"), ("n0", "n2"),
            ("n3", "n4"), ("n4", "n5"), ("n3", "n5"),
            ("n0", "n3"),
        ],
        ("source", "target"),
    )

    in_fp = tmp_path / "in.csv"
    _write_csv(in_fp, [("n0", "n1")], ("source", "target"))

    return {
        "input_fp": in_fp,
        "ref_fp": ref_fp,
        "cluster_fp": cluster_fp,
    }


def _load_state(fixture, outlier_mode="combined"):
    node_id2iid, node_iid2id, out_degs = load_reference_topologies(
        str(fixture["ref_fp"]), str(fixture["input_fp"]),
    )
    exist_neighbor, updated_out_degs = subtract_existing_edges(
        str(fixture["input_fp"]), node_id2iid, out_degs,
    )
    b, bp_budget = build_bp_budget_direct(
        str(fixture["input_fp"]), str(fixture["ref_fp"]),
        str(fixture["cluster_fp"]), outlier_mode, node_id2iid,
    )
    return node_id2iid, node_iid2id, updated_out_degs, exist_neighbor, b, bp_budget


# ---------------------------------------------------------------------------
# build_block_assignment
# ---------------------------------------------------------------------------

def test_block_assignment_no_outliers(two_cluster_fixture):
    node_id2iid, _, _ = load_reference_topologies(
        str(two_cluster_fixture["ref_fp"]), str(two_cluster_fixture["input_fp"]),
    )
    b = build_block_assignment(
        node_id2iid, str(two_cluster_fixture["cluster_fp"]), "combined",
    )
    by_node = {nd: int(b[node_id2iid[nd]]) for nd in node_id2iid}
    assert {by_node[f"n{i}"] for i in range(3)} == {0}
    assert {by_node[f"n{i}"] for i in range(3, 6)} == {1}


def test_block_assignment_outliers_combined(tmp_path):
    cluster_fp = tmp_path / "com.csv"
    _write_csv(cluster_fp, [("a", "0"), ("b", "0")], ("node_id", "cluster_id"))
    edge_fp = tmp_path / "in.csv"
    _write_csv(edge_fp, [("a", "b"), ("a", "c"), ("b", "d")], ("source", "target"))

    node_id2iid, _, _ = load_reference_topologies(str(edge_fp))
    b = build_block_assignment(node_id2iid, str(cluster_fp), "combined")
    by_node = {nd: int(b[node_id2iid[nd]]) for nd in node_id2iid}
    assert by_node["a"] == 0 and by_node["b"] == 0
    # outliers c and d share the combined block
    assert by_node["c"] == by_node["d"] == 1


def test_block_assignment_outliers_singleton(tmp_path):
    cluster_fp = tmp_path / "com.csv"
    _write_csv(cluster_fp, [("a", "0"), ("b", "0")], ("node_id", "cluster_id"))
    edge_fp = tmp_path / "in.csv"
    _write_csv(edge_fp, [("a", "b"), ("a", "c"), ("b", "d")], ("source", "target"))

    node_id2iid, _, _ = load_reference_topologies(str(edge_fp))
    b = build_block_assignment(node_id2iid, str(cluster_fp), "singleton")
    by_node = {nd: int(b[node_id2iid[nd]]) for nd in node_id2iid}
    # outliers each get their own block, ascending by node-id sort order
    assert by_node["c"] != by_node["d"]
    assert sorted([by_node["c"], by_node["d"]]) == [1, 2]


# ---------------------------------------------------------------------------
# build_bp_budget_direct
# ---------------------------------------------------------------------------

def test_bp_budget_direct_subtracts_input(two_cluster_fixture):
    node_id2iid, _, _ = load_reference_topologies(
        str(two_cluster_fixture["ref_fp"]), str(two_cluster_fixture["input_fp"]),
    )
    b, bp_budget = build_bp_budget_direct(
        str(two_cluster_fixture["input_fp"]),
        str(two_cluster_fixture["ref_fp"]),
        str(two_cluster_fixture["cluster_fp"]),
        "combined", node_id2iid,
    )
    assert bp_budget == {(0, 0): 2, (1, 1): 3, (0, 1): 1}


# ---------------------------------------------------------------------------
# Per-algo smoke + bp invariant
# ---------------------------------------------------------------------------

def _bp_counts(b, edges):
    counts = defaultdict(int)
    for u, v in edges:
        bu, bv = int(b[u]), int(b[v])
        counts[(min(bu, bv), max(bu, bv))] += 1
    return counts


def _seed_all(seed):
    random.seed(seed)
    try:
        import graph_tool.all as gt
        gt.seed_rng(seed)
    except ImportError:
        pass


CP_DETERMINISTIC = [
    ("cluster_preserving_greedy",
     match_missing_degrees_cluster_preserving_greedy),
    ("cluster_preserving_true_greedy",
     match_missing_degrees_cluster_preserving_true_greedy),
]

CP_RANDOM = [
    ("cluster_preserving_random_greedy",
     match_missing_degrees_cluster_preserving_random_greedy),
]


@pytest.mark.parametrize("name,fn", CP_DETERMINISTIC + CP_RANDOM)
def test_cp_algo_respects_bp_budget(two_cluster_fixture, name, fn):
    _seed_all(1)
    _, _, out_degs, exist_neighbor, b, bp_budget = _load_state(two_cluster_fixture)
    initial_budget = dict(bp_budget)

    edges = fn(out_degs, exist_neighbor, b, bp_budget)

    # No edge violates the per-bp budget.
    bp_emitted = _bp_counts(b, edges)
    for key, cnt in bp_emitted.items():
        assert cnt <= initial_budget.get(key, 0), (
            f"{name} placed {cnt} edges in bp {key}, budget was "
            f"{initial_budget.get(key, 0)}"
        )

    # Simple-graph invariants.
    assert all(u != v for u, v in edges)
    assert len(set(edges)) == len(edges)


@pytest.mark.parametrize("name,fn", CP_DETERMINISTIC + CP_RANDOM)
def test_cp_algo_determinism(two_cluster_fixture, name, fn):
    _seed_all(7)
    _, _, out_degs1, en1, b1, budget1 = _load_state(two_cluster_fixture)
    edges1 = fn(out_degs1, en1, b1, budget1)

    _seed_all(7)
    _, _, out_degs2, en2, b2, budget2 = _load_state(two_cluster_fixture)
    edges2 = fn(out_degs2, en2, b2, budget2)

    assert sorted(edges1) == sorted(edges2)


# ---------------------------------------------------------------------------
# Remap-mode bp budget
# ---------------------------------------------------------------------------

@pytest.fixture
def remap_fixture(tmp_path):
    """Disjoint-ID remap fixture.

    - input edgelist uses IDs i0..i5 with input clustering: i0..i2 in cluster
      I, i3..i5 in cluster J.
    - ref edgelist uses IDs r0..r5 (totally disjoint from input).
    - Both edgelists have the same per-rank degree sequence so rank-pair is
      stable: input deg desc = i0=4, i1=3, i2=2, i3=2, i4=1, i5=0;
      ref deg desc = r0=4, r1=3, r2=2, r3=2, r4=1, r5=0.
    """
    in_fp = tmp_path / "in.csv"
    _write_csv(
        in_fp,
        # i0 deg 4, i1 deg 3, i2 deg 2, i3 deg 2, i4 deg 1, i5 deg 0.
        [("i0", "i1"), ("i0", "i2"), ("i0", "i3"), ("i0", "i4"),
         ("i1", "i2"), ("i1", "i3")],
        ("source", "target"),
    )

    ref_fp = tmp_path / "ref.csv"
    _write_csv(
        ref_fp,
        # r0 deg 4, r1 deg 3, r2 deg 2, r3 deg 2, r4 deg 1, r5 deg 0.
        # All edges intra-cluster except r0-r3 (inter).
        [("r0", "r1"), ("r0", "r2"), ("r0", "r3"), ("r0", "r4"),
         ("r1", "r2"), ("r1", "r3")],
        ("source", "target"),
    )

    in_clust_fp = tmp_path / "in_com.csv"
    _write_csv(
        in_clust_fp,
        [("i0", "I"), ("i1", "I"), ("i2", "I"),
         ("i3", "J"), ("i4", "J"), ("i5", "J")],
        ("node_id", "cluster_id"),
    )

    return {
        "input_fp": in_fp,
        "ref_fp": ref_fp,
        "in_clust_fp": in_clust_fp,
    }


def test_remap_bp_budget_uses_input_blocks(remap_fixture):
    node_id2iid, _, _ = load_remap_topologies(
        str(remap_fixture["input_fp"]), str(remap_fixture["ref_fp"]),
    )
    b, bp_budget = build_bp_budget_remap(
        str(remap_fixture["input_fp"]), str(remap_fixture["ref_fp"]),
        str(remap_fixture["in_clust_fp"]), "combined", node_id2iid,
    )
    # Rank-pair: i0<-r0, i1<-r1, i2<-r2, i3<-r3, i4<-r4, i5<-r5.
    # Translated ref edges (as input bp under input clustering I=0, J=1):
    # (i0,i1) bp=(0,0); (i0,i2) bp=(0,0); (i0,i3) bp=(0,1); (i0,i4) bp=(0,1);
    # (i1,i2) bp=(0,0); (i1,i3) bp=(0,1).
    # Induced: (0,0)=3, (0,1)=3.
    # Input bp counts: (0,0)=2 (i0-i1, i0-i2, i1-i2 → wait: i0-i1 ∈ I-I = (0,0),
    # i0-i2 ∈ I-I = (0,0), i0-i3 ∈ I-J = (0,1), i0-i4 ∈ I-J = (0,1),
    # i1-i2 ∈ I-I = (0,0), i1-i3 ∈ I-J = (0,1)).
    # Input: (0,0)=3, (0,1)=3. Budget: induced - input clamped 0 = empty.
    # That makes the test boring. Adjust: input bp count = 3 each, induced
    # = 3 each → budget = {} (no edges needed). Confirm that.
    assert bp_budget == {}


def test_remap_bp_budget_when_ref_richer(tmp_path):
    """Ref has strictly more intra-cluster edges than input → positive budget."""
    in_fp = tmp_path / "in.csv"
    _write_csv(
        in_fp,
        # i0..i3 with sparse input.
        [("i0", "i1"), ("i2", "i3")],
        ("source", "target"),
    )
    ref_fp = tmp_path / "ref.csv"
    _write_csv(
        ref_fp,
        # ref has same degree sequence (rank-pair stable) but tighter clusters.
        # Degrees: r0=2, r1=1, r2=2, r3=1.
        [("r0", "r1"), ("r0", "r2"), ("r2", "r3")],
        ("source", "target"),
    )
    in_clust_fp = tmp_path / "in_com.csv"
    _write_csv(
        in_clust_fp,
        [("i0", "A"), ("i1", "A"), ("i2", "B"), ("i3", "B")],
        ("node_id", "cluster_id"),
    )

    node_id2iid, _, _ = load_remap_topologies(str(in_fp), str(ref_fp))
    b, bp_budget = build_bp_budget_remap(
        str(in_fp), str(ref_fp), str(in_clust_fp), "combined", node_id2iid,
    )
    # All input nodes have deg 1, sort by id asc: [i0, i1, i2, i3].
    # ref deg desc + id asc tie: [r0(2), r2(2), r1(1), r3(1)].
    # Rank-pair: i0<-r0, i1<-r2, i2<-r1, i3<-r3.
    # Translated ref edges: (r0,r1)->(i0,i2) bp=(0,1);
    #                       (r0,r2)->(i0,i1) bp=(0,0);
    #                       (r2,r3)->(i1,i3) bp=(0,1).
    # Induced: (0,0)=1, (0,1)=2.
    # Input bp: (0,0)=1 (i0-i1), (1,1)=1 (i2-i3).
    # Budget: (0,0)=0 (clamp), (0,1)=2, (1,1)=no induced -> not present.
    assert bp_budget == {(0, 1): 2}


def test_outlier_mode_changes_bp_budget(tmp_path):
    """combined vs singleton produce different block counts → different bp keys."""
    in_fp = tmp_path / "in.csv"
    _write_csv(
        in_fp,
        [("a", "b"), ("c", "d"), ("e", "f")],
        ("source", "target"),
    )
    ref_fp = tmp_path / "ref.csv"
    _write_csv(
        ref_fp,
        [("a", "b"), ("c", "d"), ("e", "f"), ("a", "c"), ("b", "e")],
        ("source", "target"),
    )
    cluster_fp = tmp_path / "com.csv"
    _write_csv(
        cluster_fp,
        [("a", "0"), ("b", "0")],
        ("node_id", "cluster_id"),
    )

    node_id2iid, _, _ = load_reference_topologies(str(ref_fp), str(in_fp))

    b_comb, budget_comb = build_bp_budget_direct(
        str(in_fp), str(ref_fp), str(cluster_fp), "combined", node_id2iid,
    )
    b_sing, budget_sing = build_bp_budget_direct(
        str(in_fp), str(ref_fp), str(cluster_fp), "singleton", node_id2iid,
    )
    # Combined has 2 blocks (cluster 0 + outlier-combined block 1).
    # Singleton has 5 blocks (cluster 0 + outliers c,d,e,f as 1,2,3,4).
    assert int(b_comb.max()) == 1
    assert int(b_sing.max()) == 4
    # bp budget keys differ in arity.
    assert {k for k in budget_comb} != {k for k in budget_sing}


def test_cp_rewire_drops_pre_existing_edges(two_cluster_fixture):
    """Rewire variant must not re-place edges that already exist in the input."""
    random.seed(11)
    _, _, out_degs, exist_neighbor, b, bp_budget = _load_state(two_cluster_fixture)
    baseline = {n: set(v) for n, v in exist_neighbor.items()}

    valid, _ = match_missing_degrees_cluster_preserving_rewire(
        out_degs, exist_neighbor, b, bp_budget,
    )
    for u, v in valid:
        assert v not in baseline.get(u, set())
        assert u not in baseline.get(v, set())


# ---------------------------------------------------------------------------
# Gridlock + hybrid fallback
# ---------------------------------------------------------------------------

def test_cp_greedy_gridlocks_when_budget_empty():
    """When every required bp has zero budget, greedy emits no edges."""
    out_degs = {0: 2, 1: 2, 2: 2, 3: 2}
    exist_neighbor = {n: set() for n in out_degs}
    b = np.array([0, 0, 1, 1])
    bp_budget = {}  # nothing allowed

    edges = match_missing_degrees_cluster_preserving_true_greedy(
        out_degs.copy(), {n: set() for n in out_degs}, b, dict(bp_budget),
    )
    assert edges == set()


# ---------------------------------------------------------------------------
# Golden-hash snapshot via subprocess (pins canonical Python output for the
# downstream JS port + sweep aggregator).
# ---------------------------------------------------------------------------

def _run_match_degree(env_seed, args_extra, work_dir, edge_rows, ref_rows,
                      cluster_rows):
    import subprocess
    import os

    work_dir.mkdir(parents=True, exist_ok=True)
    in_fp = work_dir / "in.csv"
    ref_fp = work_dir / "ref.csv"
    com_fp = work_dir / "com.csv"
    out_dir = work_dir / "out"
    _write_csv(in_fp, edge_rows, ("source", "target"))
    _write_csv(ref_fp, ref_rows, ("source", "target"))
    _write_csv(com_fp, cluster_rows, ("node_id", "cluster_id"))

    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    cmd = [
        "python", str(REPO_ROOT / "src" / "match_degree.py"),
        "--input-edgelist", str(in_fp),
        "--ref-edgelist", str(ref_fp),
        "--input-clustering", str(com_fp),
        "--output-folder", str(out_dir),
        "--seed", str(env_seed),
    ] + args_extra
    subprocess.run(cmd, env=env, check=True)
    return (out_dir / "degree_matching_edge.csv").read_bytes()


def test_cp_true_greedy_golden_hash(tmp_path):
    """Pin a small fixture's cluster_preserving_true_greedy output bytes.

    Catches accidental drift in iteration order / tie-break / bp lookup.
    Update the expected hash deliberately when the algorithm spec changes.
    """
    import hashlib

    edges = [("n0", "n1")]
    ref = [
        ("n0", "n1"), ("n1", "n2"), ("n0", "n2"),
        ("n3", "n4"), ("n4", "n5"), ("n3", "n5"),
        ("n0", "n3"),
    ]
    com = [(f"n{i}", "0") for i in range(3)] + [(f"n{i}", "1") for i in range(3, 6)]

    out_bytes = _run_match_degree(
        1,
        ["--degree-matcher", "cluster_preserving_true_greedy"],
        tmp_path,
        edges, ref, com,
    )
    h = hashlib.sha256(out_bytes).hexdigest()[:16]
    # Pinned 2026-05-02 on nwbench. Update with explanation if intentional.
    assert h == "4230086fe10aaade", (
        f"cluster_preserving_true_greedy golden hash drifted: got {h}"
    )
