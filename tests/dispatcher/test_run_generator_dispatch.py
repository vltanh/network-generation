"""Fast tests for the per-generator dispatch contract in run_generator.sh.

We don't actually run any generator — we copy run_generator.sh into a tmp
directory, stub each src/<gen>/pipeline.sh so it just records its
argv, and exercise the dispatcher to verify:

  * Every accepted generator routes to its own pipeline.sh.
  * Each pipeline gets the common --input-edgelist/--input-clustering/
    --output-dir flags.
  * Generator-specific flags (seed, threads, abcd-dir, lfr-binary,
    npso-dir, ec-sbm-v2 algorithm trio) are forwarded only where they
    apply — and absent where they don't.
  * The unsupported-generator branch still rejects.

These tests are fast (each invocation is a few hundred ms) and exercise
only the dispatcher, not the generators. End-to-end coverage lives in
tests/generators/ behind `-m slow`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_GENERATOR_SRC = REPO_ROOT / "run_generator.sh"

GENS = ["ec-sbm-v2", "ec-sbm-v1", "sbm", "abcd", "abcd+o", "lfr", "npso"]

STUB_PIPELINE = """#!/bin/bash
# Stub pipeline.sh: write argv to $OUT_DIR/argv (one arg per line) and
# touch edge.csv so run_generator.sh's post-gen existence check passes.
out_dir=""
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --output-dir) out_dir="$2"; shift 2 ;;
        *) shift ;;
    esac
done
mkdir -p "${out_dir}"
printf '%s\\n' "$@" > "${out_dir}/argv.unused"
# Re-walk argv to write the original ordering, since the loop above ate it.
:
"""


@pytest.fixture
def stub_repo(tmp_path: Path) -> Path:
    """Create a fake repo: real run_generator.sh + stub per-gen pipelines."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy(RUN_GENERATOR_SRC, root / "run_generator.sh")
    (root / "run_generator.sh").chmod(0o755)
    # Mirror the configs/ registry so the dispatcher can discover them.
    shutil.copytree(REPO_ROOT / "configs", root / "configs")

    for gen in GENS:
        # Path the dispatcher invokes: src/<gen>/pipeline.sh for simple
        # gens; src/ec-sbm/pipeline.sh for both ec-sbm presets.
        if gen.startswith("ec-sbm-"):
            stub_path = root / "src" / "ec-sbm" / "pipeline.sh"
        else:
            stub_path = root / "src" / gen / "pipeline.sh"
        stub_path.parent.mkdir(parents=True, exist_ok=True)
        # stub records full argv then touches edge.csv so the existence check
        # in run_generator.sh succeeds.
        stub_path.write_text(
            "#!/bin/bash\n"
            "args=(\"$@\")\n"
            "out=\"\"\n"
            "for ((i=0; i<${#args[@]}; i++)); do\n"
            "    if [[ \"${args[i]}\" == \"--output-dir\" ]]; then out=\"${args[i+1]}\"; fi\n"
            "done\n"
            "mkdir -p \"$out\"\n"
            "printf '%s\\n' \"${args[@]}\" > \"$out/argv\"\n"
            "touch \"$out/edge.csv\"\n"
        )
        stub_path.chmod(0o755)

    # Minimal input files — paths must exist and INP_COM must parse.
    inp_edge = root / "in.csv"
    inp_com = root / "com.csv"
    inp_edge.write_text("source,target\n0,1\n")
    inp_com.write_text("node_id,cluster_id\n0,0\n1,1\n")

    # External dirs: dispatcher checks they exist for abcd/lfr/npso/ec-sbm.
    (root / "ext" / "abcd").mkdir(parents=True)
    (root / "ext" / "lfr").mkdir(parents=True)
    (root / "ext" / "lfr" / "benchmark").write_text("#!/bin/bash\n")
    (root / "ext" / "npso").mkdir(parents=True)
    (root / "ext" / "ec-sbm").mkdir(parents=True)

    return root


def invoke(stub_repo: Path, generator: str, extra: list[str] | None = None) -> subprocess.CompletedProcess:
    out_dir = stub_repo / "out"
    cmd = [
        str(stub_repo / "run_generator.sh"),
        "--generator", generator,
        "--run-id", "0",
        "--input-edgelist", str(stub_repo / "in.csv"),
        "--input-clustering", str(stub_repo / "com.csv"),
        "--output-dir", str(out_dir),
        "--abcd-dir", str(stub_repo / "ext" / "abcd"),
        "--lfr-binary", str(stub_repo / "ext" / "lfr" / "benchmark"),
        "--npso-dir", str(stub_repo / "ext" / "npso"),
        "--ec-sbm-dir", str(stub_repo / "ext" / "ec-sbm"),
        "--seed", "42",
        "--n-threads", "3",
    ]
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=stub_repo)


