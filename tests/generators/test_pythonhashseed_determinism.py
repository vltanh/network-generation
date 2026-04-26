"""PYTHONHASHSEED-determinism test for every generator.

Runs each generator twice with different PYTHONHASHSEED env values and
asserts the user-facing output bytes match. Production has historically
been shielded by `pipeline.sh` exporting PYTHONHASHSEED=0; this test
catches set/dict-iteration sites whose output bytes leak when that pin
is removed (or when standalone callers import modules directly).

Marked `slow` because each generator runs the full pipeline twice.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from .conftest import _env, run_dir, run_generator


pytestmark = pytest.mark.slow


# Two arbitrary distinct seeds; if any set/dict iteration site leaks,
# at least one of these will produce a different hash-slot order.
HASHSEEDS = ("0", "1234567")


# Files whose bytes must match across the two runs. .state/ + run.log
# carry timestamps and per-stage logs that legitimately differ; we only
# compare the user-facing artifacts that downstream tools read.
def _user_facing_files(spec_name: str) -> list[str]:
    common = ["edge.csv", "com.csv", "params.txt"]
    if spec_name.startswith("ec-sbm"):
        common.append("sources.json")
    return common


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def test_pythonhashseed_determinism(
    gen_spec, tmp_path_factory
):
    """Same input, same --seed, two PYTHONHASHSEED values → byte-equal output."""
    safe = gen_spec.name.replace("+", "_").replace("-", "_")
    hashes: dict[str, dict[str, str]] = {}
    out_dirs: dict[str, Path] = {}

    for hs in HASHSEEDS:
        env = _env()
        env["PYTHONHASHSEED"] = hs
        out_root = tmp_path_factory.mktemp(f"hashseed_{safe}_{hs}")
        proc = run_generator(gen_spec, out_root, env)
        assert proc.returncode == 0, (
            f"{gen_spec.name} failed under PYTHONHASHSEED={hs}:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        out = run_dir(out_root, gen_spec.name)
        out_dirs[hs] = out
        hashes[hs] = {
            name: _sha256_file(out / name)
            for name in _user_facing_files(gen_spec.name)
            if (out / name).is_file()
        }

    a, b = HASHSEEDS
    diff: list[str] = []
    for name, h_a in hashes[a].items():
        h_b = hashes[b].get(name)
        if h_b is None:
            diff.append(f"{name}: missing under PYTHONHASHSEED={b}")
        elif h_a != h_b:
            diff.append(f"{name}: {h_a[:12]} vs {h_b[:12]}")

    assert not diff, (
        f"{gen_spec.name}: outputs differ across PYTHONHASHSEED values "
        f"({a} vs {b}):\n  " + "\n  ".join(diff)
        + f"\n\nrun_a={out_dirs[a]}\nrun_b={out_dirs[b]}"
    )

    # Cleanup the larger run; the survivors only churn tmp.
    for hs in HASHSEEDS:
        shutil.rmtree(out_dirs[hs].parent.parent.parent.parent.parent,
                      ignore_errors=True)
