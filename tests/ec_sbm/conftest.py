"""Fixtures for the ec-sbm pipeline suite (v1 and v2).

Session-scoped `fresh_run` lets many artifact-observation tests share a
single pipeline invocation per (generator, keep_state) pair. Mutation tests
take `tmp_output_dir` + explicit `run_generator` calls.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_GENERATOR = REPO_ROOT / "run_generator.sh"
EXAMPLES_IN = REPO_ROOT / "examples" / "input"

INP_EDGE = EXAMPLES_IN / "empirical_networks" / "networks" / "dnc" / "dnc.csv"
INP_COM = (
    EXAMPLES_IN
    / "reference_clusterings"
    / "clusterings"
    / "sbm-flat-best+cc"
    / "dnc"
    / "com.csv"
)


def _env() -> dict:
    env = os.environ.copy()
    env["PATH"] = f"/u/vltanh/miniconda3/envs/nw/bin:{env.get('PATH', '')}"
    env["OMP_NUM_THREADS"] = "1"
    return env


def run_generator(
    generator: str,
    output_dir: Path,
    extra: list[str] | None = None,
    inp_edge: Path = INP_EDGE,
    inp_com: Path = INP_COM,
) -> subprocess.CompletedProcess:
    cmd = [
        str(RUN_GENERATOR),
        "--generator", generator,
        "--run-id", "0",
        "--input-edgelist", str(inp_edge),
        "--input-clustering", str(inp_com),
        "--output-dir", str(output_dir),
        "--network", "dnc",
        "--clustering-id", "sbm-flat-best+cc",
    ]
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, cwd=REPO_ROOT, env=_env(), capture_output=True, text=True)


def run_dir(output_root: Path, generator: str) -> Path:
    return output_root / "networks" / generator / "sbm-flat-best+cc" / "dnc" / "0"


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "synthetic_networks"
    d.mkdir()
    return d


@pytest.fixture(scope="session")
def _fresh_run_cache() -> dict:
    return {}


@pytest.fixture(params=["ec-sbm-v1", "ec-sbm-v2"])
def generator(request) -> str:
    return request.param


@pytest.fixture
def fresh_run(generator: str, tmp_path_factory, _fresh_run_cache: dict):
    """Session-cached default-mode run per generator. Returns (out_dir, proc)."""
    if generator not in _fresh_run_cache:
        out_root = tmp_path_factory.mktemp(f"ecsbm_fresh_{generator}")
        proc = run_generator(generator, out_root)
        assert proc.returncode == 0, (
            f"fresh_run fixture: {generator} failed:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        _fresh_run_cache[generator] = (out_root, proc)
    out_root, proc = _fresh_run_cache[generator]
    return run_dir(out_root, generator), proc
