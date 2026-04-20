"""End-to-end tests for the 5 simple-generator pipelines.

Two groups:
  * Pure-observation tests consume the `fresh_run` session fixture — one
    pipeline invocation per generator is amortized across many assertions.
  * Mutation/rerun tests take `tmp_output_dir` and invoke `run_generator`
    directly since they need isolated state.

All tests are `slow`.  Run with:  pytest -m slow tests/simple_gens/
"""
from __future__ import annotations

import subprocess

import pytest

from .conftest import (
    INP_COM,
    INP_EDGE,
    run_dir,
    run_generator,
)


pytestmark = pytest.mark.slow


USER_FACING_FILES = {"edge.csv", "com.csv", "done", "run.log"}
SIMPLE_STAGES = ("1 (profile)", "2 (gen)")


# ---------------------------------------------------------------------------
# Observation-only — share one fresh run per generator
# ---------------------------------------------------------------------------

def test_fresh_run_produces_final_artifacts(fresh_run, gen_spec):
    out, _ = fresh_run
    for name in USER_FACING_FILES:
        assert (out / name).is_file(), f"{gen_spec.name}: missing {name}"


def test_user_facing_tree_holds_only_expected_files(fresh_run, gen_spec):
    out, _ = fresh_run
    surviving = {p.name for p in out.iterdir()}
    extras = surviving - USER_FACING_FILES - {".state"}
    assert not extras, (
        f"{gen_spec.name}: unexpected artifacts: {sorted(extras)}\n"
        f"full listing: {sorted(surviving)}"
    )


def test_scratch_directory_cleaned_up_on_success(fresh_run, gen_spec):
    out, _ = fresh_run
    assert not (out / ".state").exists(), (
        f"{gen_spec.name}: .state/ should be removed after success"
    )


def test_done_file_consistent_after_completion(fresh_run, gen_spec):
    out, _ = fresh_run
    result = subprocess.run(
        ["sha256sum", "-c", "--status", "done"],
        cwd=out, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"{gen_spec.name}: done inconsistent.\n"
        f"contents:\n{(out / 'done').read_text()}\nstderr:\n{result.stderr}"
    )


def test_top_level_done_records_original_inputs(fresh_run, gen_spec):
    out, _ = fresh_run
    paths = [
        ln.split(maxsplit=1)[1].strip()
        for ln in (out / "done").read_text().splitlines()
        if ln.strip()
    ]
    assert str(INP_EDGE) in paths
    assert str(INP_COM) in paths
    for name in ("edge.csv", "com.csv"):
        assert str(out / name) in paths
    assert not [p for p in paths if "/.state/" in p], (
        f"{gen_spec.name}: top-level done references .state/ paths"
    )


def test_run_log_contains_every_stage(fresh_run, gen_spec):
    out, _ = fresh_run
    log_text = (out / "run.log").read_text()
    for stage in SIMPLE_STAGES:
        assert f"[Stage {stage}]" in log_text, (
            f"{gen_spec.name}: run.log missing [Stage {stage}]\n"
            f"first 500 chars:\n{log_text[:500]}"
        )


def test_no_per_stage_log_files_in_user_tree(fresh_run, gen_spec):
    out, _ = fresh_run
    surviving = [p for p in out.rglob("*.log") if p != out / "run.log"]
    assert not surviving, (
        f"{gen_spec.name}: stray log files: "
        f"{[str(p.relative_to(out)) for p in surviving]}"
    )
    stray = list(out.rglob("time_and_err.log"))
    assert not stray, (
        f"{gen_spec.name}: legacy time_and_err.log present: "
        f"{[str(p.relative_to(out)) for p in stray]}"
    )


def test_run_log_not_hashed_in_top_level_done(fresh_run, gen_spec):
    out, _ = fresh_run
    assert "run.log" not in (out / "done").read_text()


# ---------------------------------------------------------------------------
# Mutation / rerun — each test owns its output dir
# ---------------------------------------------------------------------------

def test_rerun_short_circuits_entire_pipeline(
    gen_spec, tmp_output_dir, subprocess_env
):
    first = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert first.returncode == 0, first.stderr

    second = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert second.returncode == 0, second.stderr
    assert "Skipping entire pipeline" in second.stdout, second.stdout
    assert "Success [Stage" not in second.stdout, second.stdout


