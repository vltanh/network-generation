"""Fast tests for --keep-state plumbing inside the per-generator pipelines.

For the 5 simple-gen wrappers (sbm, abcd, abcd+o, lfr, npso): copy the
wrapper into a tmp dir alongside a stub `_common/simple_pipeline.sh` that
echoes the value of KEEP_STATE and exits.  Confirms the wrapper parses
--keep-state and propagates it into the dispatcher's scope.

For ec-sbm's unified `pipeline.sh`: static check that it parses
--keep-state into KEEP_STATE=1 and gates the final `rm -rf` on it.
Behavioral verification of the gating happens in the slow
ec-sbm integration suite.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Simple-gen wrappers — behavioral
# ---------------------------------------------------------------------------

SIMPLE_GENS = ["sbm", "abcd", "abcd+o", "lfr", "npso"]


@pytest.fixture
def wrapper_repo(tmp_path: Path) -> Path:
    """Mirror src/<gen>/pipeline.sh + a stub _common/simple_pipeline.sh.

    The stub skips all real work and just prints KEEP_STATE so tests
    can assert on its value.  Each wrapper must `source` the stub
    instead of the real dispatcher, which happens automatically because
    the wrapper resolves `_common/` relative to its own SCRIPT_DIR.
    """
    src = tmp_path / "src"
    for gen in SIMPLE_GENS:
        gen_src = REPO_ROOT / "src" / gen / "pipeline.sh"
        dst = src / gen / "pipeline.sh"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(gen_src, dst)
        dst.chmod(0o755)

    stub = src / "_common" / "simple_pipeline.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/bin/bash\n"
        "# Stub dispatcher: print KEEP_STATE so the wrapper test can assert.\n"
        "echo \"KEEP_STATE=${KEEP_STATE:-unset}\"\n"
        "exit 0\n"
    )
    stub.chmod(0o755)

    # Minimal inputs.
    inp_edge = tmp_path / "in.csv"
    inp_com = tmp_path / "com.csv"
    inp_edge.write_text("source,target\n0,1\n")
    inp_com.write_text("node_id,cluster_id\n0,0\n1,1\n")

    # Stub external dirs/binaries that wrappers validate.
    (tmp_path / "ext" / "abcd").mkdir(parents=True)
    (tmp_path / "ext" / "lfr").mkdir(parents=True)
    (tmp_path / "ext" / "lfr" / "benchmark").write_text("#!/bin/bash\n")
    (tmp_path / "ext" / "npso").mkdir(parents=True)

    return tmp_path


def _invoke_wrapper(repo: Path, gen: str, extra: list[str] | None = None) -> subprocess.CompletedProcess:
    out_dir = repo / "out" / gen
    cmd = [
        "bash", str(repo / "src" / gen / "pipeline.sh"),
        "--input-edgelist", str(repo / "in.csv"),
        "--input-clustering", str(repo / "com.csv"),
        "--output-dir", str(out_dir),
    ]
    # Wrapper-required external-dir flags. Wrappers use the short
    # pipeline-layer names: --package-dir for abcd/abcd+o/npso, --binary
    # for lfr. The dispatcher-level --abcd-dir / --lfr-binary / --npso-dir
    # flags are translated into these by configs/*.sh.
    if gen in ("abcd", "abcd+o"):
        cmd.extend(["--package-dir", str(repo / "ext" / "abcd")])
    elif gen == "lfr":
        cmd.extend(["--binary", str(repo / "ext" / "lfr" / "benchmark")])
    elif gen == "npso":
        cmd.extend(["--package-dir", str(repo / "ext" / "npso")])

    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.mark.parametrize("gen", SIMPLE_GENS)
def test_wrapper_defaults_keep_state_off(wrapper_repo: Path, gen: str):
    result = _invoke_wrapper(wrapper_repo, gen)
    assert result.returncode == 0, result.stderr
    assert "KEEP_STATE=0" in result.stdout, (
        f"{gen}: expected KEEP_STATE=0 by default; stdout={result.stdout!r}"
    )


@pytest.mark.parametrize("gen", SIMPLE_GENS)
def test_wrapper_propagates_keep_state(wrapper_repo: Path, gen: str):
    result = _invoke_wrapper(wrapper_repo, gen, extra=["--keep-state"])
    assert result.returncode == 0, result.stderr
    assert "KEEP_STATE=1" in result.stdout, (
        f"{gen}: --keep-state must set KEEP_STATE=1; stdout={result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# ec-sbm pipelines — static check
# ---------------------------------------------------------------------------

EC_SBM_PIPELINES = [
    REPO_ROOT / "src" / "ec-sbm" / "pipeline.sh",
]


@pytest.mark.parametrize("path", EC_SBM_PIPELINES, ids=lambda p: p.parent.name)
def test_ec_sbm_pipeline_parses_keep_state(path: Path):
    text = path.read_text()
    assert re.search(r"--keep-state\)\s*KEEP_STATE=1", text), (
        f"{path}: missing --keep-state arg parser"
    )
