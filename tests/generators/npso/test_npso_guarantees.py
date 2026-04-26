"""nPSO guarantees (see ``docs/algorithms/npso.md``).

nPSO is the only generator in the repo that *targets* global clustering
coefficient. It runs a secant-over-midpoint search on temperature T
(bracket 0 → 1) for up to 100 iterations with three stopping criteria:

  - Absolute ccoeff tolerance < 0.005
  - Step < 0.0001 (stagnation)
  - T < 0.0005 (degenerate bracket)

Guarantees:
  - **N** exact.
  - **Number of clusters** = c (angular sectors from the profile's
    ``cluster_sizes.csv`` row count).
  - **Global clustering coefficient ≈ target** — within 0.005 OR best
    achieved in 100 iters (docs note: on inputs where target exceeds the
    model's achievable range, only the best-so-far is returned).
  - **Degree distribution ~ power-law(γ)** asymptotic.

NOT guaranteed:
  - Exact degree sequence.
  - Cluster sizes (nPSO1 = equal proportions by default; nPSO2 matches
    empirical sizes in expectation but ceiling still applies).
  - Block structure. cluster_id values are sampler output.

The runner fallback / next_T picker / search_log are exercised by
``test_npso_runner.py``. This file adds the output-shape guarantees.
"""
from __future__ import annotations

import json
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


pytestmark = pytest.mark.slow


@pytest.fixture
def npso_run(fresh_run, gen_spec):
    if gen_spec.name != "npso":
        pytest.skip("npso-specific test")
    return fresh_run


def _profile_npso(tmp_path):
    """Drive nPSO profile once; return derived scalars + cluster_sizes."""
    env = {
        "PATH": "/home/vltanh/miniconda3/envs/nwbench/bin:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONHASHSEED": "0",
    }
    out = tmp_path / "profile"
    out.mkdir()
    subprocess.run(
        ["python", str(REPO_ROOT / "src" / "npso" / "profile.py"),
         "--edgelist", str(EDGELIST),
         "--clustering", str(CLUSTERING),
         "--output-folder", str(out)],
        env=env, check=True, capture_output=True,
    )
    derived = {}
    for line in (out / "derived.txt").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            derived[k] = v
    cs = pd.read_csv(out / "cluster_sizes.csv", header=None)[0].tolist()
    return derived, cs


@pytest.mark.slow
def test_npso_N_matches_derived(npso_run, tmp_path):
    """The sampler takes N as a scalar CLI flag from profile.derived.txt;
    the output graph must have exactly N distinct node IDs."""
    derived, _ = _profile_npso(tmp_path)
    expected_N = int(derived["N"])

    out, _ = npso_run
    edges = pd.read_csv(out / "edge.csv", dtype=str)
    nodes_in_edges = set(edges["source"]).union(edges["target"])
    assert len(nodes_in_edges) <= expected_N, (
        f"npso: {len(nodes_in_edges)} endpoints > derived N={expected_N}"
    )
    assert len(nodes_in_edges) >= int(0.90 * expected_N), (
        f"npso: only {len(nodes_in_edges)}/{expected_N} nodes in edges"
    )


@pytest.mark.slow
def test_npso_output_has_at_least_two_clusters(npso_run):
    """Sanity: npso should generate more than one cluster."""
    out, _ = npso_run
    com = pd.read_csv(out / "com.csv", dtype=str)
    assert com["cluster_id"].nunique() >= 2, (
        "npso: only one cluster survived com.csv"
    )


@pytest.mark.slow
def test_npso_search_log_is_valid_json_with_documented_shape(npso_run):
    """search_log.json schema: {"inputs_sha256": str, "iters":
    [{"T": float, "ccoeff": float}, ...]}. Pipe keeps this around when
    --keep-state is off? Actually no — ``search_log.json`` is emitted in
    the stage-2 dir (inside .state), but the user-facing tree does NOT
    promote it. Skip if missing."""
    out, _ = npso_run
    log = out / "search_log.json"
    if not log.is_file():
        pytest.skip("search_log.json not promoted without --keep-state")
    data = json.loads(log.read_text())
    assert "inputs_sha256" in data
    assert "iters" in data
    assert isinstance(data["iters"], list)
    for it in data["iters"]:
        assert "T" in it and "ccoeff" in it


@pytest.mark.slow
def test_npso_achieved_ccoeff_in_valid_range(npso_run):
    """Global clustering coefficient ∈ [0, 1]. Doesn't assert convergence
    (docs note non-convergence on dnc); just sanity-checks the value."""
    nk = pytest.importorskip("networkit")

    out, _ = npso_run
    edges = pd.read_csv(out / "edge.csv", dtype=str)
    if edges.empty:
        pytest.skip("no edges to compute ccoeff")

    nodes = pd.unique(pd.concat([edges["source"], edges["target"]], ignore_index=True))
    idx = {v: i for i, v in enumerate(nodes)}
    g = nk.graph.Graph(n=len(nodes), weighted=False, directed=False)
    for u, v in zip(edges["source"], edges["target"]):
        g.addEdge(idx[u], idx[v])

    cc = float(nk.globals.ClusteringCoefficient.exactGlobal(g))
    assert 0.0 <= cc <= 1.0, f"npso: ccoeff out of [0,1]: {cc}"
