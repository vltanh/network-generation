"""Unit tests for profile primitives across the per-generator modules.

``compute_edge_count``, ``compute_mixing_parameter``, ``identify_outliers``,
``apply_outlier_mode``, and ``read_outlier_mode`` / ``export_outlier_mode``
live in ``src/profile_common.py`` (shared by every generator).
``compute_mincut`` is specific to ec-sbm and lives in
``src/ec-sbm/common/profile.py``.  All accept plain dict/set/list inputs
and return in-memory results, so they are unit-testable without going
through the CLI.
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
    COMBINED_OUTLIER_CLUSTER_ID,
    apply_outlier_mode,
    compute_edge_count,
    compute_mixing_parameter,
    export_outlier_mode,
    identify_outliers,
    read_outlier_mode,
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
    """Build an undirected adjacency defaultdict from a list of (u, v) tuples."""
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
# identify_outliers + apply_outlier_mode
# ---------------------------------------------------------------------------

def _hand_built_graph():
    """Shared test graph spec used across the mode-mechanics tests.

    Nodes:
      - Cluster A: a1, a2, a3 (size 3)
      - Cluster B: b1, b2 (size 2)
      - Cluster S: s1 (size-1 input cluster — gets promoted to outlier)
      - Unclustered: u1
    Edges:
      - Intra-A: a1-a2, a2-a3
      - Intra-B: b1-b2
      - Cross A-B: a1-b1 (CC out)
      - Clustered↔outlier: a2-u1, b2-s1 (OC)
      - Outlier-outlier: s1-u1 (OO)
    """
    nodes = {"a1", "a2", "a3", "b1", "b2", "s1", "u1"}
    node2com = {"a1": "A", "a2": "A", "a3": "A",
                "b1": "B", "b2": "B",
                "s1": "S"}
    cluster_counts = {"A": 3, "B": 2, "S": 1}
    neighbors = _neighbors([
        ("a1", "a2"), ("a2", "a3"),
        ("b1", "b2"),
        ("a1", "b1"),
        ("a2", "u1"), ("b2", "s1"),
        ("s1", "u1"),
    ])
    return nodes, node2com, cluster_counts, neighbors


def test_identify_outliers_returns_unclustered_plus_singleton_clusters():
    nodes, node2com, cluster_counts, _ = _hand_built_graph()
    outliers = identify_outliers(nodes, node2com, cluster_counts)
    assert outliers == {"s1", "u1"}
    # Size-1 cluster removed in place; multi-member clusters preserved.
    assert "S" not in cluster_counts
    assert cluster_counts == {"A": 3, "B": 2}
    # Promoted-singleton node lost its membership; multi-member ones kept theirs.
    assert "s1" not in node2com
    assert node2com["a1"] == "A" and node2com["b1"] == "B"


def test_apply_outlier_mode_excluded_drops_outliers_and_their_edges():
    nodes, node2com, cluster_counts, neighbors = _hand_built_graph()
    outliers = identify_outliers(nodes, node2com, cluster_counts)

    apply_outlier_mode(
        nodes, node2com, cluster_counts, neighbors, outliers, mode="excluded",
    )

    assert outliers == {"s1", "u1"}
    assert nodes == {"a1", "a2", "a3", "b1", "b2"}
    assert cluster_counts == {"A": 3, "B": 2}
    # No outlier ids left anywhere in neighbors.
    for u, nb in neighbors.items():
        assert u not in outliers
        assert not (nb & outliers)
    # Edge count: intra-A 2, intra-B 1, cross A-B 1 = 4 undirected edges; 8 halves.
    halves = sum(len(nb) for nb in neighbors.values())
    assert halves == 2 * (2 + 1 + 1)


def test_apply_outlier_mode_singleton_fresh_cluster_per_outlier():
    nodes, node2com, cluster_counts, neighbors = _hand_built_graph()
    outliers = identify_outliers(nodes, node2com, cluster_counts)

    apply_outlier_mode(
        nodes, node2com, cluster_counts, neighbors, outliers, mode="singleton",
    )

    assert nodes == {"a1", "a2", "a3", "b1", "b2", "s1", "u1"}
    assert node2com["s1"] == "__outlier_s1__"
    assert node2com["u1"] == "__outlier_u1__"
    assert cluster_counts["__outlier_s1__"] == 1
    assert cluster_counts["__outlier_u1__"] == 1
    # Edges unchanged.
    assert {"s1", "u1"} <= set(neighbors)


def test_apply_outlier_mode_combined_folds_into_one_cluster():
    nodes, node2com, cluster_counts, neighbors = _hand_built_graph()
    outliers = identify_outliers(nodes, node2com, cluster_counts)

    apply_outlier_mode(
        nodes, node2com, cluster_counts, neighbors, outliers, mode="combined",
    )

    assert node2com["s1"] == COMBINED_OUTLIER_CLUSTER_ID
    assert node2com["u1"] == COMBINED_OUTLIER_CLUSTER_ID
    assert cluster_counts[COMBINED_OUTLIER_CLUSTER_ID] == 2
    # OO edge s1-u1 now lives inside the mega-cluster.
    assert "u1" in neighbors["s1"]


def test_apply_outlier_mode_drop_oo_removes_oo_edges():
    nodes, node2com, cluster_counts, neighbors = _hand_built_graph()
    outliers = identify_outliers(nodes, node2com, cluster_counts)

    apply_outlier_mode(
        nodes, node2com, cluster_counts, neighbors, outliers,
        mode="singleton", drop_outlier_outlier_edges=True,
    )

    # OO edge s1-u1 dropped from both endpoints.
    assert "u1" not in neighbors["s1"]
    assert "s1" not in neighbors["u1"]
    # OC edges still intact.
    assert "a2" in neighbors["u1"]
    assert "b2" in neighbors["s1"]


def test_apply_outlier_mode_excluded_drop_oo_is_noop_equivalent():
    """Under `excluded`, drop_oo can't matter — outliers gone anyway."""
    na, n2a, cca, nba = _hand_built_graph()
    oa = identify_outliers(na, n2a, cca)
    apply_outlier_mode(na, n2a, cca, nba, oa, mode="excluded",
                       drop_outlier_outlier_edges=False)

    nb, n2b, ccb, nbb = _hand_built_graph()
    ob = identify_outliers(nb, n2b, ccb)
    apply_outlier_mode(nb, n2b, ccb, nbb, ob, mode="excluded",
                       drop_outlier_outlier_edges=True)

    assert na == nb
    assert n2a == n2b
    assert cca == ccb
    assert {k: set(v) for k, v in nba.items()} == {k: set(v) for k, v in nbb.items()}


