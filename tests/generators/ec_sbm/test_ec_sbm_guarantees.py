"""EC-SBM v1 / v2 guarantees (see ``docs/algorithms/ec-sbm-v1.md`` + v2).

The headline guarantee: **every cluster C's induced subgraph in the
output is k(C)-edge-connected**, where k(C) is the input's per-cluster
min-cut (measured at profile time by ``pymincut``'s Nagamochi-Ibaraki).

The constructive phase in ``gen_kec_core.generate_cluster`` enforces
this:
  * Phase 1 wires the top-(k+1) nodes into a ``K_{k+1}`` clique (every
    complete graph on k+1 vertices is k-edge-connected).
  * Phase 2 attaches each remaining node with exactly k edges to the
    already-processed set — preserving the k-connectivity invariant
    since every new node is still reachable from the core through k
    edge-disjoint paths.

The unit tests here run ``generate_cluster`` on hand-crafted inputs and
verify the output subgraph is k-connected via ``networkit``'s
``minimum_cut``-equivalent probe. The slow tests read the full-pipeline
output and check the invariant end-to-end on the shipped example.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
EC_SBM_SRC = REPO_ROOT / "externals" / "ec-sbm" / "src"
EXAMPLES = REPO_ROOT / "examples" / "input"
EDGELIST = EXAMPLES / "empirical_networks" / "networks" / "dnc" / "dnc.csv"
CLUSTERING = (
    EXAMPLES / "reference_clusterings" / "clusterings"
    / "sbm-flat-best+cc" / "dnc" / "com.csv"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_kec_core():
    """Import externals/ec-sbm/src/gen_kec_core.py by path.

    The package has a hyphen so we can't use normal import syntax. Push
    its dir on sys.path so its local imports (graph_utils, pipeline_common)
    resolve, then load by file location.
    """
    gen_dir = str(EC_SBM_SRC)
    sys.path.insert(0, gen_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            "ec_sbm_gen_kec_core", str(EC_SBM_SRC / "gen_kec_core.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(gen_dir)


def _edges_to_networkit(n_nodes, edges):
    """Build a networkit Graph on n_nodes with the given undirected edges."""
    nk = pytest.importorskip("networkit")
    g = nk.graph.Graph(n=n_nodes, weighted=False, directed=False)
    for u, v in edges:
        g.addEdge(int(u), int(v))
    return g


def _min_cut(g):
    """Return the minimum edge cut of g (Stoer-Wagner via networkit)."""
    nk = pytest.importorskip("networkit")
    if g.numberOfEdges() == 0 or g.numberOfNodes() < 2:
        return 0
    # connectedComponents handles edge case of disconnected graphs.
    cc = nk.components.ConnectedComponents(g)
    cc.run()
    if cc.numberOfComponents() > 1:
        return 0  # disconnected → min cut is 0
    try:
        import networkit.flow
        # No direct min-cut in networkit stable API — compute by brute for small.
        return _brute_min_cut(g)
    except Exception:
        return _brute_min_cut(g)


def _brute_min_cut(g):
    """Min edge-cut via pairwise max-flow. Correct but O(n * edges) per probe."""
    nk = pytest.importorskip("networkit")
    n = g.numberOfNodes()
    if n < 2:
        return 0
    # For small graphs, a simple implementation is fine.
    best = g.numberOfEdges()
    s = 0
    for t in range(1, n):
        gw = nk.graph.Graph(g)  # copy
        # assign capacity 1 to each edge; networkit doesn't have directed
        # max-flow for unweighted — fall back to Ford-Fulkerson hand-rolled.
        val = _ford_fulkerson(gw, s, t)
        if val < best:
            best = val
            if best == 0:
                break
    return best


def _ford_fulkerson(g, s, t):
    """Max-flow on undirected graph (each edge = capacity 1 in both
    directions). BFS-based Edmonds-Karp; O(VE^2) — fine for our tiny
    test inputs (n ≤ 12)."""
    from collections import defaultdict, deque

    cap = defaultdict(int)
    for e in g.iterEdges():
        u, v = e[0], e[1]
        cap[(u, v)] += 1
        cap[(v, u)] += 1

    flow = 0
    n = g.numberOfNodes()
    while True:
        parent = {s: None}
        q = deque([s])
        while q and t not in parent:
            u = q.popleft()
            for v in range(n):
                if v in parent:
                    continue
                if cap[(u, v)] > 0:
                    parent[v] = u
                    q.append(v)
        if t not in parent:
            break
        # Augment by 1 (unit capacities).
        v = t
        while parent[v] is not None:
            u = parent[v]
            cap[(u, v)] -= 1
            cap[(v, u)] += 1
            v = u
        flow += 1
    return flow


# ---------------------------------------------------------------------------
# Unit: generate_cluster produces k-edge-connected subgraph
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_nodes,k", [
    (3, 2),   # triangle; min cut = 2 on a 3-clique
    (5, 2),   # 2-connected
    (6, 3),   # 3-connected
    (8, 2),   # larger, low connectivity
])
def test_generate_cluster_is_k_edge_connected(n_nodes, k):
    kec = _load_kec_core()

    np.random.seed(1)
    cluster_nodes = list(range(n_nodes))
    deg = np.array([k + 2] * n_nodes, dtype=int)  # generous budget
    from scipy.sparse import dok_matrix
    probs = dok_matrix((1, 1), dtype=int)
    probs[0, 0] = 2 * (n_nodes * (n_nodes - 1) // 2)  # cover full clique budget
    node2cluster = {n: 0 for n in cluster_nodes}

    edges = kec.generate_cluster(cluster_nodes, k, deg, probs, node2cluster)
    assert edges, "generate_cluster produced no edges for non-trivial input"

    g = _edges_to_networkit(n_nodes, edges)
    cut = _brute_min_cut(g)
    assert cut >= k, (
        f"n={n_nodes}, k={k}: induced subgraph has min cut {cut}, "
        f"expected ≥ {k}. edges: {edges}"
    )


def test_generate_cluster_empty_for_empty_input():
    kec = _load_kec_core()
    from scipy.sparse import dok_matrix
    edges = kec.generate_cluster(
        [], 3, np.array([], dtype=int), dok_matrix((1, 1), dtype=int), {}
    )
    assert edges == set()


def test_generate_cluster_no_edges_when_k_is_zero():
    kec = _load_kec_core()
    from scipy.sparse import dok_matrix
    deg = np.array([3, 3, 3], dtype=int)
    probs = dok_matrix((1, 1), dtype=int)
    probs[0, 0] = 6
    edges = kec.generate_cluster([0, 1, 2], 0, deg, probs, {0: 0, 1: 0, 2: 0})
    assert edges == set()


def test_generate_cluster_clips_k_to_n_minus_1():
    """k=10 on a 4-node cluster must not blow up; internally clipped to n-1=3,
    producing a K4 (3-edge-connected)."""
    kec = _load_kec_core()
    from scipy.sparse import dok_matrix
    deg = np.array([5] * 4, dtype=int)
    probs = dok_matrix((1, 1), dtype=int)
    probs[0, 0] = 12
    edges = kec.generate_cluster([0, 1, 2, 3], 10, deg, probs, {i: 0 for i in range(4)})
    # K4 has 6 edges, 3-edge-connected.
    g = _edges_to_networkit(4, edges)
    assert _brute_min_cut(g) >= 3, f"K4 expected 3-edge-connected, edges={edges}"


# ---------------------------------------------------------------------------
# Slow: end-to-end k-connectivity per cluster on shipped example
# ---------------------------------------------------------------------------

pytestmark_slow = pytest.mark.slow


@pytest.fixture
def ec_sbm_run(fresh_run, gen_spec):
    if not gen_spec.name.startswith("ec-sbm"):
        pytest.skip("ec-sbm-specific test")
    return fresh_run


@pytest.mark.slow
def test_ec_sbm_preserves_block_structure_from_input(ec_sbm_run):
    """com.csv is a stage-1 passthrough — every output (node, cluster)
    must match the input clustering (minus size-1 clusters + outliers).
    """
    out, _ = ec_sbm_run
    out_com = pd.read_csv(out / "com.csv", dtype=str)
    in_com = pd.read_csv(CLUSTERING, dtype=str)
    in_map = dict(zip(in_com["node_id"], in_com["cluster_id"]))
    for _, row in out_com.iterrows():
        assert row["node_id"] in in_map, (
            f"ec-sbm: fabricated node {row['node_id']}"
        )
        assert in_map[row["node_id"]] == row["cluster_id"]


@pytest.mark.slow
def test_ec_sbm_each_cluster_is_at_least_k_edge_connected(ec_sbm_run, gen_spec):
    """For each non-trivial cluster in com.csv, the induced subgraph in
    edge.csv has min-cut ≥ k, where k is measured on the INPUT's induced
    subgraph (the profile-stage min-cut).

    Scopes to the ≤ 6-node clusters to keep the brute-force max-flow
    tractable. Larger clusters are covered indirectly by the construction
    guarantee; this test is the regression tripwire for small cases.
    """
    nk = pytest.importorskip("networkit")

    out, _ = ec_sbm_run
    com = pd.read_csv(out / "com.csv", dtype=str)
    edges = pd.read_csv(out / "edge.csv", dtype=str)
    in_edges = pd.read_csv(EDGELIST, dtype=str)

    node2cluster = dict(zip(com["node_id"], com["cluster_id"]))
    checked = 0
    for cid in com["cluster_id"].unique():
        members = [n for n, c in node2cluster.items() if c == cid]
        if len(members) > 6 or len(members) < 2:
            continue
        m_set = set(members)

        # Induced subgraph in output.
        out_sub = [(u, v) for u, v in zip(edges["source"], edges["target"])
                   if u in m_set and v in m_set]
        # Input induced subgraph (for reference k).
        in_sub = [(u, v) for u, v in zip(in_edges["source"], in_edges["target"])
                  if u in m_set and v in m_set]
        if not in_sub:
            continue

        idx = {n: i for i, n in enumerate(members)}
        g_in = nk.graph.Graph(n=len(members), weighted=False, directed=False)
        for u, v in in_sub:
            g_in.addEdge(idx[u], idx[v])
        g_out = nk.graph.Graph(n=len(members), weighted=False, directed=False)
        for u, v in out_sub:
            g_out.addEdge(idx[u], idx[v])

        k_in = _brute_min_cut(g_in)
        k_out = _brute_min_cut(g_out)
        assert k_out >= k_in, (
            f"{gen_spec.name}: cluster {cid} ({len(members)} members): "
            f"input k={k_in}, output k={k_out}; invariant violated"
        )
        checked += 1

    # Sanity: we must have exercised at least one cluster.
    assert checked > 0, "test_ec_sbm_k_connectivity exercised zero clusters"
