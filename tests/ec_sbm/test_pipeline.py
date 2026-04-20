"""End-to-end tests for ec-sbm v1 and v2.

Two groups:
  * Pure-observation tests consume the `fresh_run` session fixture.
  * Mutation/rerun tests use `tmp_output_dir` + explicit `run_generator`.

All tests are `slow`.  Run with:  pytest -m slow tests/ec_sbm/
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


USER_FACING_FILES = {"edge.csv", "com.csv", "sources.json", "done", "run.log"}
EC_SBM_STAGES = (
    "1 (profile)",
    "2 (gen_clustered)",
    "3a (gen_outlier)",
    "3b (gen_outlier/combine)",
    "4a (match_degree)",
    "4b (match_degree/combine)",
)


# ---------------------------------------------------------------------------
# Observation-only — share one fresh run per generator
# ---------------------------------------------------------------------------

def test_fresh_run_produces_final_artifacts(fresh_run, generator):
    out, _ = fresh_run
    for name in ("edge.csv", "com.csv", "sources.json", "done", "run.log"):
        assert (out / name).is_file(), f"{generator}: missing {name}"


def test_user_facing_tree_holds_only_expected_files(fresh_run, generator):
    out, _ = fresh_run
    surviving = {p.name for p in out.iterdir()}
    extras = surviving - USER_FACING_FILES - {".state"}
    assert not extras, (
        f"{generator}: unexpected artifacts: {sorted(extras)}\n"
        f"full listing: {sorted(surviving)}"
    )


def test_scratch_directory_cleaned_up_on_success(fresh_run, generator):
    out, _ = fresh_run
    assert not (out / ".state").exists(), (
        f"{generator}: .state/ should be removed after success"
    )


def test_done_file_consistent_after_completion(fresh_run, generator):
    out, _ = fresh_run
    result = subprocess.run(
        ["sha256sum", "-c", "--status", "done"],
        cwd=out, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"{generator}: done inconsistent.\n"
        f"contents:\n{(out / 'done').read_text()}\nstderr:\n{result.stderr}"
    )


def test_top_level_done_records_original_inputs(fresh_run, generator):
    out, _ = fresh_run
    paths = [
        ln.split(maxsplit=1)[1].strip()
        for ln in (out / "done").read_text().splitlines()
        if ln.strip()
    ]
    assert str(INP_EDGE) in paths
    assert str(INP_COM) in paths
    for name in ("edge.csv", "com.csv", "sources.json"):
        assert str(out / name) in paths
    assert not [p for p in paths if "/.state/" in p]


def test_top_level_run_log_exists(fresh_run, generator):
    out, _ = fresh_run
    log = out / "run.log"
    assert log.is_file() and log.stat().st_size > 0


def test_run_log_contains_every_stage(fresh_run, generator):
    out, _ = fresh_run
    log_text = (out / "run.log").read_text()
    for stage in EC_SBM_STAGES:
        assert f"[Stage {stage}]" in log_text, (
            f"{generator}: run.log missing [Stage {stage}]\n"
            f"first 500 chars:\n{log_text[:500]}"
        )


def test_no_per_stage_log_files_in_user_tree(fresh_run, generator):
    out, _ = fresh_run
    surviving = [p for p in out.rglob("*.log") if p != out / "run.log"]
    assert not surviving, (
        f"{generator}: stray log files: "
        f"{[str(p.relative_to(out)) for p in surviving]}"
    )
    stray = list(out.rglob("time_and_err.log"))
    assert not stray, (
        f"{generator}: legacy time_and_err.log present: "
        f"{[str(p.relative_to(out)) for p in stray]}"
    )


def test_run_log_not_hashed_in_top_level_done(fresh_run, generator):
    out, _ = fresh_run
    assert "run.log" not in (out / "done").read_text()


# ---------------------------------------------------------------------------
# Mutation / rerun — each test owns its output dir
# ---------------------------------------------------------------------------

def test_rerun_short_circuits_entire_pipeline(tmp_output_dir, generator):
    first = run_generator(generator, tmp_output_dir)
    assert first.returncode == 0, first.stderr

    second = run_generator(generator, tmp_output_dir)
    assert second.returncode == 0, second.stderr
    assert "Skipping entire pipeline" in second.stdout, second.stdout
    assert "Success [Stage" not in second.stdout, second.stdout


def test_final_output_corruption_triggers_full_rerun(tmp_output_dir, generator):
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)

    (out / "edge.csv").write_text("source,target\n0,1\n")

    second = run_generator(generator, tmp_output_dir)
    assert second.returncode == 0, second.stderr
    assert "State change detected" in second.stdout
    for name in ("edge.csv", "com.csv", "sources.json", "done"):
        assert (out / name).is_file()
    check = subprocess.run(
        ["sha256sum", "-c", "--status", "done"],
        cwd=out, capture_output=True, text=True,
    )
    assert check.returncode == 0, check.stderr


def test_input_change_invalidates_pipeline(tmp_path, generator):
    edge_local = tmp_path / "edge.csv"
    com_local = tmp_path / "com.csv"
    edge_local.write_bytes(INP_EDGE.read_bytes())
    com_local.write_bytes(INP_COM.read_bytes())

    out_root = tmp_path / "synthetic_networks"
    out_root.mkdir()

    first = run_generator(
        generator, out_root, inp_edge=edge_local, inp_com=com_local
    )
    assert first.returncode == 0, first.stderr

    com_local.write_text("node_id,cluster_id\n0,0\n1,0\n")

    second = run_generator(
        generator, out_root, inp_edge=edge_local, inp_com=com_local
    )
    assert second.returncode == 0, second.stderr
    assert "State change detected" in second.stdout
    assert "Success [Stage 1 (profile)" in second.stdout, second.stdout


def test_keep_state_retains_scratch_directory(tmp_output_dir, generator):
    result = run_generator(generator, tmp_output_dir, extra=["--keep-state"])
    assert result.returncode == 0, result.stderr
    out = run_dir(tmp_output_dir, generator)
    state = out / ".state"
    assert state.is_dir()
    assert (state / "profile").is_dir()
    assert (state / "gen_clustered").is_dir()
    assert (out / "edge.csv").is_file()
    assert (out / "done").is_file()


def test_keep_state_stage_caches_survive_final_output_corruption(
    tmp_output_dir, generator
):
    """Regression: the profile stage and stage 4b used to `mv` outputs into
    OUTPUT_DIR, leaving hashed `.state/` paths pointing at missing files."""
    first = run_generator(generator, tmp_output_dir, extra=["--keep-state"])
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, generator)

    (out / "com.csv").write_text("node_id,cluster_id\n0,0\n")

    second = run_generator(generator, tmp_output_dir, extra=["--keep-state"])
    assert second.returncode == 0, second.stderr
    assert "State change detected" in second.stdout
    assert "Success [Stage" not in second.stdout, (
        f"{generator}: no stage should have re-run.\nstdout:\n{second.stdout}"
    )


def test_top_level_short_circuit_wipes_stale_state(tmp_output_dir, generator):
    first = run_generator(generator, tmp_output_dir, extra=["--keep-state"])
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, generator)
    assert (out / ".state").is_dir()

    (out / ".state" / "STALE_MARKER").write_text("leftover\n")

    second = run_generator(generator, tmp_output_dir)
    assert second.returncode == 0, second.stderr
    assert "Skipping entire pipeline" in second.stdout
    assert not (out / ".state").exists()
