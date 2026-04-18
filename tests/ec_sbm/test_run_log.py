"""Tests for the Priority #4 refactor: a single top-level run.log.

After a successful run, the user-facing output directory contains one
persistent debug log — `run.log` — with per-stage prefixes.  All the
per-stage logs that used to live under 14 different files (`run.log` +
`time_and_err.log` per stage) are funneled into this single artifact.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_GENERATOR = REPO_ROOT / "run_generator.sh"
EXAMPLES_IN = REPO_ROOT / "examples" / "input"


pytestmark = pytest.mark.slow


def run_generator(generator: str, output_dir: Path) -> subprocess.CompletedProcess:
    cmd = [
        str(RUN_GENERATOR),
        "--generator", generator,
        "--run-id", "0",
        "--input-edgelist", str(EXAMPLES_IN / "empirical_networks" / "networks" / "dnc" / "dnc.csv"),
        "--input-clustering", str(EXAMPLES_IN / "reference_clusterings" / "clusterings" / "sbm-flat-best+cc" / "dnc" / "com.csv"),
        "--output-dir", str(output_dir),
        "--network", "dnc",
        "--clustering-id", "sbm-flat-best+cc",
    ]
    env = os.environ.copy()
    env["PATH"] = f"/u/vltanh/miniconda3/envs/nw/bin:{env.get('PATH', '')}"
    env["OMP_NUM_THREADS"] = "1"
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def run_dir(output_root: Path, generator: str) -> Path:
    return output_root / "networks" / generator / "sbm-flat-best+cc" / "dnc" / "0"


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "synthetic_networks"
    d.mkdir()
    return d


EC_SBM_STAGES = ("1a", "1b", "1c", "2a", "2b", "3a", "3b")


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_top_level_run_log_exists(tmp_output_dir: Path, generator: str):
    """A top-level `run.log` must be present after a successful run."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)
    log = out / "run.log"
    assert log.is_file(), "top-level run.log missing"
    assert log.stat().st_size > 0, "top-level run.log is empty"


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_run_log_contains_every_stage(tmp_output_dir: Path, generator: str):
    """Every pipeline stage must appear in the consolidated log, with an
    identifiable prefix (`[Stage 1a]`, etc.)."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)
    log_text = (out / "run.log").read_text()
    for stage in EC_SBM_STAGES:
        assert f"[Stage {stage}]" in log_text, (
            f"run.log missing [Stage {stage}] prefix.\n"
            f"first 500 chars:\n{log_text[:500]}"
        )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_no_per_stage_log_files_in_user_tree(
    tmp_output_dir: Path, generator: str
):
    """The legacy per-stage `run.log` / `time_and_err.log` files must not
    survive cleanup — only the single top-level `run.log` remains."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)

    # rglob picks up anything under out/ including .state/, but .state/ should
    # already be gone at this point.
    surviving_logs = [
        p for p in out.rglob("*.log") if p != out / "run.log"
    ]
    assert not surviving_logs, (
        f"unexpected per-stage log files surviving: "
        f"{[str(p.relative_to(out)) for p in surviving_logs]}"
    )
    # time_and_err.log files likewise should be gone.
    stray = list(out.rglob("time_and_err.log"))
    assert not stray, (
        f"legacy time_and_err.log files still present: "
        f"{[str(p.relative_to(out)) for p in stray]}"
    )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_run_log_survives_state_cleanup(
    tmp_output_dir: Path, generator: str
):
    """`run.log` must survive the `.state/` cleanup step — it's meant to be
    a persistent debugging artifact."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)
    assert not (out / ".state").exists()
    assert (out / "run.log").is_file()


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_run_log_not_hashed_in_top_level_done(
    tmp_output_dir: Path, generator: str
):
    """The top-level done-file must NOT include run.log in its hashes — log
    content changes between runs (timestamps, timings) and would otherwise
    keep invalidating the cache on every rerun."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)
    done = (out / "done").read_text()
    assert "run.log" not in done, (
        f"run.log appears in top-level done-file; it must not be hashed.\n"
        f"done contents:\n{done}"
    )
