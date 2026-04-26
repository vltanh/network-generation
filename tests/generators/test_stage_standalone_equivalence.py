"""Stage-standalone equivalence test for ec-sbm.

Run the full pipeline once; then for each stage, invoke its python entry
point directly with the same inputs and compare the byte-output to the
pipeline's `.state/<stage>/` snapshot. Catches drift between
`pipeline.sh`'s stage invocation and a hand-run standalone invocation
(env var leaks, missing flags, path differences).

Why this matters: PYTHONHASHSEED-determinism only proves the pipeline
gives the same bytes across hash-seed values. It does not prove that a
user re-running a single stage by hand reproduces the pipeline's
intermediate. The stages are publicly importable scripts; if `pipeline.sh`
relies on env vars or argument shapes that the stage's docstring or CLI
help does not advertise, the standalone run drifts silently.

Marked `slow`. ec-sbm-v1 and ec-sbm-v2 only — the simple gens have one
substantive stage each (profile + gen) and the surface drift is small.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import (
    INP_COM, INP_EDGE, REPO_ROOT, _env, run_dir, run_generator,
)


pytestmark = pytest.mark.slow


# Per-version CLI flag resolution (mirrors src/ec-sbm/pipeline.sh:54-78).
VERSION_FLAGS = {
    "ec-sbm-v1": {
        "outlier_mode": "excluded",
        "drop_oo": "false",
        "sbm_overlay": "true",
        "scope": "outlier-incident",
        "gen_outlier_mode": "singleton",
        "edge_correction": "none",
        "algorithm": "greedy",
    },
    "ec-sbm-v2": {
        "outlier_mode": "excluded",
        "drop_oo": "false",
        "sbm_overlay": "false",
        "scope": "all",
        "gen_outlier_mode": "combined",
        "edge_correction": "rewire",
        "algorithm": "hybrid",
    },
}

PACKAGE_DIR = REPO_ROOT / "externals" / "ec-sbm"
PACKAGE_PY = PACKAGE_DIR / "src"
SRC_DIR = REPO_ROOT / "src"
SEED = 1


def _stage_env() -> dict:
    """Match pipeline.sh's env: PYTHONPATH = SRC_DIR:PACKAGE_PY, plus the
    PYTHONHASHSEED=0 pin so the standalone invocation lands in the same
    determinism regime as pipeline.sh.
    """
    env = _env()
    paths = [str(SRC_DIR), str(PACKAGE_PY)]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = ":".join(paths)
    env["OMP_NUM_THREADS"] = "1"
    env["PYTHONHASHSEED"] = "0"
    return env


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _data_files(root: Path) -> dict[str, str]:
    """Walk `root`, hash every .csv / .json / .txt file. Skip logs +
    `done` (timestamps), skip params.txt (test re-creates them on the
    standalone side, sometimes with the same bytes; safer to skip the
    self-input).
    """
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in {"run.log", "time_and_err.log", "done", "params.txt"}:
            continue
        if p.suffix not in (".csv", ".json"):
            continue
        out[p.relative_to(root).as_posix()] = _sha256_file(p)
    return out


def _diff(a: dict[str, str], b: dict[str, str]) -> list[str]:
    out: list[str] = []
    for k in sorted(set(a) - set(b)):
        out.append(f"{k}: only in pipeline run")
    for k in sorted(set(b) - set(a)):
        out.append(f"{k}: only in standalone run")
    for k in sorted(set(a) & set(b)):
        if a[k] != b[k]:
            out.append(f"{k}: {a[k][:12]} vs {b[k][:12]}")
    return out


def _run(cmd: list[str], env: dict, cwd: Path | None = None):
    return subprocess.run(
        cmd, env=env, cwd=cwd or REPO_ROOT,
        capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def pipeline_states(tmp_path_factory):
    """Run ec-sbm-v1 and ec-sbm-v2 once each with --keep-state, return
    a dict {gen_name: state_dir}. Module-scoped to share between the
    parametrized stage tests.
    """
    if not PACKAGE_DIR.exists():
        pytest.skip(f"externals/ec-sbm missing at {PACKAGE_DIR}")
    out = {}
    for gen in VERSION_FLAGS:
        from .conftest import GenSpec
        spec = next(
            s for s in __import__(
                "tests.generators.conftest", fromlist=["SPECS"]
            ).SPECS if s.name == gen
        )
        out_root = tmp_path_factory.mktemp(f"pipeline_{gen.replace('-', '_')}")
        proc = run_generator(spec, out_root, _env(), extra=["--keep-state"])
        assert proc.returncode == 0, (
            f"pipeline run for {gen} failed:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        out[gen] = run_dir(out_root, gen) / ".state"
    return out


def _replay_profile(state_dir: Path, version: str, tmp: Path):
    flags = VERSION_FLAGS[version]
    params = tmp / "params.txt"
    params.write_text(
        f"drop_outlier_outlier_edges={flags['drop_oo']}\n"
        f"outlier_mode={flags['outlier_mode']}\n"
    )
    cmd = [
        "python", str(PACKAGE_PY / "profile.py"),
        "--edgelist", str(INP_EDGE),
        "--clustering", str(INP_COM),
        "--output-folder", str(tmp),
        "--params-file", str(params),
    ]
    proc = _run(cmd, _stage_env())
    assert proc.returncode == 0, proc.stderr
    return _data_files(tmp), _data_files(state_dir / "profile")


def _replay_gen_clustered(state_dir: Path, version: str, tmp: Path):
    flags = VERSION_FLAGS[version]
    overlay = "--sbm-overlay" if flags["sbm_overlay"] == "true" else "--no-sbm-overlay"
    profile_dir = state_dir / "profile"
    cmd = [
        "python", str(PACKAGE_PY / "gen_clustered.py"),
        "--node-id", str(profile_dir / "node_id.csv"),
        "--cluster-id", str(profile_dir / "cluster_id.csv"),
        "--assignment", str(profile_dir / "assignment.csv"),
        "--degree", str(profile_dir / "degree.csv"),
        "--mincut", str(profile_dir / "mincut.csv"),
        "--edge-counts", str(profile_dir / "edge_counts.csv"),
        "--output-folder", str(tmp),
        "--seed", str(SEED),
        overlay,
    ]
    proc = _run(cmd, _stage_env())
    assert proc.returncode == 0, proc.stderr
    return _data_files(tmp), _data_files(state_dir / "gen_clustered")


def _replay_gen_outlier(state_dir: Path, version: str, tmp: Path):
    flags = VERSION_FLAGS[version]
    cmd = [
        "python", str(PACKAGE_PY / "gen_outlier.py"),
        "--orig-edgelist", str(INP_EDGE),
        "--orig-clustering", str(INP_COM),
        "--scope", flags["scope"],
        "--outlier-mode", flags["gen_outlier_mode"],
        "--edge-correction", flags["edge_correction"],
        "--output-folder", str(tmp),
        "--seed", str(SEED + 1),
    ]
    if flags["scope"] == "all":
        cmd.extend([
            "--exist-edgelist", str(state_dir / "gen_clustered" / "edge.csv"),
        ])
    proc = _run(cmd, _stage_env())
    assert proc.returncode == 0, proc.stderr
    return _data_files(tmp), _data_files(state_dir / "gen_outlier" / "edges")


def _replay_match_degree(state_dir: Path, version: str, tmp: Path):
    flags = VERSION_FLAGS[version]
    cmd = [
        "python", str(SRC_DIR / "match_degree.py"),
        "--input-edgelist", str(state_dir / "gen_outlier" / "edge.csv"),
        "--ref-edgelist", str(INP_EDGE),
        "--match-degree-algorithm", flags["algorithm"],
        "--output-folder", str(tmp),
        "--seed", str(SEED + 2),
    ]
    proc = _run(cmd, _stage_env())
    assert proc.returncode == 0, proc.stderr
    return _data_files(tmp), _data_files(state_dir / "match_degree" / "edges")


REPLAY_STAGES = [
    ("profile",       _replay_profile),
    ("gen_clustered", _replay_gen_clustered),
    ("gen_outlier",   _replay_gen_outlier),
    ("match_degree",  _replay_match_degree),
]


@pytest.mark.parametrize("version", list(VERSION_FLAGS))
@pytest.mark.parametrize("stage_name,replay", REPLAY_STAGES, ids=[s[0] for s in REPLAY_STAGES])
def test_stage_standalone_matches_pipeline(
    pipeline_states, version, stage_name, replay, tmp_path
):
    """Standalone re-invocation of `<stage>` produces byte-equal output
    to what `pipeline.sh` produced for the same gen + same inputs.
    """
    state_dir = pipeline_states[version]
    standalone_tmp = tmp_path / "stage_out"
    standalone_tmp.mkdir()
    standalone_hashes, pipeline_hashes = replay(state_dir, version, standalone_tmp)

    diff = _diff(pipeline_hashes, standalone_hashes)
    assert not diff, (
        f"{version}/{stage_name}: standalone re-invocation differs from "
        f"pipeline output:\n  " + "\n  ".join(diff)
        + f"\n\npipeline_state={state_dir}\nstandalone={standalone_tmp}"
    )
