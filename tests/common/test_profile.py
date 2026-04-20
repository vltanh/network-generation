"""Unit tests for profile primitives across the per-generator modules.

``compute_edge_count`` and ``compute_mixing_parameter`` live in
``src/profile_common.py`` (shared by every generator).  ``compute_mincut``
is specific to ec-sbm and lives in ``src/ec-sbm/common/profile.py``.  All
three accept plain dict/set/list inputs and return in-memory results, so
they are unit-testable without going through the CLI.
"""
from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from profile_common import (  # noqa: E402
    compute_edge_count,
    compute_mixing_parameter,
)


def _load_ecsbm_profile():
    """Load src/ec-sbm/common/profile.py by absolute path.

    The directory name contains a hyphen so we can't `import ec-sbm.common`;
    we load by file path instead, and push the module dir onto sys.path so
    its local `from profile_common import ...` resolves.
    """
    path = REPO_ROOT / "src" / "ec-sbm" / "common" / "profile.py"
    gen_dir = str(path.parent)
    sys.path.insert(0, gen_dir)
    try:
        spec = importlib.util.spec_from_file_location("ecsbm_profile", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(gen_dir)


ecsbm_profile = _load_ecsbm_profile()
compute_mincut = ecsbm_profile.compute_mincut


def _neighbors(edges):
    """Build an undirected adjacency dict from a list of (u, v) tuples."""
    nb = defaultdict(set)
    for u, v in edges:
        nb[u].add(v)
        nb[v].add(u)
    return nb


# ---------------------------------------------------------------------------
# compute_edge_count
# ---------------------------------------------------------------------------

def test_compute_edge_count_counts_both_directions():
    """Each undirected edge contributes to probs[i,j] and probs[j,i]."""
    nodes = {"a", "b"}
    neighbors = _neighbors([("a", "b")])
    node2com = {"a": "C0", "b": "C1"}
    cluster_id2iid = {"C0": 0, "C1": 1}

    ec = compute_edge_count(nodes, neighbors, node2com, cluster_id2iid)
    assert ec[(0, 1)] == 1
    assert ec[(1, 0)] == 1


def test_compute_edge_count_intra_cluster_edges():
    """Intra-cluster edges populate the diagonal, counted both directions."""
    nodes = {"a", "b", "c"}
    neighbors = _neighbors([("a", "b"), ("b", "c"), ("a", "c")])
    node2com = {"a": "C0", "b": "C0", "c": "C0"}
    cluster_id2iid = {"C0": 0}

    ec = compute_edge_count(nodes, neighbors, node2com, cluster_id2iid)
    # 3 undirected edges × 2 directions = 6 diagonal counts.
    assert ec[(0, 0)] == 6


def test_compute_edge_count_skips_unclustered_nodes():
    """Edges incident to an unclustered node contribute nothing."""
    nodes = {"a", "b", "c"}
    neighbors = _neighbors([("a", "b"), ("b", "c")])
    # Only 'a' and 'b' are clustered; 'c' is an outlier.
    node2com = {"a": "C0", "b": "C0"}
    cluster_id2iid = {"C0": 0}

    ec = compute_edge_count(nodes, neighbors, node2com, cluster_id2iid)
    # Only (a,b) survives → 2 counts on the diagonal.
    assert ec[(0, 0)] == 2
    # No off-diagonal entries from b-c.
    assert (0, 1) not in ec and (1, 0) not in ec


# ---------------------------------------------------------------------------
# compute_mincut
# ---------------------------------------------------------------------------

def test_compute_mincut_singleton_cluster_is_zero():
    """Clusters with ≤1 node get min-cut 0 without invoking PyGraph."""
    nodes = {"a"}
    neighbors = _neighbors([])
    node2com = {"a": "C0"}
    comm_size_sorted = [("C0", 1)]
    node_id2iid = {"a": 0}

    mcs = compute_mincut(nodes, neighbors, node2com, comm_size_sorted, node_id2iid)
    assert mcs == [[0]]


def test_compute_mincut_path_graph_returns_one():
    """A 3-node path has min-cut 1 (any single edge disconnects it)."""
    nodes = {"a", "b", "c"}
    neighbors = _neighbors([("a", "b"), ("b", "c")])
    node2com = {"a": "C0", "b": "C0", "c": "C0"}
    comm_size_sorted = [("C0", 3)]
    node_id2iid = {"a": 0, "b": 1, "c": 2}

    mcs = compute_mincut(nodes, neighbors, node2com, comm_size_sorted, node_id2iid)
    assert mcs == [[1]]


def test_compute_mincut_aligns_with_comm_size_sorted():
    """Result indexing matches comm_size_sorted order, not node2com insertion."""
    nodes = {"a", "b", "c", "d"}
    # Two triangles: C0={a,b,c}, C1={d} (singleton).
    neighbors = _neighbors([("a", "b"), ("b", "c"), ("a", "c")])
    node2com = {"a": "C0", "b": "C0", "c": "C0", "d": "C1"}
    comm_size_sorted = [("C0", 3), ("C1", 1)]
    node_id2iid = {"a": 0, "b": 1, "c": 2, "d": 3}

    mcs = compute_mincut(nodes, neighbors, node2com, comm_size_sorted, node_id2iid)
    # Triangle min-cut is 2 (must remove 2 edges to disconnect any vertex);
    # singleton is 0.
    assert mcs[0] == [2]
    assert mcs[1] == [0]


def test_compute_mincut_ignores_cross_cluster_edges():
    """Only intra-cluster edges enter the induced subgraph."""
    nodes = {"a", "b", "c"}
    # a-b intra (C0), a-c cross (→ C1).  Induced subgraph of C0 is just
    # one edge a-b, min-cut = 1.
    neighbors = _neighbors([("a", "b"), ("a", "c")])
    node2com = {"a": "C0", "b": "C0", "c": "C1"}
    comm_size_sorted = [("C0", 2), ("C1", 1)]
    node_id2iid = {"a": 0, "b": 1, "c": 2}

    mcs = compute_mincut(nodes, neighbors, node2com, comm_size_sorted, node_id2iid)
    assert mcs[0] == [1]
    assert mcs[1] == [0]


# ---------------------------------------------------------------------------
# compute_mixing_parameter
# ---------------------------------------------------------------------------

@pytest.fixture
def two_cluster_network():
    """Two triangles C0={a,b,c}, C1={d,e,f} plus one bridge edge c-d."""
    nodes = {"a", "b", "c", "d", "e", "f"}
    neighbors = _neighbors([
        ("a", "b"), ("b", "c"), ("a", "c"),  # C0 triangle
        ("d", "e"), ("e", "f"), ("d", "f"),  # C1 triangle
        ("c", "d"),                           # cross-cluster bridge
    ])
    node2com = {"a": "C0", "b": "C0", "c": "C0",
                "d": "C1", "e": "C1", "f": "C1"}
    return nodes, neighbors, node2com


def test_mixing_parameter_abcd_global_ratio(two_cluster_network):
    """abcd: global ξ = Σ_out / Σ_total.  1 out-edge (counted twice) / 14 total."""
    nodes, neighbors, node2com = two_cluster_network
    xi = compute_mixing_parameter(nodes, neighbors, node2com, "abcd")
    # c and d each have 1 out-edge: out_sum = 2; in_sum = 3+3+3+3+3+3 - ? Let's
    # enumerate: each triangle has 3 edges, each node has 2 intra-neighbors → 6
    # in-half-edges per triangle × 2 triangles = 12 in; 2 out half-edges.
    # Total half-edges = 14; out_fraction = 2/14.
    assert xi == pytest.approx(2 / 14)


def test_mixing_parameter_lfr_mean_of_per_node(two_cluster_network):
    """lfr: mean of per-node µ_i = out_i / (in_i + out_i)."""
    nodes, neighbors, node2com = two_cluster_network
    mu = compute_mixing_parameter(nodes, neighbors, node2com, "lfr")
    # Per-node µ: a,b,e,f have 2 in 0 out → 0; c,d have 2 in 1 out → 1/3.
    expected = (0 + 0 + 1 / 3 + 1 / 3 + 0 + 0) / 6
    assert mu == pytest.approx(expected)


def test_mixing_parameter_lfr_singleton_outliers_all_out():
    """lfr treats outliers as singletons — all their edges count as out."""
    nodes = {"a", "b", "o"}
    neighbors = _neighbors([("a", "b"), ("a", "o")])
    node2com = {"a": "C0", "b": "C0"}  # o is outlier

    mu = compute_mixing_parameter(nodes, neighbors, node2com, "lfr")
    # a: 1 in (b), 1 out (o) → 0.5
    # b: 1 in (a), 0 out    → 0
    # o: 0 in, 1 out (a)    → 1
    expected = (0.5 + 0 + 1) / 3
    assert mu == pytest.approx(expected)


def test_mixing_parameter_abcd_o_drops_outlier_outlier_edges():
    """abcd+o: outlier-outlier edges contribute nothing; outlier-clustered
    edges count as out on both endpoints."""
    nodes = {"a", "b", "o1", "o2"}
    neighbors = _neighbors([
        ("a", "b"),    # intra-cluster (C0)
        ("a", "o1"),   # clustered-outlier (out-out)
        ("o1", "o2"),  # outlier-outlier — dropped
    ])
    node2com = {"a": "C0", "b": "C0"}

    xi = compute_mixing_parameter(nodes, neighbors, node2com, "abcd+o")
    # out_sum: a-o1 contributes 2 (both endpoints); o1-o2 skipped.  = 2.
    # in_sum: a-b contributes 2.  = 2.
    # xi = 2/4 = 0.5.
    assert xi == pytest.approx(0.5)


def test_mixing_parameter_abcd_outlier_outlier_counts_as_out():
    """abcd: outlier-outlier edges count as out (singletons model)."""
    nodes = {"a", "b", "o1", "o2"}
    neighbors = _neighbors([
        ("a", "b"),
        ("o1", "o2"),
    ])
    node2com = {"a": "C0", "b": "C0"}

    xi = compute_mixing_parameter(nodes, neighbors, node2com, "abcd")
    # a-b: 2 in.  o1-o2: 2 out (both sides, since outliers are singletons).
    # xi = 2/4 = 0.5.
    assert xi == pytest.approx(0.5)


def test_mixing_parameter_fully_intra_cluster_is_zero():
    """A clustering with no cross-edges has µ = 0."""
    nodes = {"a", "b", "c"}
    neighbors = _neighbors([("a", "b"), ("b", "c"), ("a", "c")])
    node2com = {"a": "C0", "b": "C0", "c": "C0"}

    for gen in ("abcd", "abcd+o", "lfr"):
        assert compute_mixing_parameter(nodes, neighbors, node2com, gen) == 0.0


def test_mixing_parameter_lfr_deterministic_across_iteration_orders():
    """Result must not depend on set-iteration order (PYTHONHASHSEED stability)."""
    nodes = {"a", "b", "c", "d"}
    neighbors = _neighbors([("a", "b"), ("b", "c"), ("c", "d"), ("a", "d")])
    node2com = {"a": "C0", "b": "C0", "c": "C1", "d": "C1"}

    # Run twice with different node-set insertion orders; the function sorts
    # nodes internally for lfr, so results must match.
    mu1 = compute_mixing_parameter(nodes, neighbors, node2com, "lfr")
    mu2 = compute_mixing_parameter(
        {"d", "c", "b", "a"}, neighbors, node2com, "lfr"
    )
    assert mu1 == mu2
