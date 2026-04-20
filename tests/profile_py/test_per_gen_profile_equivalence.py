"""Byte-equivalence check: per-generator profile modules produce the same
outputs as the legacy monolithic src/profile.py.

Pins the refactor to "behavior-preserving".  The new per-generator
profile.py modules dissolve the dispatch registry in src/profile.py but
must not change any output file.
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


# (generator, per_gen_profile_module_path_parts)
_CASES = [
    ("sbm",    ("sbm", "profile.py")),
    ("abcd",   ("abcd", "profile.py")),
    ("abcd+o", ("abcd+o", "profile.py")),
    ("lfr",    ("lfr", "profile.py")),
    ("npso",   ("npso", "profile.py")),
    ("ecsbm",  ("ec-sbm", "common", "profile.py")),
]


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
def legacy_profile():
    """The monolithic src/profile.py — deliberately loaded by absolute path
    so it doesn't collide with Python's stdlib `profile`."""
    spec = importlib.util.spec_from_file_location(
        "legacy_profile", str(REPO_ROOT / "src" / "profile.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_per_gen(module_parts):
    """Load src/<parts>/profile.py as a standalone module.

    We stick the generator dir on sys.path so any local imports resolve,
    then clean it back off.
    """
    path = REPO_ROOT / "src" / Path(*module_parts)
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


@pytest.mark.parametrize("generator,path_parts", _CASES)
def test_per_gen_profile_matches_legacy(
    legacy_profile, tmp_path, generator, path_parts,
):
    legacy_out = tmp_path / "legacy"
    new_out = tmp_path / "new"
    legacy_out.mkdir()
    new_out.mkdir()

    legacy_profile.setup_generator_inputs(
        str(EDGELIST), str(CLUSTERING), str(legacy_out), generator,
    )

    per_gen = _load_per_gen(path_parts)
    per_gen.setup_inputs(str(EDGELIST), str(CLUSTERING), str(new_out))

    for name in EXPECTED_OUTPUTS[generator]:
        assert (new_out / name).read_bytes() == (legacy_out / name).read_bytes(), (
            f"{generator}: {name} differs from legacy profile.py output"
        )
