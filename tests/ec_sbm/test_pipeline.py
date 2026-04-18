"""End-to-end tests for the ec-sbm v1 and v2 pipelines.

Marked `slow` — these run the full generator on the bundled `dnc` example
and take tens of seconds each.  Run with:  pytest -m slow tests/ec_sbm/
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


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_fresh_run_produces_final_artifacts(tmp_output_dir: Path, generator: str):
    result = run_generator(generator, tmp_output_dir)
    assert result.returncode == 0, (
        f"pipeline failed: stdout={result.stdout}\nstderr={result.stderr}"
    )
    out = run_dir(tmp_output_dir, generator)
    assert (out / "edge.csv").is_file()
    assert (out / "com.csv").is_file()
    assert (out / "sources.json").is_file()
    assert (out / "done").is_file()


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_rerun_short_circuits_entire_pipeline(
    tmp_output_dir: Path, generator: str
):
    """With the top-level done-file scheme, a rerun against identical inputs
    should short-circuit at the very top — no stage banners at all."""
    first = run_generator(generator, tmp_output_dir)
    assert first.returncode == 0, first.stderr

    second = run_generator(generator, tmp_output_dir)
    assert second.returncode == 0, second.stderr

    assert "Skipping entire pipeline" in second.stdout, (
        f"expected top-level short-circuit on rerun.\n"
        f"stdout:\n{second.stdout}"
    )
    # No stage should have executed.
    assert "Success [Stage" not in second.stdout, (
        f"no individual stage should have run on rerun.\n"
        f"stdout:\n{second.stdout}"
    )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_done_file_consistent_after_completion(
    tmp_output_dir: Path, generator: str
):
    """The top-level done-file's recorded sha256 hashes must match the
    on-disk files after a successful run."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)

    done = out / "done"
    assert done.is_file(), "top-level done-file missing"

    result = subprocess.run(
        ["sha256sum", "-c", "--status", "done"],
        cwd=out,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"done file inconsistent with on-disk files.\n"
        f"contents:\n{done.read_text()}\n"
        f"sha256sum -c stderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_final_output_corruption_triggers_full_rerun(
    tmp_output_dir: Path, generator: str
):
    """If a final artifact is corrupted, rerunning must detect the state
    mismatch and re-execute the full pipeline (since `.state/` intermediates
    have been cleaned up on the previous success)."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)

    # Corrupt the final edge.csv.
    (out / "edge.csv").write_text("source,target\n0,1\n")

    second = run_generator(generator, tmp_output_dir)
    assert second.returncode == 0, second.stderr

    assert "State change detected" in second.stdout, (
        f"expected state.sh to notice the mutated edge.csv.\n"
        f"stdout:\n{second.stdout}"
    )
    # All stages re-run because .state/ was cleaned after the first success.
    for stage in ("1a", "1b", "1c", "2a", "2b", "3a", "3b"):
        assert f"Success [Stage {stage}" in second.stdout, (
            f"Stage {stage} should have re-run after final-output corruption.\n"
            f"stdout:\n{second.stdout}"
        )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_input_change_invalidates_pipeline(
    tmp_path: Path, generator: str
):
    """When an input file changes between runs, the top-level done-file must
    fail its hash check and the whole pipeline must re-run."""
    # Copy the reference inputs into tmp so we can mutate one between runs.
    edge_src = EXAMPLES_IN / "empirical_networks" / "networks" / "dnc" / "dnc.csv"
    com_src = (
        EXAMPLES_IN
        / "reference_clusterings"
        / "clusterings"
        / "sbm-flat-best+cc"
        / "dnc"
        / "com.csv"
    )
    edge_local = tmp_path / "edge.csv"
    com_local = tmp_path / "com.csv"
    edge_local.write_bytes(edge_src.read_bytes())
    com_local.write_bytes(com_src.read_bytes())

    out_root = tmp_path / "synthetic_networks"
    out_root.mkdir()

    def run_with_local() -> subprocess.CompletedProcess:
        cmd = [
            str(RUN_GENERATOR),
            "--generator", generator,
            "--run-id", "0",
            "--input-edgelist", str(edge_local),
            "--input-clustering", str(com_local),
            "--output-dir", str(out_root),
            "--network", "dnc",
            "--clustering-id", "sbm-flat-best+cc",
        ]
        env = os.environ.copy()
        env["PATH"] = f"/u/vltanh/miniconda3/envs/nw/bin:{env.get('PATH', '')}"
        env["OMP_NUM_THREADS"] = "1"
        return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)

    assert run_with_local().returncode == 0

    # Mutate the clustering input.
    com_local.write_text("node_id,cluster_id\n0,0\n1,0\n")

    second = run_with_local()
    assert second.returncode == 0, second.stderr
    assert "State change detected" in second.stdout, (
        f"expected state.sh to notice the mutated clustering input.\n"
        f"stdout:\n{second.stdout}"
    )
    # Full pipeline must have re-run.
    assert "Success [Stage 1a" in second.stdout, second.stdout
