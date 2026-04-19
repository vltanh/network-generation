"""Tests for src/_common/state.sh (is_step_done / mark_done).

Each test runs a bash subshell that sources state.sh and exercises the
helpers against files in a pytest-managed tmp directory.  The return code
of is_step_done is surfaced as the subshell's exit status so we can assert
cache hit vs. miss directly.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


STATE_SH = Path(__file__).resolve().parents[2] / "src" / "_common" / "state.sh"


def run_bash(script: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run `script` in a bash subshell with state.sh already sourced."""
    full = f'source "{STATE_SH}"\n{script}'
    return subprocess.run(
        ["bash", "-c", full], cwd=cwd, capture_output=True, text=True
    )


def mark_done(tmp_path: Path, done: str, stage: str, inputs: str, outputs: str) -> None:
    result = run_bash(
        f'mark_done "{done}" "{stage}" "{inputs}" "{outputs}"', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr


def is_step_done(tmp_path: Path, done: str, outputs: str) -> bool:
    result = run_bash(
        f'if is_step_done "{done}" "{outputs}"; then exit 0; else exit 1; fi',
        cwd=tmp_path,
    )
    return result.returncode == 0


def test_is_step_done_false_when_done_file_missing(tmp_path: Path):
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("out")
    assert not is_step_done(tmp_path, "done", "out.txt")


def test_mark_done_roundtrip(tmp_path: Path):
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("out")
    mark_done(tmp_path, "done", "t", "in.txt", "out.txt")
    assert is_step_done(tmp_path, "done", "out.txt")


def test_mutating_output_invalidates_cache(tmp_path: Path):
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("out")
    mark_done(tmp_path, "done", "t", "in.txt", "out.txt")
    (tmp_path / "out.txt").write_text("mutated")
    assert not is_step_done(tmp_path, "done", "out.txt")


def test_mutating_input_invalidates_cache(tmp_path: Path):
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("out")
    mark_done(tmp_path, "done", "t", "in.txt", "out.txt")
    (tmp_path / "in.txt").write_text("mutated")
    assert not is_step_done(tmp_path, "done", "out.txt")


def test_deleting_output_invalidates_cache(tmp_path: Path):
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("out")
    mark_done(tmp_path, "done", "t", "in.txt", "out.txt")
    (tmp_path / "out.txt").unlink()
    assert not is_step_done(tmp_path, "done", "out.txt")


def test_side_files_do_not_invalidate_cache(tmp_path: Path):
    """Regression test for the old mark_done that hashed every file in out_dir."""
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("out")
    (tmp_path / "time_and_err.log").write_text("log before")
    mark_done(tmp_path, "done", "t", "in.txt", "out.txt")
    (tmp_path / "time_and_err.log").write_text("log after")
    (tmp_path / "scratch.tmp").write_text("unrelated")
    assert is_step_done(tmp_path, "done", "out.txt")


def test_multi_file_roundtrip_and_partial_mutation(tmp_path: Path):
    for name in ("in1", "in2", "out1", "out2"):
        (tmp_path / name).write_text(name)
    mark_done(tmp_path, "done", "t", "in1 in2", "out1 out2")
    assert is_step_done(tmp_path, "done", "out1 out2")
    (tmp_path / "in2").write_text("mutated")
    assert not is_step_done(tmp_path, "done", "out1 out2")


def test_zero_byte_output_invalidates_cache(tmp_path: Path):
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("out")
    mark_done(tmp_path, "done", "t", "in.txt", "out.txt")
    (tmp_path / "out.txt").write_text("")
    assert not is_step_done(tmp_path, "done", "out.txt")


def test_mark_done_fails_on_missing_output(tmp_path: Path):
    (tmp_path / "in.txt").write_text("in")
    result = run_bash(
        'mark_done "done" "t" "in.txt" "out.txt"; echo $?',
        cwd=tmp_path,
    )
    # mark_done calls `exit 1` from a sourced function — that exits the whole subshell.
    assert result.returncode != 0
    assert "was not created" in result.stdout + result.stderr


def test_mark_done_fails_on_empty_output(tmp_path: Path):
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("")
    result = run_bash(
        'mark_done "done" "t" "in.txt" "out.txt"', cwd=tmp_path
    )
    assert result.returncode != 0
    assert "empty" in result.stdout + result.stderr


def test_mark_done_leaves_no_tmp_files_on_success(tmp_path: Path):
    """The `done.tmp.$$` stage file used for atomic write must not linger."""
    (tmp_path / "in.txt").write_text("in")
    (tmp_path / "out.txt").write_text("out")
    mark_done(tmp_path, "done", "t", "in.txt", "out.txt")
    stragglers = sorted(p.name for p in tmp_path.iterdir() if ".tmp." in p.name)
    assert not stragglers, f"unexpected tmp files left behind: {stragglers}"


def test_mark_done_fails_atomically_when_sha256sum_fails(tmp_path: Path):
    """If `sha256sum` exits non-zero mid-write (e.g. one of the declared
    inputs is missing), `mark_done` must fail loudly: no partial done-file,
    no leftover .tmp.$$, and a non-zero exit.

    Regression: previous implementation ignored sha256sum's exit code,
    leaving a partial `done` that claimed the stage passed.
    """
    (tmp_path / "out.txt").write_text("out")
    # "nonexistent_input" is declared as an input but doesn't exist.
    result = run_bash(
        'mark_done "done" "t" "nonexistent_input" "out.txt"',
        cwd=tmp_path,
    )
    assert result.returncode != 0, (
        f"mark_done must fail when sha256sum fails.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert not (tmp_path / "done").exists(), "partial done-file must not remain"
    stragglers = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert not stragglers, f"leftover tmp files: {stragglers}"


# ---------------------------------------------------------------------------
# append_stage_log
# ---------------------------------------------------------------------------

def test_append_stage_log_prefixes_each_line(tmp_path: Path):
    (tmp_path / "source.log").write_text("line one\nline two\n")
    result = run_bash(
        'append_stage_log "dest.log" "Stage 1a" "source.log"', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    dest = (tmp_path / "dest.log").read_text()
    assert "[Stage 1a] line one" in dest
    assert "[Stage 1a] line two" in dest
    assert "=== [Stage 1a] source.log ===" in dest


def test_append_stage_log_appends_rather_than_overwrites(tmp_path: Path):
    (tmp_path / "a.log").write_text("first\n")
    (tmp_path / "b.log").write_text("second\n")
    run_bash('append_stage_log "dest.log" "A" "a.log"', cwd=tmp_path)
    run_bash('append_stage_log "dest.log" "B" "b.log"', cwd=tmp_path)
    dest = (tmp_path / "dest.log").read_text()
    assert "[A] first" in dest
    assert "[B] second" in dest
    # A must come before B.
    assert dest.index("[A] first") < dest.index("[B] second")


def test_append_stage_log_silently_skips_missing_source(tmp_path: Path):
    result = run_bash(
        'append_stage_log "dest.log" "Stage X" "nonexistent.log"', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "dest.log").exists()


def test_append_stage_log_creates_dest_parent_dir(tmp_path: Path):
    (tmp_path / "src.log").write_text("x\n")
    result = run_bash(
        'append_stage_log "out/subdir/dest.log" "S" "src.log"', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "subdir" / "dest.log").is_file()
