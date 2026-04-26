"""Unit tests for src/pipeline_common.py helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pipeline_common import (  # noqa: E402
    drop_singleton_clusters,
    load_probs_matrix,
    simplify_edges,
    write_edge_tuples_csv,
)


def test_load_probs_matrix_populated(tmp_path):
    ec = tmp_path / "edge_counts.csv"
    ec.write_text("0,0,12\n0,1,3\n1,1,7\n2,2,5\n")

    probs = load_probs_matrix(ec, num_clusters=3)

    dense = probs.toarray()
    assert dense[0, 0] == 12
    assert dense[0, 1] == 3
    assert dense[1, 1] == 7
    assert dense[2, 2] == 5
    # Missing entries default to 0.
    assert dense[1, 0] == 0
    assert dense[2, 0] == 0
    assert dense.shape == (3, 3)


def test_load_probs_matrix_empty_file(tmp_path):
    ec = tmp_path / "edge_counts.csv"
    ec.write_text("")

    probs = load_probs_matrix(ec, num_clusters=4)

    assert probs.shape == (4, 4)
    assert probs.nnz == 0


def test_load_probs_matrix_int_dtype(tmp_path):
    ec = tmp_path / "edge_counts.csv"
    ec.write_text("0,0,1\n")

    probs = load_probs_matrix(ec, num_clusters=2)
    assert probs.dtype.kind == "i"


# ---------------------------------------------------------------------------
# simplify_edges (shipping guard: runs on every generator's edge.csv)
# ---------------------------------------------------------------------------

def test_simplify_edges_drops_self_loops():
    df = pd.DataFrame([("a", "a"), ("a", "b"), ("c", "c")],
                      columns=["source", "target"])
    out = simplify_edges(df)
    assert len(out) == 1
    assert (out["source"].iloc[0], out["target"].iloc[0]) == ("a", "b")


def test_simplify_edges_drops_parallel_edges():
    df = pd.DataFrame([("a", "b"), ("b", "a"), ("a", "b")],
                      columns=["source", "target"])
    out = simplify_edges(df)
    assert len(out) == 1


def test_simplify_edges_normalizes_endpoints_min_max():
    """Every row has source ≤ target after simplify (canonical form)."""
    df = pd.DataFrame([("b", "a"), ("d", "c")], columns=["source", "target"])
    out = simplify_edges(df)
    assert list(out["source"]) == ["a", "c"]
    assert list(out["target"]) == ["b", "d"]


def test_simplify_edges_preserves_distinct_pairs_after_normalization():
    """(b,a) and (a,b) are the same edge after normalization — dedup to 1."""
    df = pd.DataFrame([("b", "a"), ("a", "b"), ("c", "d"), ("d", "c")],
                      columns=["source", "target"])
    out = simplify_edges(df)
    assert len(out) == 2
    pairs = {(r.source, r.target) for r in out.itertuples()}
    assert pairs == {("a", "b"), ("c", "d")}


def test_simplify_edges_on_empty_input():
    df = pd.DataFrame(columns=["source", "target"])
    out = simplify_edges(df)
    assert out.empty
    assert list(out.columns) == ["source", "target"]


def test_simplify_edges_returns_new_df_doesnt_mutate_input():
    df = pd.DataFrame([("a", "b"), ("a", "b")], columns=["source", "target"])
    _ = simplify_edges(df)
    assert len(df) == 2  # original unchanged


def test_simplify_edges_numeric_comparison_order():
    """Numeric-string IDs normalize by string compare — the same pipeline
    shipping guard that every gen uses."""
    df = pd.DataFrame([("10", "2"), ("2", "10")], columns=["source", "target"])
    out = simplify_edges(df)
    # min/max of strings "10" and "2" gives ("10", "2") because "1" < "2".
    # Test asserts current behavior (stability is the value).
    assert len(out) == 1


# ---------------------------------------------------------------------------
# drop_singleton_clusters (shipping guard)
# ---------------------------------------------------------------------------

def test_drop_singleton_clusters_removes_size_one():
    df = pd.DataFrame({
        "node_id":    ["a", "b", "c", "d"],
        "cluster_id": ["C0", "C0", "C1", "C2"],
    })
    out = drop_singleton_clusters(df)
    # C1 (1 member) and C2 (1 member) dropped; C0 kept.
    assert set(out["cluster_id"]) == {"C0"}
    assert set(out["node_id"]) == {"a", "b"}


def test_drop_singleton_clusters_keeps_every_non_singleton():
    df = pd.DataFrame({
        "node_id":    ["a", "b", "c", "d", "e"],
        "cluster_id": ["C0", "C0", "C1", "C1", "C1"],
    })
    out = drop_singleton_clusters(df)
    assert len(out) == 5


def test_drop_singleton_clusters_on_empty():
    df = pd.DataFrame(columns=["node_id", "cluster_id"])
    out = drop_singleton_clusters(df)
    assert out.empty


def test_drop_singleton_clusters_on_all_singletons_returns_empty():
    df = pd.DataFrame({
        "node_id":    ["a", "b", "c"],
        "cluster_id": ["C0", "C1", "C2"],
    })
    out = drop_singleton_clusters(df)
    assert out.empty


# ---------------------------------------------------------------------------
# write_edge_tuples_csv
# ---------------------------------------------------------------------------

def test_write_edge_tuples_csv_without_remap(tmp_path):
    out = tmp_path / "edges.csv"
    write_edge_tuples_csv(out, [(0, 1), (2, 3)])
    df = pd.read_csv(out)
    assert list(df.columns) == ["source", "target"]
    assert list(df["source"]) == [0, 2]
    assert list(df["target"]) == [1, 3]


def test_write_edge_tuples_csv_with_iid_remap(tmp_path):
    """When ``node_iid2id`` is provided, each iid is rewritten to its
    string id. The ec-sbm + sbm gens use this on graph-tool output."""
    out = tmp_path / "edges.csv"
    write_edge_tuples_csv(out, [(0, 1), (1, 2)],
                          node_iid2id={0: "alpha", 1: "beta", 2: "gamma"})
    df = pd.read_csv(out, dtype=str)
    assert list(df["source"]) == ["alpha", "beta"]
    assert list(df["target"]) == ["beta", "gamma"]
