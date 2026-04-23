"""Fixtures + GenSpec for the unified 7-generator integration suite.

Covers sbm, abcd, abcd+o, lfr, npso, ec-sbm-v1, ec-sbm-v2. All tests
drive `run_generator.sh` as a subprocess and skip when the generator's
required external dependency is missing.

Two generator families:
  * **Simple** (sbm, abcd, abcd+o, lfr, npso): two-stage pipeline with
    `.state/setup/` + `.state/gen/` and user-facing files
    `{edge.csv, com.csv, done, run.log, params.txt}`.
  * **ec-sbm** (v1, v2): six-stage pipeline with `.state/{profile,
    gen_clustered, gen_outlier, match_degree}/` plus `sources.json` in
    the user-facing tree.

Each generator's `GenSpec` carries the per-family differences so tests
stay parametrized across all 7.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
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
    external_flag: str | None
    external_path: Path | None
    user_facing: frozenset[str]
    stages: tuple[str, ...]
    state_stage_dirs: tuple[str, ...]
    # Path (relative to .state/) of a stage intermediate whose deletion
    # breaks .state/ consistency without touching user-facing outputs.
    inconsistency_probe: str
    # User-facing file whose corruption invalidates the top-level done
    # but is not tracked by any stage's done (so rerun should rebuild
    # top-level from cached stages without re-running any stage).
    #   simple-gens: edge.csv — stage 2 does not rehash post-promotion.
    #   ec-sbm: com.csv — promoted from profile at end, no stage tracks
    #                     it in its done hash-set.
    untracked_user_file: str

    @property
    def has_sources_json(self) -> bool:
        return "sources.json" in self.user_facing


SIMPLE_USER_FACING = frozenset({"edge.csv", "com.csv", "done", "run.log", "params.txt"})
SIMPLE_STAGES = ("1 (profile)", "2 (gen)")
SIMPLE_STATE_DIRS = ("setup", "gen")
SIMPLE_PROBE = "gen/edge.csv"

ECSBM_USER_FACING = frozenset({
    "edge.csv", "com.csv", "sources.json", "done", "run.log", "params.txt",
})
ECSBM_STAGES = (
    "1 (profile)",
    "2 (gen_clustered)",
    "3a (gen_outlier)",
    "3b (gen_outlier/combine)",
    "4a (match_degree)",
    "4b (match_degree/combine)",
)
ECSBM_STATE_DIRS = ("profile", "gen_clustered", "gen_outlier", "match_degree")
ECSBM_PROBE = "gen_clustered/edge.csv"


SPECS: list[GenSpec] = [
    GenSpec(
        "sbm", None, None,
        SIMPLE_USER_FACING, SIMPLE_STAGES, SIMPLE_STATE_DIRS, SIMPLE_PROBE,
        "edge.csv",
    ),
    GenSpec(
        "abcd", "--abcd-dir", REPO_ROOT / "externals" / "abcd",
        SIMPLE_USER_FACING, SIMPLE_STAGES, SIMPLE_STATE_DIRS, SIMPLE_PROBE,
        "edge.csv",
    ),
    GenSpec(
        "abcd+o", "--abcd-dir", REPO_ROOT / "externals" / "abcd",
        SIMPLE_USER_FACING, SIMPLE_STAGES, SIMPLE_STATE_DIRS, SIMPLE_PROBE,
        "edge.csv",
    ),
    GenSpec(
        "lfr", "--lfr-binary",
        REPO_ROOT / "externals" / "lfr" / "unweighted_undirected" / "benchmark",
        SIMPLE_USER_FACING, SIMPLE_STAGES, SIMPLE_STATE_DIRS, SIMPLE_PROBE,
        "edge.csv",
    ),
    GenSpec(
        "npso", "--npso-dir", REPO_ROOT / "externals" / "npso",
        SIMPLE_USER_FACING, SIMPLE_STAGES, SIMPLE_STATE_DIRS, SIMPLE_PROBE,
        "edge.csv",
    ),
    GenSpec(
        "ec-sbm-v1", "--ec-sbm-dir", REPO_ROOT / "externals" / "ec-sbm",
        ECSBM_USER_FACING, ECSBM_STAGES, ECSBM_STATE_DIRS, ECSBM_PROBE,
        "com.csv",
    ),
    GenSpec(
        "ec-sbm-v2", "--ec-sbm-dir", REPO_ROOT / "externals" / "ec-sbm",
        ECSBM_USER_FACING, ECSBM_STAGES, ECSBM_STATE_DIRS, ECSBM_PROBE,
        "com.csv",
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
    spec: GenSpec,
    output_dir: Path,
    env: dict,
    inp_edge: Path = INP_EDGE,
    inp_com: Path = INP_COM,
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess:
    cmd = [
        str(RUN_GENERATOR),
        "--generator", spec.name,
        "--run-id", "0",
        "--input-edgelist", str(inp_edge),
        "--input-clustering", str(inp_com),
        "--output-dir", str(output_dir),
        "--network", "dnc",
        "--clustering-id", "sbm-flat-best+cc",
        "--seed", "1",
        "--n-threads", "1",
    ]
    if spec.external_flag is not None and spec.external_path is not None:
        cmd.extend([spec.external_flag, str(spec.external_path)])
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def run_dir(output_root: Path, gen_name: str) -> Path:
    return output_root / "networks" / gen_name / "sbm-flat-best+cc" / "dnc" / "0"


@pytest.fixture(params=SPECS, ids=[s.name for s in SPECS])
def gen_spec(request) -> GenSpec:
    spec: GenSpec = request.param
    if spec.external_path is not None and not spec.external_path.exists():
        pytest.skip(
            f"{spec.name}: required binary/dir missing at {spec.external_path}"
        )
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
    """Session cache keyed on gen name → (output_root, CompletedProcess).

    Populated lazily by `fresh_run`. Tests that only read artifacts of a
    one-shot run share this cache instead of re-invoking. Tests that
    mutate the tree must use `tmp_output_dir` + an explicit
    `run_generator(...)` call.
    """
    return {}


@pytest.fixture
def fresh_run(gen_spec: GenSpec, tmp_path_factory, _fresh_run_cache: dict):
    if gen_spec.name not in _fresh_run_cache:
        safe = gen_spec.name.replace("+", "_").replace("-", "_")
        out_root = tmp_path_factory.mktemp(f"fresh_{safe}")
        proc = run_generator(gen_spec, out_root, _env())
        assert proc.returncode == 0, (
            f"fresh_run fixture: {gen_spec.name} failed:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        _fresh_run_cache[gen_spec.name] = (out_root, proc)
    out_root, proc = _fresh_run_cache[gen_spec.name]
    return run_dir(out_root, gen_spec.name), proc
