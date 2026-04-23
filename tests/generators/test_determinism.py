"""Mutation / rerun behavior across all 7 generators.

Each test owns its output dir (via `tmp_output_dir`) because these
exercise the pipeline's short-circuit, cache-invalidate, and
keep-state paths.
"""
from __future__ import annotations

import subprocess

import pytest

from .conftest import INP_COM, INP_EDGE, run_dir, run_generator


pytestmark = pytest.mark.slow


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
    for sub in gen_spec.state_stage_dirs:
        assert (state / sub).is_dir(), (
            f"{gen_spec.name}: missing .state/{sub}/"
        )
    assert (out / "edge.csv").is_file()
    assert (out / "done").is_file()


def test_keep_state_stage_caches_survive_final_output_corruption(
    gen_spec, tmp_output_dir, subprocess_env
):
    """Corrupt a user-facing file that the top-level done tracks but no
    stage done tracks. The pipeline must detect the change, rebuild the
    top-level from cached stages, and leave every stage's done valid
    (no 'Success [Stage' log line).
    """
    first = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)

    target = gen_spec.untracked_user_file
    if target == "edge.csv":
        payload = "source,target\n0,1\n"
    elif target == "com.csv":
        payload = "node_id,cluster_id\n0,0\n"
    else:
        pytest.fail(f"unsupported corruption target for {gen_spec.name}: {target}")
    (out / target).write_text(payload)

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
    assert (out / ".state").is_dir()
    # First state-dir in the ordering carries a `done` file in both families.
    assert (out / ".state" / gen_spec.state_stage_dirs[0] / "done").is_file()


def test_keep_state_rerun_regenerates_when_state_inconsistent(
    gen_spec, tmp_output_dir, subprocess_env
):
    first = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)

    probe = out / ".state" / gen_spec.inconsistency_probe
    assert probe.is_file()
    probe.unlink()

    second = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert second.returncode == 0, second.stderr
    assert "Top-level done valid but .state/ is inconsistent" in second.stdout
    assert (out / ".state").is_dir()
    assert probe.is_file()
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
    """An inconsistent .state/ is a signal something went wrong. Without
    --keep-state the pipeline regenerates from scratch rather than trust
    the top-level done and silently wipe the broken cache.
    """
    first = run_generator(
        gen_spec, tmp_output_dir, subprocess_env, extra=["--keep-state"]
    )
    assert first.returncode == 0, first.stderr
    out = run_dir(tmp_output_dir, gen_spec.name)

    probe = out / ".state" / gen_spec.inconsistency_probe
    assert probe.is_file()
    probe.unlink()

    second = run_generator(gen_spec, tmp_output_dir, subprocess_env)
    assert second.returncode == 0, second.stderr
    assert "Top-level done valid but .state/ is inconsistent" in second.stdout
    assert not (out / ".state").exists()
    assert (out / "edge.csv").is_file()
    assert (out / "done").is_file()
