"""Observation-only smoke tests across all 7 generators.

Share one `fresh_run` per (generator, session). Asserts final-tree
shape, consistency of the top-level `done` file, and stage coverage in
`run.log`.
"""
from __future__ import annotations

import subprocess

import pytest

from .conftest import INP_COM, INP_EDGE


pytestmark = pytest.mark.slow


def test_fresh_run_produces_final_artifacts(fresh_run, gen_spec):
    out, _ = fresh_run
    for name in gen_spec.user_facing:
        assert (out / name).is_file(), f"{gen_spec.name}: missing {name}"


def test_user_facing_tree_holds_only_expected_files(fresh_run, gen_spec):
    out, _ = fresh_run
    surviving = {p.name for p in out.iterdir()}
    extras = surviving - gen_spec.user_facing - {".state"}
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
    user_output_files = {"edge.csv", "com.csv"}
    if gen_spec.has_sources_json:
        user_output_files.add("sources.json")
    for name in user_output_files:
        assert str(out / name) in paths
    assert not [p for p in paths if "/.state/" in p], (
        f"{gen_spec.name}: top-level done references .state/ paths"
    )


def test_run_log_contains_every_stage(fresh_run, gen_spec):
    out, _ = fresh_run
    log_text = (out / "run.log").read_text()
    for stage in gen_spec.stages:
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
