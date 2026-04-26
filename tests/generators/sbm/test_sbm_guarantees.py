"""SBM guarantees (see ``docs/algorithms/sbm.md``).

SBM's contract is the strictest in the repo:

  - **Block structure**: com.csv is a passthrough of the input clustering
    with singleton clusters dropped. Every output cluster_id is also an
    input cluster_id (plus the ``__outliers__`` fold when ``combined``
    mode leaves a > 1-member outlier pool).
  - **Degree sequence**: exact pre-dedup (``micro_degs=True``), upper-bounded
    post-dedup (``simplify_edges`` removes self-loops and parallel edges).
  - **Inter-block counts ``e_{rs}``**: exact pre-dedup (``micro_ers=True``),
    upper-bounded post-dedup.
  - **N**: exact after outlier-transform.
  - **Clusters**: count equals the input's non-singleton cluster count.

The **unit tests** in this file pin the micro-SBM invariant directly by
calling ``gt.generate_sbm`` on hand-crafted inputs — independent of the
full pipeline. The **slow tests** check the post-pipeline com.csv /
edge.csv against the reference inputs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples" / "input"
EDGELIST = EXAMPLES / "empirical_networks" / "networks" / "dnc" / "dnc.csv"
CLUSTERING = (
    EXAMPLES / "reference_clusterings" / "clusterings"
    / "sbm-flat-best+cc" / "dnc" / "com.csv"
)

sys.path.insert(0, str(REPO_ROOT / "src"))


# ---------------------------------------------------------------------------
# Unit: micro-SBM hits exact degrees + exact inter-block counts pre-dedup
# ---------------------------------------------------------------------------

graph_tool = pytest.importorskip("graph_tool.all", reason="needs graph-tool")


def _stub_probs(num_clusters, counts):
    """Return a scipy csr matrix holding the per-(r,c) edge counts."""
    from scipy.sparse import dok_matrix

    m = dok_matrix((num_clusters, num_clusters), dtype=int)
    for (r, c), w in counts.items():
        m[r, c] = w
    return m.tocsr()


def test_micro_sbm_produces_exact_degrees_on_multigraph():
    """With ``micro_degs=True``, each node's total-degree (counting every
    half-edge, including self-loops once each and parallels separately)
    matches the input ``out_degs``."""
    import numpy as np

    np.random.seed(1)
    graph_tool.seed_rng(1)

    # 6 nodes, 3 clusters of 2 nodes each. Degree: A={3,3}, B={3,3}, C={2,2}.
    # Per-block half-edges: A=6, B=6, C=4. Design probs:
    #   1 intra-A edge (probs[0,0]=2), 1 intra-B edge (probs[1,1]=2),
    #   2 A-B, 2 A-C, 2 B-C edges → rows sum to 6, 6, 4.
    assignments = np.array([0, 0, 1, 1, 2, 2])
    degrees = np.array([3, 3, 3, 3, 2, 2])
    probs = _stub_probs(3, {(0, 0): 2, (1, 1): 2, (2, 2): 0,
                            (0, 1): 2, (1, 0): 2,
                            (0, 2): 2, (2, 0): 2,
                            (1, 2): 2, (2, 1): 2})
    g = graph_tool.generate_sbm(
        assignments, probs, out_degs=degrees,
        micro_ers=True, micro_degs=True, directed=False,
    )
    # graph-tool returns a Graph; count total degree including self-loops twice.
    # The multigraph's per-node out-degree must equal the input exactly.
    for v in range(6):
        got = g.vertex(v).out_degree()
        assert got == degrees[v], (
            f"micro_degs=True violated: node {v} got degree {got}, expected {degrees[v]}"
        )


def test_micro_sbm_hits_exact_per_cell_edge_count():
    """With ``micro_ers=True``, the multigraph's inter-block counts match
    the probs matrix on the diagonal (edge count = probs[k,k] / 2 intra-block
    edges) and off-diagonal (edge count = probs[r,s] = probs[s,r]).
    """
    import numpy as np

    np.random.seed(7)
    graph_tool.seed_rng(7)

    assignments = np.array([0, 0, 0, 1, 1, 1])
    degrees = np.array([2, 2, 2, 2, 2, 2])
    probs = _stub_probs(2, {(0, 0): 2, (1, 1): 2, (0, 1): 2, (1, 0): 2})

    g = graph_tool.generate_sbm(
        assignments, probs, out_degs=degrees,
        micro_ers=True, micro_degs=True, directed=False,
    )
    counts = {(0, 0): 0, (0, 1): 0, (1, 0): 0, (1, 1): 0}
    for e in g.edges():
        r = int(assignments[int(e.source())])
        s = int(assignments[int(e.target())])
        if r == s:
            counts[(r, s)] += 2  # intra: each undirected edge = 2 half-edges in r.
        else:
            counts[(r, s)] += 1
            counts[(s, r)] += 1
    # The per-cell total must match probs exactly.
    for (r, s), want in ((0, 0), 2), ((1, 1), 2), ((0, 1), 2), ((1, 0), 2):
        assert counts[(r, s)] == want, (
            f"micro_ers=True violated: probs[{r},{s}]={want}, got {counts[(r, s)]}"
        )


# ---------------------------------------------------------------------------
# Unit: sbm gen.py → post-dedup is upper-bounded by profile
# ---------------------------------------------------------------------------

def test_post_dedup_degrees_never_exceed_profile(tmp_path):
    """Run sbm's profile + gen directly (no pipeline.sh) and check every
    output degree is ≤ the profile degree for that node. micro_degs is
    exact pre-dedup; ``simplify_edges`` only removes half-edges so every
    node's degree is monotonically non-increasing through the guard."""
    import importlib.util

    env = {
        "PATH": "/home/vltanh/miniconda3/envs/nwbench/bin:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONHASHSEED": "0",
    }
    profile_dir = tmp_path / "profile"
    gen_dir = tmp_path / "gen"
    profile_dir.mkdir()
    gen_dir.mkdir()

    subprocess.run(
        ["python", str(REPO_ROOT / "src" / "sbm" / "profile.py"),
         "--edgelist", str(EDGELIST),
         "--clustering", str(CLUSTERING),
         "--output-folder", str(profile_dir)],
        env=env, check=True, capture_output=True,
    )
    subprocess.run(
        ["python", str(REPO_ROOT / "src" / "sbm" / "gen.py"),
         "--node-id", str(profile_dir / "node_id.csv"),
         "--cluster-id", str(profile_dir / "cluster_id.csv"),
         "--assignment", str(profile_dir / "assignment.csv"),
         "--degree", str(profile_dir / "degree.csv"),
         "--edge-counts", str(profile_dir / "edge_counts.csv"),
         "--input-clustering", str(CLUSTERING),
         "--output-folder", str(gen_dir),
         "--seed", "1", "--n-threads", "1"],
        env=env, check=True, capture_output=True,
    )

    node_ids = pd.read_csv(profile_dir / "node_id.csv", header=None, dtype=str)[0].tolist()
    profile_deg = pd.read_csv(profile_dir / "degree.csv", header=None)[0].to_numpy()
    edge_df = pd.read_csv(gen_dir / "edge.csv", dtype=str)
    deg_out = {}
    for u, v in zip(edge_df["source"], edge_df["target"]):
        deg_out[u] = deg_out.get(u, 0) + 1
        deg_out[v] = deg_out.get(v, 0) + 1

    for i, nid in enumerate(node_ids):
        got = deg_out.get(nid, 0)
        assert got <= profile_deg[i], (
            f"post-dedup degree {got} > profile degree {profile_deg[i]} for node {nid}"
        )


