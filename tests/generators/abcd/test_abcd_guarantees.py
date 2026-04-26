"""ABCD guarantees (see ``docs/algorithms/abcd.md``).

ABCD.jl contract:
  - **Cluster sizes** exact (from cs.tsv passed to the Julia sampler).
  - **Degree sequence** targeted (from deg.tsv); slightly perturbed by
    rewiring that fixes self-loops + multi-edges.
  - **Global mixing ξ** preserved in expectation: ξ = Σ_out / Σ_total
    measured on the output should land near the profile's ξ.
  - **N** exact.
  - **Node IDs** are integer strings "1".."N" (ABCD's convention).

No block structure guarantee: ABCD regenerates the clustering (com.csv
is sampler output, not a passthrough). Input and output cluster labels
are unrelated.

Slow tests drive the full pipeline. The session-cached ``fresh_run``
fixture is shared with smoke/determinism tests.
"""
from __future__ import annotations

import sys
from collections import Counter
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


pytestmark = pytest.mark.slow


@pytest.fixture
def abcd_run(fresh_run, gen_spec):
    if gen_spec.name != "abcd":
        pytest.skip("abcd-specific test")
    return fresh_run


def _profile_sizes_and_xi(tmp_path):
    """Drive profile.py once, return (expected_sizes_desc, xi)."""
    import subprocess
    env = {
        "PATH": "/home/vltanh/miniconda3/envs/nwbench/bin:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONHASHSEED": "0",
    }
    out = tmp_path / "profile"
    out.mkdir()
    subprocess.run(
        ["python", str(REPO_ROOT / "src" / "abcd" / "profile.py"),
         "--edgelist", str(EDGELIST),
         "--clustering", str(CLUSTERING),
         "--output-folder", str(out)],
        env=env, check=True, capture_output=True,
    )
    sizes = pd.read_csv(out / "cluster_sizes.csv", header=None)[0].tolist()
    xi = float((out / "mixing_parameter.txt").read_text().strip())
    return sorted(sizes, reverse=True), xi


# ---------------------------------------------------------------------------
# Slow tests (use fresh_run cache)
# ---------------------------------------------------------------------------

def test_abcd_cluster_sizes_match_profile(abcd_run, tmp_path):
    """Documented guarantee: cluster sizes exact.

    Compare the output's size distribution (sorted desc) against the
    profile stage-1 ``cluster_sizes.csv`` (also sorted desc). After
    ``drop_singleton_clusters`` runs on the output, singletons may be
    gone — compare size-≥2 only.
    """
    out, _ = abcd_run
    expected_sizes, _ = _profile_sizes_and_xi(tmp_path)
    # Drop singletons from expected (they're also dropped in com.csv).
    expected_non_singleton = sorted(
        [s for s in expected_sizes if s > 1], reverse=True
    )

    com = pd.read_csv(out / "com.csv", dtype=str)
    got_sizes = sorted(Counter(com["cluster_id"]).values(), reverse=True)

    # The Julia sampler assigns nodes to clusters via a matching that
    # respects the cap d_i ≤ s_{C(i)} - 1; for some inputs the exact
    # size cannot be achieved and the sampler re-draws. We accept the
    # multisets match exactly (docs say "exact") up to singleton drop.
    assert got_sizes == expected_non_singleton, (
        f"abcd: cluster size multisets differ.\n"
        f"  profile (non-singleton): {expected_non_singleton[:10]}... ({len(expected_non_singleton)} total)\n"
        f"  output                 : {got_sizes[:10]}... ({len(got_sizes)} total)"
    )


def test_abcd_xi_is_close_to_profile_target(abcd_run, tmp_path):
    """Global ξ on the output should be within a reasonable tolerance of
    the profile's ξ. Rewiring collisions cause small drift; on the
    shipped dnc example, drift is typically < 0.05 absolute."""
    out, _ = abcd_run
    _, target_xi = _profile_sizes_and_xi(tmp_path)

    com = pd.read_csv(out / "com.csv", dtype=str)
    node2com = dict(zip(com["node_id"], com["cluster_id"]))
    edges = pd.read_csv(out / "edge.csv", dtype=str)

    out_sum = 0
    total = 0
    for u, v in zip(edges["source"], edges["target"]):
        c_u = node2com.get(u)
        c_v = node2com.get(v)
        if c_u is None or c_v is None:
            # Unclustered endpoint (filtered from com.csv as singleton).
            # Count it conservatively: unclustered counts as cross.
            out_sum += 2
            total += 2
            continue
        if c_u == c_v:
            total += 2
        else:
            out_sum += 2
            total += 2
    measured = out_sum / total if total else 0
    # Very loose tolerance: ABCD on dnc can drift a few percent.
    assert abs(measured - target_xi) < 0.15, (
        f"abcd: measured xi={measured:.4f} vs target {target_xi:.4f} "
        f"(|Δ|={abs(measured - target_xi):.4f}, tol=0.15)"
    )


def test_abcd_n_nodes_matches_profile(abcd_run, tmp_path):
    """N must equal the profile's N (number of degree-csv rows)."""
    import subprocess
    env = {
        "PATH": "/home/vltanh/miniconda3/envs/nwbench/bin:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONHASHSEED": "0",
    }
    prof = tmp_path / "profile"
    prof.mkdir()
    subprocess.run(
        ["python", str(REPO_ROOT / "src" / "abcd" / "profile.py"),
         "--edgelist", str(EDGELIST),
         "--clustering", str(CLUSTERING),
         "--output-folder", str(prof)],
        env=env, check=True, capture_output=True,
    )
    expected_N = len(pd.read_csv(prof / "degree.csv", header=None))

    out, _ = abcd_run
    edges = pd.read_csv(out / "edge.csv", dtype=str)
    nodes_in_output = set(edges["source"]).union(edges["target"])
    assert len(nodes_in_output) == expected_N, (
        f"abcd: expected {expected_N} nodes, got {len(nodes_in_output)}"
    )


def test_abcd_node_ids_are_integer_strings(abcd_run):
    """ABCD's sampler emits integer node IDs (1..N). Check every
    endpoint parses as an int > 0."""
    out, _ = abcd_run
    edges = pd.read_csv(out / "edge.csv", dtype=str)
    all_ids = set(edges["source"]).union(edges["target"])
    for nid in all_ids:
        assert nid.isdigit() and int(nid) >= 1, (
            f"abcd: non-integer node id {nid!r}"
        )