def stub_argv(stub_repo: Path, generator: str) -> list[str]:
    """Return the argv recorded by the stub pipeline.sh for `generator`."""
    out_dir = stub_repo / "out" / "networks" / generator / "0"
    argv_file = out_dir / "argv"
    assert argv_file.is_file(), f"stub never invoked for {generator}; out tree: {list((stub_repo / 'out').rglob('*'))}"
    return argv_file.read_text().splitlines()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("generator", GENS)
def test_dispatch_routes_to_pipeline(stub_repo: Path, generator: str):
    result = invoke(stub_repo, generator)
    assert result.returncode == 0, (
        f"{generator}: dispatch failed.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    # `argv` must exist under the per-gen output tree, proving the right
    # pipeline.sh was the one we hit.
    argv = stub_argv(stub_repo, generator)
    assert "--input-edgelist" in argv
    assert "--input-clustering" in argv
    assert "--output-dir" in argv


@pytest.mark.parametrize("generator", GENS)
def test_common_flags_always_forwarded(stub_repo: Path, generator: str):
    invoke(stub_repo, generator)
    argv = stub_argv(stub_repo, generator)
    assert str(stub_repo / "in.csv") in argv
    assert str(stub_repo / "com.csv") in argv


# ---------------------------------------------------------------------------
# Per-generator flag matrix
# ---------------------------------------------------------------------------

# (generator, flag, expected_value, should_be_present)
FLAG_MATRIX = [
    # seed: every gen takes --seed
    ("sbm", "--seed", "42", True),
    ("abcd", "--seed", "42", True),
    ("abcd+o", "--seed", "42", True),
    ("lfr", "--seed", "42", True),
    ("npso", "--seed", "42", True),
    ("ec-sbm-v1", "--seed", "42", True),
    ("ec-sbm-v2", "--seed", "42", True),
    # n-threads: every gen *except* lfr
    ("sbm", "--n-threads", "3", True),
    ("abcd", "--n-threads", "3", True),
    ("abcd+o", "--n-threads", "3", True),
    ("npso", "--n-threads", "3", True),
    ("ec-sbm-v1", "--n-threads", "3", True),
    ("ec-sbm-v2", "--n-threads", "3", True),
    ("lfr", "--n-threads", None, False),
    # external-binary flags — dispatcher's --abcd-dir/--lfr-binary/--npso-dir
    # are translated by configs/*.sh into short pipeline-level flags
    # (--package-dir for abcd/abcd+o/npso, --binary for lfr).
    ("abcd", "--package-dir", None, True),
    ("abcd+o", "--package-dir", None, True),
    ("lfr", "--binary", None, True),
    ("npso", "--package-dir", None, True),
    ("ec-sbm-v1", "--package-dir", None, True),
    ("ec-sbm-v2", "--package-dir", None, True),
    ("sbm", "--package-dir", None, False),
    # ec-sbm preset bundle: both configs forward the full residual-SBM knob
    # set at the pipeline layer (sbm-overlay / scope / gen-outlier-mode /
    # edge-correction / match-degree-algorithm). The profile-stage
    # --outlier-mode stays at the pipeline default (excluded) and is not
    # forwarded by either config.
    ("ec-sbm-v1", "--sbm-overlay", None, True),
    ("ec-sbm-v2", "--no-sbm-overlay", None, True),
    ("ec-sbm-v1", "--scope", "outlier-incident", True),
    ("ec-sbm-v2", "--scope", "all", True),
    ("ec-sbm-v1", "--gen-outlier-mode", "singleton", True),
    ("ec-sbm-v2", "--gen-outlier-mode", "combined", True),
    ("ec-sbm-v1", "--edge-correction", "none", True),
    ("ec-sbm-v2", "--edge-correction", "rewire", True),
    ("ec-sbm-v1", "--match-degree-algorithm", "greedy", True),
    ("ec-sbm-v2", "--match-degree-algorithm", "hybrid", True),
    ("ec-sbm-v1", "--outlier-mode", None, False),
    ("ec-sbm-v2", "--outlier-mode", None, False),
    ("sbm", "--match-degree-algorithm", None, False),
]


# ---------------------------------------------------------------------------
# --keep-state forwarding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("generator", GENS)
def test_keep_state_absent_by_default(stub_repo: Path, generator: str):
    invoke(stub_repo, generator)
    argv = stub_argv(stub_repo, generator)
    assert "--keep-state" not in argv, (
        f"{generator}: --keep-state must default to off but argv={argv}"
    )


@pytest.mark.parametrize("generator", GENS)
def test_keep_state_forwarded_when_set(stub_repo: Path, generator: str):
    invoke(stub_repo, generator, extra=["--keep-state"])
    argv = stub_argv(stub_repo, generator)
    assert "--keep-state" in argv, (
        f"{generator}: --keep-state must reach the per-gen pipeline.sh but argv={argv}"
    )


@pytest.mark.parametrize("generator,flag,expected,should_be_present", FLAG_MATRIX)
def test_flag_forwarding(stub_repo: Path, generator: str, flag: str, expected: str | None, should_be_present: bool):
    invoke(stub_repo, generator)
    argv = stub_argv(stub_repo, generator)

    if not should_be_present:
        assert flag not in argv, f"{generator}: {flag} should NOT be forwarded but argv={argv}"
        return

    assert flag in argv, f"{generator}: {flag} should be forwarded but argv={argv}"
    if expected is not None:
        idx = argv.index(flag)
        assert argv[idx + 1] == expected, (
            f"{generator}: expected {flag}={expected}, got {argv[idx + 1]}"
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_unsupported_generator_rejected(stub_repo: Path):
    result = invoke(stub_repo, "not-a-real-gen")
    assert result.returncode != 0
    assert "Unsupported generator" in result.stdout or "Unsupported generator" in result.stderr


def test_generators_glob_ignores_non_regular_files(stub_repo: Path):
    """A directory or dangling symlink named `foo.sh` under configs/ must
    not leak into ACCEPTED_GENERATORS — only regular files count."""
    # A directory that masquerades as a generator config.
    (stub_repo / "configs" / "ghost.sh").mkdir()
    # A symlink pointing at a nonexistent target.
    (stub_repo / "configs" / "phantom.sh").symlink_to("nonexistent")

    result = invoke(stub_repo, "ghost")
    assert result.returncode != 0, "directory entry must not be accepted"
    assert "Unsupported generator" in result.stdout + result.stderr

    result = invoke(stub_repo, "phantom")
    assert result.returncode != 0, "dangling symlink must not be accepted"
    assert "Unsupported generator" in result.stdout + result.stderr


def test_singleton_warning_appears_for_csv_with_singletons(stub_repo: Path):
    """Reference check: a CSV clustering with a singleton cluster triggers
    the `WARNING: Input clustering contains N singleton cluster(s).` log line.
    """
    # Overwrite the fixture's com.csv with one that has exactly one
    # singleton (cluster 2 has only node 2).
    (stub_repo / "com.csv").write_text(
        "node_id,cluster_id\n0,0\n1,0\n2,2\n"
    )
    result = invoke(stub_repo, "sbm")
    assert "WARNING: Input clustering contains 1 singleton cluster" in result.stdout, (
        f"expected singleton warning; stdout={result.stdout!r}"
    )


def test_singleton_warning_works_with_tsv_clustering(stub_repo: Path):
    """The delimiter sniffer must detect TAB so a TSV clustering still
    triggers the singleton warning.  Prior behavior: awk -F',' treated the
    whole line as one field and under-reported to 0."""
    (stub_repo / "com.csv").write_text(
        "node_id\tcluster_id\n0\t0\n1\t0\n2\t2\n"
    )
    result = invoke(stub_repo, "sbm")
    assert "WARNING: Input clustering contains 1 singleton cluster" in result.stdout, (
        f"TSV clustering should still trigger singleton warning; "
        f"stdout={result.stdout!r}"
    )


def test_missing_required_external_dir_rejected(tmp_path: Path):
    # Build a stub repo, then *remove* the abcd dir before invoking.
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy(RUN_GENERATOR_SRC, root / "run_generator.sh")
    (root / "run_generator.sh").chmod(0o755)
    shutil.copytree(REPO_ROOT / "configs", root / "configs")
    # No src/abcd/pipeline.sh needed — we expect to fail before that.
    inp_edge = root / "in.csv"
    inp_com = root / "com.csv"
    inp_edge.write_text("source,target\n0,1\n")
    inp_com.write_text("node_id,cluster_id\n0,0\n1,1\n")

    cmd = [
        str(root / "run_generator.sh"),
        "--generator", "abcd",
        "--run-id", "0",
        "--input-edgelist", str(inp_edge),
        "--input-clustering", str(inp_com),
        "--output-dir", str(root / "out"),
        "--abcd-dir", "",  # explicitly empty
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
    assert result.returncode != 0
    assert "abcd-dir" in result.stdout + result.stderr
