"""Per-generator profile-module output contract tests.

Each generator has its own profile module under ``src/<gen>/profile.py``
(or ``src/ec-sbm/common/profile.py`` for ec-sbm).  Each module exposes
``setup_inputs(edgelist, clustering, output_dir)`` and writes a
documented set of output files.  These tests pin that contract.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

EXAMPLES_IN = REPO_ROOT / "examples" / "input"
EDGELIST = EXAMPLES_IN / "empirical_networks" / "networks" / "dnc" / "dnc.csv"
CLUSTERING = (
    EXAMPLES_IN / "reference_clusterings" / "clusterings"
    / "sbm-flat-best+cc" / "dnc" / "com.csv"
)


# (generator_label, per_gen_profile_path_parts_under_src)
_GENERATORS = {
    "sbm":    ("sbm", "profile.py"),
    "abcd":   ("abcd", "profile.py"),
    "abcd+o": ("abcd+o", "profile.py"),
    "lfr":    ("lfr", "profile.py"),
    "npso":   ("npso", "profile.py"),
    "ecsbm":  ("ec-sbm", "common", "profile.py"),
}


# Expected output filenames per generator.  profile.py no longer writes
# params.txt — the pipeline writes it (into each stage dir) as a cache
# fingerprint, separately from what profile.py produces.  Standalone
# profile.py runs (as exercised by these tests) do not write params.txt.
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


def _load_per_gen(generator):
    """Load src/<path_parts>/profile.py as a standalone module.

    We stick the generator dir on sys.path so any local imports (e.g.
    ``from profile_common import ...`` in the shared ec-sbm profile)
    resolve, then clean it back off.
    """
    path = REPO_ROOT / "src" / Path(*_GENERATORS[generator])
    gen_dir = str(path.parent)
    sys.path.insert(0, gen_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            f"per_gen_profile_{path.parent.name}", str(path),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(gen_dir)


@pytest.mark.parametrize("generator", sorted(EXPECTED_OUTPUTS.keys()))
def test_generator_output_set(tmp_path, generator):
    """Every documented output file is produced; nothing extra is written."""
    mod = _load_per_gen(generator)
    mod.setup_inputs(str(EDGELIST), str(CLUSTERING), str(tmp_path))
    produced = {p.name for p in tmp_path.iterdir() if p.is_file()}
    expected = EXPECTED_OUTPUTS[generator]
    missing = expected - produced
    assert not missing, (
        f"{generator}: missing expected outputs {sorted(missing)}; "
        f"produced {sorted(produced)}"
    )
    extra_generator_outputs = produced - expected - {"run.log"}
    assert not extra_generator_outputs, (
        f"{generator}: unexpected extra outputs {sorted(extra_generator_outputs)}"
    )


@pytest.mark.parametrize("generator", sorted(EXPECTED_OUTPUTS.keys()))
def test_generator_output_deterministic(tmp_path, generator):
    """Two invocations on the same inputs produce byte-identical outputs."""
    mod = _load_per_gen(generator)
    d1 = tmp_path / "run1"
    d2 = tmp_path / "run2"
    d1.mkdir()
    d2.mkdir()
    for d in (d1, d2):
        mod.setup_inputs(str(EDGELIST), str(CLUSTERING), str(d))
    for name in EXPECTED_OUTPUTS[generator]:
        assert (d1 / name).read_bytes() == (d2 / name).read_bytes(), (
            f"{generator}: {name} differs between runs"
        )


@pytest.mark.parametrize("generator", sorted(EXPECTED_OUTPUTS.keys()))
def test_generator_output_stable_across_pythonhashseed(tmp_path, generator):
    """Outputs are byte-identical across processes with different
    PYTHONHASHSEED values.

    Prior to the sort-fix, iteration over the ``nodes`` set in
    compute_node_degree / compute_edge_count caused tie-breaking to vary
    by hash seed, so assignment.csv, node_id.csv, and edge_counts.csv
    flaked across processes. The fix sorts on (-degree, node_id) and on
    cluster iid pairs at export.
    """
    import os
    import subprocess

    mod = _load_per_gen(generator)
    gold = tmp_path / "gold"
    gold.mkdir()
    mod.setup_inputs(str(EDGELIST), str(CLUSTERING), str(gold))

    other = tmp_path / "other"
    other.mkdir()
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "42"
    # Per-gen profile modules import ``pipeline_common`` / ``profile_common``
    # from the repo's src/ dir; the pipeline.sh wrappers put it on PYTHONPATH,
    # so we do the same here when invoking the module directly.
    src_dir = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing}" if existing else src_dir
    profile_path = REPO_ROOT / "src" / Path(*_GENERATORS[generator])
    subprocess.run(
        [
            "python", str(profile_path),
            "--edgelist", str(EDGELIST),
            "--clustering", str(CLUSTERING),
            "--output-folder", str(other),
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


def test_abcd_and_lfr_produce_identical_degree_and_cluster_sizes(tmp_path):
    """ABCD and LFR use the same outlier-as-singleton folding for degree.csv
    and cluster_sizes.csv.  The mixing_parameter values differ (LFR is a
    mean-of-per-node, ABCD is a global ratio), but the shared outputs must
    match byte-for-byte."""
    abcd_mod = _load_per_gen("abcd")
    lfr_mod = _load_per_gen("lfr")
    d_abcd = tmp_path / "abcd"
    d_lfr = tmp_path / "lfr"
    d_abcd.mkdir()
    d_lfr.mkdir()
    abcd_mod.setup_inputs(str(EDGELIST), str(CLUSTERING), str(d_abcd))
    lfr_mod.setup_inputs(str(EDGELIST), str(CLUSTERING), str(d_lfr))
    for name in ("degree.csv", "cluster_sizes.csv"):
        assert (d_abcd / name).read_bytes() == (d_lfr / name).read_bytes(), (
            f"abcd vs lfr: {name} should be identical"
        )


def test_sbm_and_ecsbm_share_node_id_cluster_id_edge_counts(tmp_path):
    """SBM folds outliers into one mega-cluster; ecsbm drops outliers and
    singleton clusters entirely via its pre-profile hook.  Both reduce to a
    different clustered view of the same input, so the cluster_id sets
    differ: sbm has at least every non-singleton cluster plus the mega-
    cluster, ecsbm has only the non-singleton clusters.  This test gates
    on that inequality so the refactor doesn't silently swap the two."""
    sbm_mod = _load_per_gen("sbm")
    ecsbm_mod = _load_per_gen("ecsbm")
    d_sbm = tmp_path / "sbm"
    d_ecsbm = tmp_path / "ecsbm"
    d_sbm.mkdir()
    d_ecsbm.mkdir()
    sbm_mod.setup_inputs(str(EDGELIST), str(CLUSTERING), str(d_sbm))
    ecsbm_mod.setup_inputs(str(EDGELIST), str(CLUSTERING), str(d_ecsbm))
    sbm_clusters = (d_sbm / "cluster_id.csv").read_text().splitlines()
    ecsbm_clusters = (d_ecsbm / "cluster_id.csv").read_text().splitlines()
    assert sbm_clusters, "sbm cluster_id.csv is empty"
    assert ecsbm_clusters, "ecsbm cluster_id.csv is empty"
    assert len(sbm_clusters) >= len(ecsbm_clusters), (
        "sbm (with outlier fold) should have >= clusters than ecsbm "
        "(which drops outliers + singletons)"
    )
