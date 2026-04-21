"""Fixtures for the simple-generator pipeline suite.

The 5 simple generators (`sbm`, `abcd`, `abcd+o`, `lfr`, `npso`) all share
the same two-stage shape. Some depend on external binaries that may or may
not be installed locally; the `gen_spec` fixture parametrizes the suite and
skips rather than fails when a binary is absent.

A session-scoped `fresh_run_dir` fixture runs each generator once per test
session and returns the output dir so many artifact-observation tests can
share the same pipeline invocation.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
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


@dataclass(frozen=True)
class GenSpec:
    name: str
    binary_env: dict
    binary_check: Path | None


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


def _env() -> dict:
    env = os.environ.copy()
    prefix = os.environ.get("NW_TEST_PATH_PREFIX")
    if prefix:
        env["PATH"] = f"{prefix}:{env.get('PATH', '')}"
    env["OMP_NUM_THREADS"] = "1"
    return env


def run_generator(
    gen_spec: GenSpec,
    output_dir: Path,
    env: dict,
    inp_edge: Path = INP_EDGE,
    inp_com: Path = INP_COM,
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess:
    cmd = [
        str(RUN_GENERATOR),
        "--generator", gen_spec.name,
        "--run-id", "0",
        "--input-edgelist", str(inp_edge),
        "--input-clustering", str(inp_com),
        "--output-dir", str(output_dir),
        "--network", "dnc",
        "--clustering-id", "sbm-flat-best+cc",
        "--seed", "0",
        "--n-threads", "1",
    ]
    for flag, val in gen_spec.binary_env.items():
        cmd.extend([flag, val])
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def run_dir(output_root: Path, gen_name: str) -> Path:
    return output_root / "networks" / gen_name / "sbm-flat-best+cc" / "dnc" / "0"


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


@pytest.fixture
def subprocess_env() -> dict:
    return _env()


@pytest.fixture(scope="session")
def _fresh_run_cache(tmp_path_factory) -> dict:
    """Session-scoped cache mapping gen name → (output_root, CompletedProcess).

    Populated lazily by `fresh_run`. Tests that only inspect artifacts of a
    one-shot pipeline run share this cache instead of re-invoking.
    """
    return {}


@pytest.fixture
def fresh_run(gen_spec: GenSpec, tmp_path_factory, _fresh_run_cache: dict):
    """Session-cached one-shot pipeline run per generator.

    Returns a tuple `(out_dir, completed_process)`. The pipeline is invoked
    at most once per generator per test session. Tests that mutate the
    output tree must NOT use this fixture — use `tmp_output_dir` + explicit
    `run_generator(...)` calls instead.
    """
    if gen_spec.name not in _fresh_run_cache:
        out_root = tmp_path_factory.mktemp(f"fresh_{gen_spec.name.replace('+', '_')}")
        proc = run_generator(gen_spec, out_root, _env())
        assert proc.returncode == 0, (
            f"fresh_run fixture: {gen_spec.name} pipeline failed:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        _fresh_run_cache[gen_spec.name] = (out_root, proc)
    out_root, proc = _fresh_run_cache[gen_spec.name]
    return run_dir(out_root, gen_spec.name), proc