def test_apply_outlier_mode_rejects_unknown_mode():
    nodes, node2com, cluster_counts, neighbors = _hand_built_graph()
    outliers = identify_outliers(nodes, node2com, cluster_counts)
    with pytest.raises(ValueError, match="unknown outlier mode"):
        apply_outlier_mode(
            nodes, node2com, cluster_counts, neighbors, outliers, mode="bogus",
        )


# ---------------------------------------------------------------------------
# outlier_mode.txt round-trip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["excluded", "singleton", "combined"])
@pytest.mark.parametrize("drop_oo", [False, True])
def test_outlier_mode_roundtrip(tmp_path, mode, drop_oo):
    export_outlier_mode(str(tmp_path), mode, drop_oo)
    got_mode, got_drop = read_outlier_mode(str(tmp_path / "outlier_mode.txt"))
    assert got_mode == mode
    assert got_drop is drop_oo


def test_read_outlier_mode_rejects_malformed(tmp_path):
    p = tmp_path / "outlier_mode.txt"
    p.write_text("onlyoneline\n")
    with pytest.raises(ValueError, match="2 non-empty lines"):
        read_outlier_mode(str(p))

    p.write_text("bogus\nfalse\n")
    with pytest.raises(ValueError, match="unknown mode"):
        read_outlier_mode(str(p))

    p.write_text("excluded\nmaybe\n")
    with pytest.raises(ValueError, match="true/false"):
        read_outlier_mode(str(p))


