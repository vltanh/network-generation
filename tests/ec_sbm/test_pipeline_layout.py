"""Tests for the Priority #3 refactor: intermediate artifacts live under
`.state/`, the top-level output directory holds only the four user-facing
files, and `.state/` is cleaned up on success while remaining intact for
resume on failure."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_GENERATOR = REPO_ROOT / "run_generator.sh"
EXAMPLES_IN = REPO_ROOT / "examples" / "input"


pytestmark = pytest.mark.slow


def run_generator(
    generator: str, output_dir: Path, extra: list[str] | None = None
) -> subprocess.CompletedProcess:
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
    if extra:
        cmd.extend(extra)
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


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

USER_FACING_FILES = {"edge.csv", "com.csv", "sources.json", "done", "run.log"}


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_user_facing_tree_holds_only_expected_files(
    tmp_output_dir: Path, generator: str
):
    """After a successful run, the top-level output directory must contain
    only the five user-facing files (edge.csv, com.csv, sources.json, done,
    run.log).  No scratch subdirectories like clustered/, outlier/,
    match_degree/, or setup/ should survive."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)

    surviving = {p.name for p in out.iterdir()}
    # .state/ may or may not survive depending on cleanup mode; allow but
    # don't require.  All other entries must be in the user-facing set.
    extras = surviving - USER_FACING_FILES - {".state"}
    assert not extras, (
        f"unexpected artifacts in top-level output dir: {sorted(extras)}\n"
        f"full listing: {sorted(surviving)}"
    )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_scratch_directory_cleaned_up_on_success(
    tmp_output_dir: Path, generator: str
):
    """`.state/` should be removed after a successful run — the intermediate
    artifacts have been consumed, so they no longer need to sit on disk."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)

    assert not (out / ".state").exists(), (
        f".state/ directory should be removed after successful completion; "
        f"found: {sorted(p.name for p in (out / '.state').iterdir()) if (out / '.state').exists() else 'n/a'}"
    )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_keep_state_stage_caches_survive_final_output_corruption(
    tmp_output_dir: Path, generator: str
):
    """Under --keep-state, every stage's done-file must still validate on
    rerun, not just the top-level done.  If the user mutates a final output
    to force a full rerun, the pipeline should enter stage-by-stage checking
    and find every `.state/*/done` still consistent — meaning no stage
    re-executes unnecessarily.

    Regression: stage 1a and stage 3b used to `mv` their outputs into
    OUTPUT_DIR, leaving the hashed `.state/` paths pointing at missing files.
    """
    first = run_generator(generator, tmp_output_dir, extra=["--keep-state"])
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, generator)

    # Corrupt the final com.csv to invalidate the top-level done.
    (out / "com.csv").write_text("node_id,cluster_id\n0,0\n")

    second = run_generator(generator, tmp_output_dir, extra=["--keep-state"])
    assert second.returncode == 0, second.stderr
    # Top-level must have invalidated (state change).
    assert "State change detected" in second.stdout, second.stdout
    # The contract: no stage re-executes.  `Success [Stage ...]` is emitted
    # by mark_done; if any stage actually ran, one would appear.
    assert "Success [Stage" not in second.stdout, (
        f"{generator}: no stage should have re-run under --keep-state after "
        f"mutating com.csv.\nstdout:\n{second.stdout}"
    )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_top_level_short_circuit_wipes_stale_state(
    tmp_output_dir: Path, generator: str
):
    """If the top-level done validates, .state/ is redundant and potentially
    stale (e.g. inherited from an older pipeline version whose stage dones
    were inconsistent).  The dispatcher should wipe .state/ on the top-level
    short-circuit path rather than trusting it."""
    first = run_generator(generator, tmp_output_dir, extra=["--keep-state"])
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, generator)
    assert (out / ".state").is_dir(), "precondition: --keep-state keeps .state/"

    # Seed a stale-looking marker under .state/ and rerun *without*
    # --keep-state.  Because the top-level done is still valid, the
    # pipeline should short-circuit and wipe .state/.
    (out / ".state" / "STALE_MARKER").write_text("leftover\n")

    second = run_generator(generator, tmp_output_dir)
    assert second.returncode == 0, second.stderr
    assert "Skipping entire pipeline" in second.stdout, second.stdout
    assert not (out / ".state").exists(), (
        f"{generator}: .state/ should be wiped on top-level short-circuit"
    )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_keep_state_retains_scratch_directory(
    tmp_output_dir: Path, generator: str
):
    """`--keep-state` opts out of the final `rm -rf .state/`, so users can
    inspect intermediates after a successful run."""
    result = run_generator(generator, tmp_output_dir, extra=["--keep-state"])
    assert result.returncode == 0, result.stderr
    out = run_dir(tmp_output_dir, generator)

    state = out / ".state"
    assert state.is_dir(), (
        f"{generator}: --keep-state should preserve .state/ but it was removed"
    )
    # Stage subdirectories produced during the run should still be present.
    assert (state / "clustered").is_dir(), (
        f"{generator}: .state/clustered/ missing after --keep-state run"
    )
    # Final outputs are still produced — flag only affects cleanup.
    assert (out / "edge.csv").is_file()
    assert (out / "done").is_file()


# ---------------------------------------------------------------------------
# Top-level `done` semantics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_top_level_done_records_original_inputs(
    tmp_output_dir: Path, generator: str
):
    """The top-level done-file must hash the *original* inputs
    (INPUT_EDGELIST, INPUT_CLUSTERING) and the final outputs
    (edge.csv, com.csv, sources.json) — not intermediate .state/ files.
    This lets `is_step_done` short-circuit the entire pipeline on rerun
    after `.state/` has been cleaned up."""
    assert run_generator(generator, tmp_output_dir).returncode == 0
    out = run_dir(tmp_output_dir, generator)

    done = (out / "done").read_text()
    lines = [ln for ln in done.splitlines() if ln.strip()]

    # Each line is "<sha256>  <path>".  Collect the paths.
    recorded_paths = [ln.split(maxsplit=1)[1].strip() for ln in lines]

    # Must include the original inputs (full paths to the example files).
    expected_input_edge = str(
        EXAMPLES_IN / "empirical_networks" / "networks" / "dnc" / "dnc.csv"
    )
    expected_input_com = str(
        EXAMPLES_IN / "reference_clusterings" / "clusterings"
        / "sbm-flat-best+cc" / "dnc" / "com.csv"
    )
    assert expected_input_edge in recorded_paths, (
        f"top-level done should hash INPUT_EDGELIST={expected_input_edge}\n"
        f"recorded paths: {recorded_paths}"
    )
    assert expected_input_com in recorded_paths, (
        f"top-level done should hash INPUT_CLUSTERING={expected_input_com}\n"
        f"recorded paths: {recorded_paths}"
    )

    # Must include the final outputs (paths under `out/`).
    for name in ("edge.csv", "com.csv", "sources.json"):
        assert str(out / name) in recorded_paths, (
            f"top-level done should hash final output {name}\n"
            f"recorded paths: {recorded_paths}"
        )

    # Must NOT reference any path inside .state/.
    stateful = [p for p in recorded_paths if "/.state/" in p]
    assert not stateful, (
        f"top-level done should not reference intermediate .state/ paths; "
        f"found: {stateful}"
    )


@pytest.mark.parametrize("generator", ["ec-sbm-v1", "ec-sbm-v2"])
def test_rerun_after_cleanup_short_circuits_entirely(
    tmp_output_dir: Path, generator: str
):
    """On rerun with identical inputs and `.state/` already cleaned up, the
    pipeline must recognize that the final `done` file is still valid and
    short-circuit — no stages should execute."""
    first = run_generator(generator, tmp_output_dir)
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, generator)
    assert not (out / ".state").exists(), (
        "precondition: .state/ should already be cleaned up after first run"
    )

    second = run_generator(generator, tmp_output_dir)
    assert second.returncode == 0, second.stderr

    # No "Success [Stage ...]" markers means nothing re-ran.
    assert "Success [Stage" not in second.stdout, (
        f"no stage should have re-run after .state/ cleanup; "
        f"found successes in stdout:\n{second.stdout}"
    )