# ---------------------------------------------------------------------------
# Slow: end-to-end guarantees via fresh_run fixture
# ---------------------------------------------------------------------------

pytestmark_slow = pytest.mark.slow


@pytest.fixture
def sbm_run(fresh_run, gen_spec):
    """Use fresh_run only when this test is being run against sbm."""
    if gen_spec.name != "sbm":
        pytest.skip("sbm-specific test")
    return fresh_run


@pytest.mark.slow
def test_sbm_com_csv_is_passthrough_of_input_clustering(sbm_run):
    """The docs say: com.csv = input clustering with singleton clusters
    dropped. Check that every (node_id, cluster_id) in output com.csv
    appears as-is in the input clustering."""
    out, _ = sbm_run
    out_com = pd.read_csv(out / "com.csv", dtype=str)
    in_com = pd.read_csv(CLUSTERING, dtype=str)

    # Build (node_id → cluster_id) dict for the input.
    in_map = dict(zip(in_com["node_id"], in_com["cluster_id"]))
    for _, row in out_com.iterrows():
        assert row["node_id"] in in_map, (
            f"sbm: node {row['node_id']} in output com.csv not in input"
        )
        assert in_map[row["node_id"]] == row["cluster_id"], (
            f"sbm: node {row['node_id']} re-labeled "
            f"{in_map[row['node_id']]} -> {row['cluster_id']}"
        )


@pytest.mark.slow
def test_sbm_cluster_count_equals_input_non_singleton(sbm_run):
    """Cluster count in output = input clusters minus singleton clusters."""
    out, _ = sbm_run
    in_com = pd.read_csv(CLUSTERING, dtype=str)
    in_sizes = in_com["cluster_id"].value_counts()
    in_non_singleton = (in_sizes > 1).sum()

    out_com = pd.read_csv(out / "com.csv", dtype=str)
    out_clusters = out_com["cluster_id"].nunique()
    assert out_clusters == in_non_singleton, (
        f"sbm: expected {in_non_singleton} clusters (input minus singletons), "
        f"got {out_clusters}"
    )


@pytest.mark.slow
def test_sbm_no_new_cluster_ids_appear(sbm_run):
    """The output partition is a subset of the input partition (minus
    singletons). No new cluster_id is fabricated by sbm's gen."""
    out, _ = sbm_run
    out_com = pd.read_csv(out / "com.csv", dtype=str)
    in_com = pd.read_csv(CLUSTERING, dtype=str)
    out_clusters = set(out_com["cluster_id"])
    in_clusters = set(in_com["cluster_id"])
    novel = out_clusters - in_clusters
    assert not novel, f"sbm: fabricated clusters {novel}"