# ---------------------------------------------------------------------------
# compute_mixing_parameter (reduction= "mean" | "global")
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


def test_mixing_parameter_global_ratio(two_cluster_network):
    """global: ξ = Σ_out / Σ_total.  2 out-halves / 14 total."""
    nodes, neighbors, node2com = two_cluster_network
    xi = compute_mixing_parameter(nodes, neighbors, node2com, reduction="global")
    assert xi == pytest.approx(2 / 14)


def test_mixing_parameter_mean_of_per_node(two_cluster_network):
    """mean: average of per-node µ_i = out_i / (in_i + out_i)."""
    nodes, neighbors, node2com = two_cluster_network
    mu = compute_mixing_parameter(nodes, neighbors, node2com, reduction="mean")
    # a,b,e,f have 2 in 0 out → 0; c,d have 2 in 1 out → 1/3.
    expected = (0 + 0 + 1 / 3 + 1 / 3 + 0 + 0) / 6
    assert mu == pytest.approx(expected)


def test_mixing_parameter_skips_isolated_nodes():
    """Mean-mu skips 0-degree nodes so isolated outliers don't divide by zero."""
    nodes = {"a", "b", "iso"}
    neighbors = _neighbors([("a", "b")])
    # `iso` has no edges and no cluster — should be skipped silently.
    node2com = {"a": "C0", "b": "C0"}

    mu = compute_mixing_parameter(nodes, neighbors, node2com, reduction="mean")
    # a and b each contribute 0 (both fully intra); iso contributes nothing.
    assert mu == 0.0


def test_mixing_parameter_fully_intra_cluster_is_zero():
    """A clustering with no cross-edges has µ = 0."""
    nodes = {"a", "b", "c"}
    neighbors = _neighbors([("a", "b"), ("b", "c"), ("a", "c")])
    node2com = {"a": "C0", "b": "C0", "c": "C0"}

    for red in ("mean", "global"):
        assert compute_mixing_parameter(
            nodes, neighbors, node2com, reduction=red,
        ) == 0.0


def test_mixing_parameter_mean_deterministic_across_iteration_orders():
    """Result must not depend on set-iteration order (PYTHONHASHSEED stability)."""
    nodes = {"a", "b", "c", "d"}
    neighbors = _neighbors([("a", "b"), ("b", "c"), ("c", "d"), ("a", "d")])
    node2com = {"a": "C0", "b": "C0", "c": "C1", "d": "C1"}

    mu1 = compute_mixing_parameter(nodes, neighbors, node2com, reduction="mean")
    mu2 = compute_mixing_parameter(
        {"d", "c", "b", "a"}, neighbors, node2com, reduction="mean",
    )
    assert mu1 == mu2


def test_mixing_parameter_rejects_unknown_reduction(two_cluster_network):
    nodes, neighbors, node2com = two_cluster_network
    with pytest.raises(ValueError, match="unknown reduction"):
        compute_mixing_parameter(nodes, neighbors, node2com, reduction="bogus")


# ---------------------------------------------------------------------------
# End-to-end Step A + Step B behavior matching the per-generator contract
# ---------------------------------------------------------------------------

def test_abcd_plus_o_default_drops_oo_and_gives_global_mu():
    """ABCD+o default is (singleton, drop_oo=True). Rebuild the hand graph,
    apply the default, then compute global mu — OO contribution must be 0."""
    nodes, node2com, cluster_counts, neighbors = _hand_built_graph()
    outliers = identify_outliers(nodes, node2com, cluster_counts)
    apply_outlier_mode(
        nodes, node2com, cluster_counts, neighbors, outliers,
        mode="singleton", drop_outlier_outlier_edges=True,
    )

    # After drop_oo: intra edges A:2, B:1; CC A-B:1; OC a2-u1, b2-s1 (each now
    # cross-cluster since outliers are singletons). CC count = 3 crossings,
    # IN = 3 intra. Half-edges: 2*(3+3) = 12. out half-edges = 2*3 = 6.
    xi = compute_mixing_parameter(nodes, neighbors, node2com, reduction="global")
    assert xi == pytest.approx(6 / 12)
