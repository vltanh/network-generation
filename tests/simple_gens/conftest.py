"""Fixtures for the simple-generator pipeline suite.

The 5 simple generators (`sbm`, `abcd`, `abcd+o`, `lfr`, `npso`) all share
the same two-stage shape (profile + gen).  Some depend on external binaries
that may or may not be installed locally; the `simple_generator` fixture
parametrizes the suite and skips rather than fails when a binary is absent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class GenSpec:
    name: str
    binary_env: dict  # extra CLI flags for run_generator.sh
    binary_check: Path | None  # file/dir that must exist for tests to run


SPECS = [
    GenSpec("sbm", {}, None),
    GenSpec(
        "abcd",
        {"--abcd-dir": str(REPO_ROOT / "externals" / "abcd")},
        REPO_ROOT / "externals" / "abcd",
    ),
    GenSpec(
        "abcd+o",
        {"--abcd-dir": str(REPO_ROOT / "externals" / "abcd")},
        REPO_ROOT / "externals" / "abcd",
    ),
    GenSpec(
        "lfr",
        {"--lfr-binary": str(REPO_ROOT / "externals" / "lfr" / "unweighted_undirected" / "benchmark")},
        REPO_ROOT / "externals" / "lfr" / "unweighted_undirected" / "benchmark",
    ),
    GenSpec(
        "npso",
        {"--npso-dir": str(REPO_ROOT / "externals" / "npso")},
        REPO_ROOT / "externals" / "npso",
    ),
]


@pytest.fixture(params=SPECS, ids=[s.name for s in SPECS])
def gen_spec(request) -> GenSpec:
    spec: GenSpec = request.param
    if spec.binary_check is not None and not spec.binary_check.exists():
        pytest.skip(f"{spec.name}: required binary/dir missing at {spec.binary_check}")
    return spec


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "synthetic_networks"
    d.mkdir()
    return d


def _env() -> dict:
    env = os.environ.copy()
    env["PATH"] = f"/u/vltanh/miniconda3/envs/nw/bin:{env.get('PATH', '')}"
    env["OMP_NUM_THREADS"] = "1"
    return env


@pytest.fixture
def subprocess_env() -> dict:
    return _env()
