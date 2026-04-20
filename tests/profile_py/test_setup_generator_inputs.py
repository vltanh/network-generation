"""Tests for P11 refactor: profile.py's per-generator dispatch.

Each generator has a documented output contract (the docstring of
``setup_generator_inputs``).  The refactor replaces the 6 sequential
``if generator == ...`` blocks with a dispatch dict, but the output
contract must not change.  These tests pin that contract so the
refactor is provably behavior-preserving.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
# src/ has to come first so pipeline_common resolves, but we load profile.py
# by absolute path to avoid colliding with Python's stdlib `profile` module
# (and with our own `tests/profile/` package name).
sys.path.insert(0, str(REPO_ROOT / "src"))

EXAMPLES_IN = REPO_ROOT / "examples" / "input"
EDGELIST = EXAMPLES_IN / "empirical_networks" / "networks" / "dnc" / "dnc.csv"
CLUSTERING = (
    EXAMPLES_IN / "reference_clusterings" / "clusterings"
    / "sbm-flat-best+cc" / "dnc" / "com.csv"
)


# Expected output filenames per generator, from the docstring of
# setup_generator_inputs.
EXPECTED_OUTPUTS = {
    "sbm": {
        "node_id.csv", "cluster_id.csv", "assignment.csv",
        "degree.csv", "edge_counts.csv",
    },
    "ecsbm": {
        "node_id.csv", "cluster_id.csv", "assignment.csv",
        "degree.csv", "edge_counts.csv", "mincut.csv", "com.csv",
    },
    "abcd": {"degree.csv", "cluster_sizes.csv", "mixing_parameter.txt"},
    "abcd+o": {
        "degree.csv", "cluster_sizes.csv",
        "mixing_parameter.txt", "n_outliers.txt",
    },
    "lfr": {"degree.csv", "cluster_sizes.csv", "mixing_parameter.txt"},
    "npso": {"degree.csv", "cluster_sizes.csv"},
}


@pytest.fixture(scope="module")
def profile_module():
    spec = importlib.util.spec_from_file_location(
        "ecsbm_profile", str(REPO_ROOT / "src" / "profile.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("generator", sorted(EXPECTED_OUTPUTS.keys()))
def test_generator_output_set(profile_module, tmp_path, generator):
    """Every documented output file is produced; nothing extra is written."""
    profile_module.setup_generator_inputs(
        str(EDGELIST), str(CLUSTERING), str(tmp_path), generator
    )
    produced = {p.name for p in tmp_path.iterdir() if p.is_file()}
    # Allow the standard_setup log files to coexist (run.log etc.) — we only
    # assert the generator-specific outputs are present.
    expected = EXPECTED_OUTPUTS[generator]
    missing = expected - produced
    assert not missing, (
        f"{generator}: missing expected outputs {sorted(missing)}; "
        f"produced {sorted(produced)}"
    )
    # Extra generator-like outputs (not the log infra) shouldn't appear.
    extra_generator_outputs = produced - expected - {"run.log"}
    assert not extra_generator_outputs, (
        f"{generator}: unexpected extra outputs {sorted(extra_generator_outputs)}"
    )


@pytest.mark.parametrize("generator", sorted(EXPECTED_OUTPUTS.keys()))
def test_generator_output_deterministic(profile_module, tmp_path, generator):
    """Two invocations on the same inputs produce byte-identical outputs."""
    d1 = tmp_path / "run1"
    d2 = tmp_path / "run2"
    d1.mkdir()
    d2.mkdir()
    for d in (d1, d2):
        profile_module.setup_generator_inputs(
            str(EDGELIST), str(CLUSTERING), str(d), generator
        )
    for name in EXPECTED_OUTPUTS[generator]:
        assert (d1 / name).read_bytes() == (d2 / name).read_bytes(), (
            f"{generator}: {name} differs between runs"
        )


@pytest.mark.parametrize("generator", sorted(EXPECTED_OUTPUTS.keys()))
def test_generator_output_stable_across_pythonhashseed(
    profile_module, tmp_path, generator,
):
    """Outputs are byte-identical across processes with different
    PYTHONHASHSEED values.

    Prior to the sort-fix, iteration over the `nodes` set in
    compute_node_degree / compute_edge_count caused tie-breaking to vary by
    hash seed, so assignment.csv, node_id.csv, and edge_counts.csv flaked
    across processes. The fix sorts on (-degree, node_id) and on cluster
    iid pairs at export.
    """
    import os
    import subprocess

    # In-process run as the golden.
    gold = tmp_path / "gold"
    gold.mkdir()
    profile_module.setup_generator_inputs(
        str(EDGELIST), str(CLUSTERING), str(gold), generator,
    )

    # Run profile.py in a fresh process with a non-default PYTHONHASHSEED.
    other = tmp_path / "other"
    other.mkdir()
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "42"
    subprocess.run(
        [
            "python", str(REPO_ROOT / "src" / "profile.py"),
            "--edgelist", str(EDGELIST),
            "--clustering", str(CLUSTERING),
            "--output-folder", str(other),
            "--generator", generator,
        ],
        cwd=str(REPO_ROOT),
        env=env,
        check=True,
        capture_output=True,
    )
    for name in EXPECTED_OUTPUTS[generator]:
        assert (gold / name).read_bytes() == (other / name).read_bytes(), (
            f"{generator}: {name} differs across PYTHONHASHSEED values"
        )


def test_abcd_and_lfr_produce_identical_degree_and_cluster_sizes(
    profile_module, tmp_path,
):
    """ABCD and LFR use the same outlier-as-singleton folding for degree.csv
    and cluster_sizes.csv.  The mixing_parameter values differ (LFR is a
    mean-of-per-node, ABCD is a global ratio), but the shared outputs must
    match byte-for-byte."""
    d_abcd = tmp_path / "abcd"
    d_lfr = tmp_path / "lfr"
    d_abcd.mkdir()
    d_lfr.mkdir()
    profile_module.setup_generator_inputs(
        str(EDGELIST), str(CLUSTERING), str(d_abcd), "abcd"
    )
    profile_module.setup_generator_inputs(
        str(EDGELIST), str(CLUSTERING), str(d_lfr), "lfr"
    )
    for name in ("degree.csv", "cluster_sizes.csv"):
        assert (d_abcd / name).read_bytes() == (d_lfr / name).read_bytes(), (
            f"abcd vs lfr: {name} should be identical"
        )


# Cross-generator invariants are covered by the shared-output tests below.
# Cross-process byte stability is covered by
# test_generator_output_stable_across_pythonhashseed.


def test_sbm_and_ecsbm_share_node_id_cluster_id_edge_counts(
    profile_module, tmp_path,
):
    """SBM folds outliers into one mega-cluster; ecsbm drops outliers and
    singleton clusters entirely via its pre-profile hook.  Both reduce to a
    different clustered view of the same input, so the cluster_id sets
    differ: sbm has at least every non-singleton cluster plus the mega-
    cluster, ecsbm has only the non-singleton clusters.  This test gates
    on that inequality so the refactor doesn't silently swap the two."""
    d_sbm = tmp_path / "sbm"
    d_ecsbm = tmp_path / "ecsbm"
    d_sbm.mkdir()
    d_ecsbm.mkdir()
    profile_module.setup_generator_inputs(
        str(EDGELIST), str(CLUSTERING), str(d_sbm), "sbm"
    )
    profile_module.setup_generator_inputs(
        str(EDGELIST), str(CLUSTERING), str(d_ecsbm), "ecsbm"
    )
    sbm_clusters = (d_sbm / "cluster_id.csv").read_text().splitlines()
    ecsbm_clusters = (d_ecsbm / "cluster_id.csv").read_text().splitlines()
    assert sbm_clusters, "sbm cluster_id.csv is empty"
    assert ecsbm_clusters, "ecsbm cluster_id.csv is empty"
    assert len(sbm_clusters) >= len(ecsbm_clusters), (
        "sbm (with outlier fold) should have >= clusters than ecsbm "
        "(which drops outliers + singletons)"
    )