def test_final_output_corruption_triggers_full_rerun(
    gen_spec, tmp_output_dir, subprocess_env
):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)

    (out / "edge.csv").write_text("source,target\n0,1\n")

    second = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert second.returncode == 0, second.stderr
    assert "State change detected" in second.stdout
    for name in ("edge.csv", "com.csv", "done"):
        assert (out / name).is_file()
    check = subprocess.run(
        ["sha256sum", "-c", "--status", "done"],
        cwd=out, capture_output=True, text=True,
    )
    assert check.returncode == 0, check.stderr


def test_input_change_invalidates_pipeline(gen_spec, tmp_path, subprocess_env):
    edge_local = tmp_path / "edge.csv"
    com_local = tmp_path / "com.csv"
    edge_local.write_bytes(INP_EDGE.read_bytes())
    com_local.write_bytes(INP_COM.read_bytes())

    out_root = tmp_path / "synthetic_networks"
    out_root.mkdir()

    first = run_generator(
        gen_spec, out_root, subprocess_env,
        inp_edge=edge_local, inp_com=com_local,
    )
    assert first.returncode == 0, first.stderr

    com_local.write_text("node_id,cluster_id\n0,0\n1,0\n")

    second = run_generator(
        gen_spec, out_root, subprocess_env,
        inp_edge=edge_local, inp_com=com_local,
    )
    assert "State change detected" in second.stdout


def test_keep_state_retains_scratch_directory(
    gen_spec, tmp_output_dir, subprocess_env
):
    result = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert result.returncode == 0, result.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)
    state = out / ".state"
    assert state.is_dir()
    assert (state / "setup").is_dir()
    assert (out / "edge.csv").is_file()
    assert (out / "done").is_file()


def test_keep_state_stage2_cache_survives_final_output_corruption(
    gen_spec, tmp_output_dir, subprocess_env
):
    first = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)

    (out / "edge.csv").write_text("source,target\n0,1\n")

    second = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert second.returncode == 0, second.stderr
    assert "State change detected" in second.stdout
    assert "Success [Stage" not in second.stdout, second.stdout


def test_top_level_short_circuit_wipes_stale_state(
    gen_spec, tmp_output_dir, subprocess_env
):
    first = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)
    assert (out / ".state").is_dir()

    (out / ".state" / "STALE_MARKER").write_text("leftover\n")

    second = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert second.returncode == 0, second.stderr
    assert "Skipping entire pipeline" in second.stdout
    assert not (out / ".state").exists()


def test_keep_state_rerun_preserves_consistent_state(
    gen_spec, tmp_output_dir, subprocess_env
):
    first = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)
    assert (out / ".state").is_dir()

    second = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert second.returncode == 0, second.stderr
    assert "Skipping entire pipeline" in second.stdout
    assert (out / ".state").is_dir(), (
        f"{gen_spec.name}: --keep-state rerun must preserve consistent .state/"
    )
    assert (out / ".state" / "setup" / "done").is_file()


def test_keep_state_rerun_regenerates_when_state_inconsistent(
    gen_spec, tmp_output_dir, subprocess_env
):
    first = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)

    stage2_edge = out / ".state" / "gen" / "edge.csv"
    assert stage2_edge.is_file()
    stage2_edge.unlink()

    second = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert second.returncode == 0, second.stderr
    assert "Top-level done valid but .state/ is inconsistent" in second.stdout
    assert (out / ".state").is_dir()
    assert (out / ".state" / "gen" / "edge.csv").is_file()
    assert (out / ".state" / "gen" / "done").is_file()
    assert (out / "edge.csv").is_file()
    assert (out / "done").is_file()

    third = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert third.returncode == 0, third.stderr
    assert "Skipping entire pipeline" in third.stdout
    assert "inconsistent" not in third.stdout


def test_rerun_without_keep_state_regenerates_when_state_inconsistent(
    gen_spec, tmp_output_dir, subprocess_env
):
    """An inconsistent .state/ is a signal something went wrong — even
    without --keep-state the pipeline must regenerate rather than trust
    the top-level done and silently wipe the broken cache."""
    first = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)

    stage2_edge = out / ".state" / "gen" / "edge.csv"
    assert stage2_edge.is_file()
    stage2_edge.unlink()

    second = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert second.returncode == 0, second.stderr
    assert "Top-level done valid but .state/ is inconsistent" in second.stdout
    assert not (out / ".state").exists()
    assert (out / "edge.csv").is_file()
    assert (out / "done").is_file()
