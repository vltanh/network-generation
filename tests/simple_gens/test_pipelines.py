"""End-to-end tests for the 5 simple-generator pipelines after P5.

Covers the same cache/layout/log contract the ec-sbm pipelines got in
P2–P4:
  * fresh run produces edge.csv + com.csv + done + run.log only
  * rerun short-circuits at the top level
  * corrupting a final output forces a full re-run
  * changing an input invalidates the cache
  * .state/ is cleaned up on success
  * no stray per-stage logs survive

All tests are `slow`.  Run with:  pytest -m slow tests/simple_gens/
"""
from __future__ import annotations

import os
import subprocess
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


pytestmark = pytest.mark.slow


USER_FACING_FILES = {"edge.csv", "com.csv", "done", "run.log"}
SIMPLE_STAGES = ("1 (profile)", "2 (gen)")


def run_generator(
    gen_spec, output_dir: Path, env: dict, inp_edge: Path = INP_EDGE, inp_com: Path = INP_COM
) -> subprocess.CompletedProcess:
    cmd = [
        str(RUN_GENERATOR),
        "--generator", gen_spec.name,
        "--run-id", "0",
        "--input-edgelist", str(inp_edge),
        "--input-clustering", str(inp_com),
        "--output-dir", str(output_dir),
        "--network", "dnc",
        "--clustering-id", "sbm-flat-best+cc",
        "--seed", "0",
        "--n-threads", "1",
    ]
    for flag, val in gen_spec.binary_env.items():
        cmd.extend([flag, val])
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def run_dir(output_root: Path, gen_name: str) -> Path:
    return output_root / "networks" / gen_name / "sbm-flat-best+cc" / "dnc" / "0"


# ---------------------------------------------------------------------------
# Fresh run
# ---------------------------------------------------------------------------

def test_fresh_run_produces_final_artifacts(gen_spec, tmp_output_dir, subprocess_env):
    result = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert result.returncode == 0, (
        f"{gen_spec.name} pipeline failed:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    out = run_dir(tmp_output_dir, gen_spec.name)
    for name in ("edge.csv", "com.csv", "done", "run.log"):
        assert (out / name).is_file(), f"{gen_spec.name}: missing {name}"


def test_user_facing_tree_holds_only_expected_files(
    gen_spec, tmp_output_dir, subprocess_env
):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)

    surviving = {p.name for p in out.iterdir()}
    extras = surviving - USER_FACING_FILES - {".state"}
    assert not extras, (
        f"{gen_spec.name}: unexpected artifacts in top-level output dir: "
        f"{sorted(extras)}\nfull listing: {sorted(surviving)}"
    )


def test_scratch_directory_cleaned_up_on_success(
    gen_spec, tmp_output_dir, subprocess_env
):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)
    assert not (out / ".state").exists(), (
        f"{gen_spec.name}: .state/ should be removed after successful completion"
    )


# ---------------------------------------------------------------------------
# Cache / rerun
# ---------------------------------------------------------------------------

def test_rerun_short_circuits_entire_pipeline(
    gen_spec, tmp_output_dir, subprocess_env
):
    first = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert first.returncode == 0, first.stderr

    second = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert second.returncode == 0, second.stderr

    assert "Skipping entire pipeline" in second.stdout, (
        f"{gen_spec.name}: expected top-level short-circuit on rerun.\n"
        f"stdout:\n{second.stdout}"
    )
    assert "Success [Stage" not in second.stdout, (
        f"{gen_spec.name}: no individual stage should have run on rerun.\n"
        f"stdout:\n{second.stdout}"
    )


def test_done_file_consistent_after_completion(
    gen_spec, tmp_output_dir, subprocess_env
):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)

    done = out / "done"
    assert done.is_file()
    result = subprocess.run(
        ["sha256sum", "-c", "--status", "done"],
        cwd=out,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{gen_spec.name}: done inconsistent.\n"
        f"contents:\n{done.read_text()}\nstderr:\n{result.stderr}"
    )


def test_top_level_done_records_original_inputs(
    gen_spec, tmp_output_dir, subprocess_env
):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)

    done = (out / "done").read_text()
    paths = [ln.split(maxsplit=1)[1].strip() for ln in done.splitlines() if ln.strip()]

    assert str(INP_EDGE) in paths, (
        f"{gen_spec.name}: top-level done should hash INPUT_EDGELIST.\nrecorded: {paths}"
    )
    assert str(INP_COM) in paths, (
        f"{gen_spec.name}: top-level done should hash INPUT_CLUSTERING.\nrecorded: {paths}"
    )
    for name in ("edge.csv", "com.csv"):
        assert str(out / name) in paths, (
            f"{gen_spec.name}: top-level done should hash final {name}.\nrecorded: {paths}"
        )
    stateful = [p for p in paths if "/.state/" in p]
    assert not stateful, (
        f"{gen_spec.name}: top-level done should not reference .state/ paths; found: {stateful}"
    )


def test_final_output_corruption_triggers_full_rerun(
    gen_spec, tmp_output_dir, subprocess_env
):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)

    (out / "edge.csv").write_text("source,target\n0,1\n")

    second = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert second.returncode == 0, second.stderr
    assert "State change detected" in second.stdout, (
        f"{gen_spec.name}: expected state.sh to notice mutated edge.csv.\n{second.stdout}"
    )
    for stage in SIMPLE_STAGES:
        assert f"Success [Stage {stage}" in second.stdout, (
            f"{gen_spec.name}: Stage {stage} should have re-run.\n{second.stdout}"
        )


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
    # Mutating to a degenerate 2-node clustering may legitimately cause the
    # generator to fail (it's tiny and ill-formed).  What we require is the
    # cache-invalidation signal; a non-zero exit after that is acceptable.
    assert "State change detected" in second.stdout, (
        f"{gen_spec.name}: expected state.sh to notice mutated clustering.\n{second.stdout}"
    )


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def test_run_log_contains_every_stage(gen_spec, tmp_output_dir, subprocess_env):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)
    log_text = (out / "run.log").read_text()
    for stage in SIMPLE_STAGES:
        assert f"[Stage {stage}]" in log_text, (
            f"{gen_spec.name}: run.log missing [Stage {stage}] prefix.\n"
            f"first 500 chars:\n{log_text[:500]}"
        )


def test_no_per_stage_log_files_in_user_tree(
    gen_spec, tmp_output_dir, subprocess_env
):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)

    surviving_logs = [p for p in out.rglob("*.log") if p != out / "run.log"]
    assert not surviving_logs, (
        f"{gen_spec.name}: unexpected per-stage log files: "
        f"{[str(p.relative_to(out)) for p in surviving_logs]}"
    )
    stray = list(out.rglob("time_and_err.log"))
    assert not stray, (
        f"{gen_spec.name}: legacy time_and_err.log files still present: "
        f"{[str(p.relative_to(out)) for p in stray]}"
    )


def test_run_log_not_hashed_in_top_level_done(
    gen_spec, tmp_output_dir, subprocess_env
):
    assert run_generator(gen_spec, tmp_output_dir, subprocess_env).returncode == 0
    out = run_dir(tmp_output_dir, gen_spec.name)
    done = (out / "done").read_text()
    assert "run.log" not in done, (
        f"{gen_spec.name}: run.log should not be hashed.\n{done}"
    )
