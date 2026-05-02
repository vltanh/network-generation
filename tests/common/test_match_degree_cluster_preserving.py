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

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from match_degree import (  # noqa: E402
    build_block_assignment,
    build_bp_budget_direct,
    load_reference_topologies,
    match_missing_degrees_cluster_preserving_greedy,
    match_missing_degrees_cluster_preserving_hybrid,
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


CP_DETERMINISTIC = [
    ("cluster_preserving_greedy",
     match_missing_degrees_cluster_preserving_greedy),
    ("cluster_preserving_true_greedy",
     match_missing_degrees_cluster_preserving_true_greedy),
]

CP_RANDOM = [
    ("cluster_preserving_random_greedy",
     match_missing_degrees_cluster_preserving_random_greedy),
    ("cluster_preserving_hybrid",
     match_missing_degrees_cluster_preserving_hybrid),
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


def _seed_all(seed):
    random.seed(seed)
    try:
        import graph_tool.all as gt
        gt.seed_rng(seed)
    except ImportError:
        pass


@pytest.mark.parametrize("name,fn", CP_DETERMINISTIC + CP_RANDOM)
def test_cp_algo_determinism(two_cluster_fixture, name, fn):
    _seed_all(7)
    _, _, out_degs1, en1, b1, budget1 = _load_state(two_cluster_fixture)
    edges1 = fn(out_degs1, en1, b1, budget1)

    _seed_all(7)
    _, _, out_degs2, en2, b2, budget2 = _load_state(two_cluster_fixture)
    edges2 = fn(out_degs2, en2, b2, budget2)

    assert sorted(edges1) == sorted(edges2)


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
