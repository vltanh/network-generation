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
# is_state_tree_consistent
# ---------------------------------------------------------------------------

def is_state_tree_consistent(tmp_path: Path, state_dir: str) -> bool:
    result = run_bash(
        f'if is_state_tree_consistent "{state_dir}"; then exit 0; else exit 1; fi',
        cwd=tmp_path,
    )
    return result.returncode == 0


def test_state_tree_consistent_returns_false_when_missing(tmp_path: Path):
    assert not is_state_tree_consistent(tmp_path, "absent_state")


def test_state_tree_consistent_returns_false_when_no_done_files(tmp_path: Path):
    (tmp_path / "state").mkdir()
    assert not is_state_tree_consistent(tmp_path, "state")


def test_state_tree_consistent_returns_true_when_all_done_files_verify(tmp_path: Path):
    (tmp_path / "state" / "a").mkdir(parents=True)
    (tmp_path / "state" / "a" / "in.txt").write_text("in")
    (tmp_path / "state" / "a" / "out.txt").write_text("out")
    mark_done(
        tmp_path, "state/a/done", "t",
        "state/a/in.txt", "state/a/out.txt",
    )
    (tmp_path / "state" / "b").mkdir(parents=True)
    (tmp_path / "state" / "b" / "in.txt").write_text("in2")
    (tmp_path / "state" / "b" / "out.txt").write_text("out2")
    mark_done(
        tmp_path, "state/b/done", "t",
        "state/b/in.txt", "state/b/out.txt",
    )
    assert is_state_tree_consistent(tmp_path, "state")


def test_state_tree_consistent_returns_false_when_hashed_file_missing(tmp_path: Path):
    (tmp_path / "state" / "a").mkdir(parents=True)
    (tmp_path / "state" / "a" / "in.txt").write_text("in")
    (tmp_path / "state" / "a" / "out.txt").write_text("out")
    mark_done(
        tmp_path, "state/a/done", "t",
        "state/a/in.txt", "state/a/out.txt",
    )
    (tmp_path / "state" / "a" / "out.txt").unlink()
    assert not is_state_tree_consistent(tmp_path, "state")


def test_state_tree_consistent_returns_false_when_any_done_invalid(tmp_path: Path):
    """Rule: a single inconsistent stage-done invalidates the whole tree."""
    (tmp_path / "state" / "ok").mkdir(parents=True)
    (tmp_path / "state" / "ok" / "in.txt").write_text("in")
    (tmp_path / "state" / "ok" / "out.txt").write_text("out")
    mark_done(
        tmp_path, "state/ok/done", "t",
        "state/ok/in.txt", "state/ok/out.txt",
    )
    (tmp_path / "state" / "bad").mkdir(parents=True)
    (tmp_path / "state" / "bad" / "in.txt").write_text("in")
    (tmp_path / "state" / "bad" / "out.txt").write_text("out")
    mark_done(
        tmp_path, "state/bad/done", "t",
        "state/bad/in.txt", "state/bad/out.txt",
    )
    (tmp_path / "state" / "bad" / "out.txt").write_text("mutated")
    assert not is_state_tree_consistent(tmp_path, "state")


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


# ---------------------------------------------------------------------------
# Logging journal: log_invocation_header, run_stage, note_stage_skipped
# ---------------------------------------------------------------------------

def _count_matches(text: str, prefix: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith(prefix))


def test_log_invocation_header_appends_rather_than_truncating(tmp_path: Path):
    (tmp_path / "run.log").write_text("== pre-existing content ==\n")
    result = run_bash(
        'log_invocation_header "run.log" "1" "0"', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "run.log").read_text()
    assert "pre-existing content" in text, "header must not truncate run.log"
    assert "=== Invocation " in text
    assert "seed=1" in text and "keep_state=0" in text


def test_log_invocation_header_creates_parent_dir(tmp_path: Path):
    result = run_bash(
        'log_invocation_header "out/subdir/run.log" "42" "1"', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "subdir" / "run.log").is_file()


def test_log_invocation_header_two_calls_produce_two_entries(tmp_path: Path):
    for _ in range(2):
        result = run_bash(
            'log_invocation_header "run.log" "1" "0"', cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
    text = (tmp_path / "run.log").read_text()
    assert _count_matches(text, "=== Invocation ") == 2, (
        f"expected 2 invocation headers, got:\n{text}"
    )


def test_run_stage_appends_executed_block_with_exit_footer(tmp_path: Path):
    result = run_bash(
        'TIMEOUT=10 run_stage "time_and_err.log" true', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "time_and_err.log").read_text()
    assert "| EXECUTED ===" in text
    assert "=== exit=0 ===" in text


def test_run_stage_captures_nonzero_exit(tmp_path: Path):
    result = run_bash(
        'TIMEOUT=10 run_stage "time_and_err.log" false', cwd=tmp_path
    )
    assert result.returncode != 0
    text = (tmp_path / "time_and_err.log").read_text()
    assert "=== exit=1 ===" in text


def test_run_stage_captures_timeout_exit_124(tmp_path: Path):
    """Timeout (exit 124) must be in the footer even though /usr/bin/time -v
    only prints its own "Exit status:" line for clean exits."""
    result = run_bash(
        'TIMEOUT=1 run_stage "time_and_err.log" sleep 5', cwd=tmp_path
    )
    # Subshell returncode is run_stage's rc, which is `timeout`'s exit = 124.
    assert result.returncode == 124, (
        f"expected timeout exit 124, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    text = (tmp_path / "time_and_err.log").read_text()
    assert "=== exit=124 ===" in text, (
        f"timeout exit code must be recorded in footer; log:\n{text}"
    )


def test_run_stage_appends_rather_than_overwrites(tmp_path: Path):
    for _ in range(2):
        result = run_bash(
            'TIMEOUT=10 run_stage "time_and_err.log" true', cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
    text = (tmp_path / "time_and_err.log").read_text()
    assert _count_matches(text, "=== ") >= 4, (
        f"two runs should produce ≥4 '===' delimiters (2 headers + 2 footers); "
        f"log:\n{text}"
    )
    assert text.count("| EXECUTED ===") == 2


def test_run_stage_creates_parent_dir(tmp_path: Path):
    result = run_bash(
        'TIMEOUT=10 run_stage "out/subdir/time_and_err.log" true', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "subdir" / "time_and_err.log").is_file()


def test_note_stage_skipped_appends_one_line(tmp_path: Path):
    result = run_bash(
        'note_stage_skipped "time_and_err.log"', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "time_and_err.log").read_text()
    assert "| SKIPPED (cache hit) ===" in text


def test_note_stage_skipped_creates_parent_dir(tmp_path: Path):
    result = run_bash(
        'note_stage_skipped "out/subdir/time_and_err.log"', cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "subdir" / "time_and_err.log").is_file()


def test_stage_journal_executed_then_skipped_sequence(tmp_path: Path):
    """First call executes a stage; second is a cache-hit skip.  The stage
    log must hold exactly 1 EXECUTED block + 1 SKIPPED line — the
    invocation-count assertion from the logging-journal test plan."""
    assert run_bash(
        'TIMEOUT=10 run_stage "time_and_err.log" true', cwd=tmp_path
    ).returncode == 0
    assert run_bash(
        'note_stage_skipped "time_and_err.log"', cwd=tmp_path
    ).returncode == 0
    text = (tmp_path / "time_and_err.log").read_text()
    assert text.count("| EXECUTED ===") == 1
    assert text.count("| SKIPPED (cache hit) ===") == 1


def test_pipeline_level_invocation_journal_across_two_runs(tmp_path: Path):
    """Simulate a cache-hit-then-execute pattern matching the memory test
    plan: run a fake pipeline twice with identical behavior — each
    invocation logs a header, the first executes stage 1, the second
    skips it.  run.log must hold 2 headers; stage log must hold 1
    EXECUTED + 1 SKIPPED."""
    fake_pipeline = tmp_path / "fake_pipeline.sh"
    fake_pipeline.write_text(
        f"""#!/bin/bash
set -u
source "{STATE_SH}"

OUTPUT_DIR="$1"
SEED="$2"
KEEP_STATE=0
TIMEOUT=10
mkdir -p "${{OUTPUT_DIR}}"

FINAL_LOG="${{OUTPUT_DIR}}/run.log"
log_invocation_header "${{FINAL_LOG}}" "${{SEED}}" "${{KEEP_STATE}}"

STAGE_DIR="${{OUTPUT_DIR}}/.state/stage1"
mkdir -p "${{STAGE_DIR}}"
IN_FILE="${{OUTPUT_DIR}}/input.txt"
OUT_FILE="${{STAGE_DIR}}/out.txt"
[ -f "${{IN_FILE}}" ] || echo "payload" > "${{IN_FILE}}"

if ! is_step_done "${{STAGE_DIR}}/done" "${{OUT_FILE}}"; then
    run_stage "${{STAGE_DIR}}/time_and_err.log" \\
        cp "${{IN_FILE}}" "${{OUT_FILE}}"
    mark_done "${{STAGE_DIR}}/done" "stage1" "${{IN_FILE}}" "${{OUT_FILE}}"
else
    note_stage_skipped "${{STAGE_DIR}}/time_and_err.log"
fi
"""
    )
    fake_pipeline.chmod(0o755)

    out = tmp_path / "out"
    for _ in range(2):
        proc = subprocess.run(
            ["bash", str(fake_pipeline), str(out), "1"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr

    run_log = (out / "run.log").read_text()
    assert _count_matches(run_log, "=== Invocation ") == 2, (
        f"run.log must hold 2 invocation headers after 2 runs:\n{run_log}"
    )

    stage_log = (out / ".state" / "stage1" / "time_and_err.log").read_text()
    assert stage_log.count("| EXECUTED ===") == 1, (
        f"stage log must have exactly 1 EXECUTED block:\n{stage_log}"
    )
    assert stage_log.count("| SKIPPED (cache hit) ===") == 1, (
        f"stage log must have exactly 1 SKIPPED line:\n{stage_log}"
    )


def test_pipeline_keep_state_mutate_stage2_input_then_rerun(tmp_path: Path):
    """Memory test plan: with --keep-state equivalent, mutate a stage-2
    input between runs.  Stage 1's log gets SKIPPED (cache hit), stage 2's
    log gets a second EXECUTED block."""
    fake_pipeline = tmp_path / "fake_pipeline.sh"
    fake_pipeline.write_text(
        f"""#!/bin/bash
set -u
source "{STATE_SH}"

OUTPUT_DIR="$1"
TIMEOUT=10
mkdir -p "${{OUTPUT_DIR}}"

STATE_DIR="${{OUTPUT_DIR}}/.state"
S1_DIR="${{STATE_DIR}}/stage1"
S2_DIR="${{STATE_DIR}}/stage2"
mkdir -p "${{S1_DIR}}" "${{S2_DIR}}"

IN_1="${{OUTPUT_DIR}}/in1.txt"
OUT_1="${{S1_DIR}}/out1.txt"
OUT_2="${{S2_DIR}}/out2.txt"
[ -f "${{IN_1}}" ] || echo "stage1-input" > "${{IN_1}}"

# Stage 1: reads IN_1, writes OUT_1.
if ! is_step_done "${{S1_DIR}}/done" "${{OUT_1}}"; then
    run_stage "${{S1_DIR}}/time_and_err.log" cp "${{IN_1}}" "${{OUT_1}}"
    mark_done "${{S1_DIR}}/done" "stage1" "${{IN_1}}" "${{OUT_1}}"
else
    note_stage_skipped "${{S1_DIR}}/time_and_err.log"
fi

# Stage 2: reads OUT_1 + an external stage-2 input, writes OUT_2.
IN_2_EXTRA="${{OUTPUT_DIR}}/in2.txt"
if ! is_step_done "${{S2_DIR}}/done" "${{OUT_2}}"; then
    run_stage "${{S2_DIR}}/time_and_err.log" cat "${{OUT_1}}" "${{IN_2_EXTRA}}"
    # Stage-2 output is just a copy of stage-1's output; but we hash
    # IN_2_EXTRA as a declared input so mutating it invalidates stage 2.
    cp "${{OUT_1}}" "${{OUT_2}}"
    mark_done "${{S2_DIR}}/done" "stage2" "${{OUT_1}} ${{IN_2_EXTRA}}" "${{OUT_2}}"
else
    note_stage_skipped "${{S2_DIR}}/time_and_err.log"
fi
"""
    )
    fake_pipeline.chmod(0o755)

    out = tmp_path / "out"
    (tmp_path).mkdir(exist_ok=True)
    # Seed stage-2 extra input before the first run.
    out.mkdir()
    (out / "in2.txt").write_text("v1\n")

    first = subprocess.run(
        ["bash", str(fake_pipeline), str(out)], capture_output=True, text=True
    )
    assert first.returncode == 0, first.stderr

    # Mutate stage-2's declared input.
    (out / "in2.txt").write_text("v2\n")

    second = subprocess.run(
        ["bash", str(fake_pipeline), str(out)], capture_output=True, text=True
    )
    assert second.returncode == 0, second.stderr

    s1_log = (out / ".state" / "stage1" / "time_and_err.log").read_text()
    s2_log = (out / ".state" / "stage2" / "time_and_err.log").read_text()

    assert s1_log.count("| EXECUTED ===") == 1, f"stage1 must only execute once:\n{s1_log}"
    assert s1_log.count("| SKIPPED (cache hit) ===") == 1, (
        f"stage1 cache must hold across input-2 mutation:\n{s1_log}"
    )
    assert s2_log.count("| EXECUTED ===") == 2, (
        f"stage2 must re-execute after its declared input mutated:\n{s2_log}"
    )
