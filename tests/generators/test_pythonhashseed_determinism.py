"""PYTHONHASHSEED-determinism test for every generator.

Runs each generator twice with different PYTHONHASHSEED env values and
asserts every data byte matches: both the user-facing files (edge.csv,
com.csv, params.txt, sources.json) AND every per-stage intermediate
under `.state/<stage>/` that ends in a data extension (.csv, .json,
.txt). Logs (`run.log`, `time_and_err.log`) and the `done` sha256
manifest are skipped because they bake in timestamps.

Catches set / dict-iteration sites whose bytes leak when the canonical
PYTHONHASHSEED=0 pin is removed (standalone callers, notebooks, future
Python upgrades). Goes deeper than the user-facing-only check so we
detect leaks at any pipeline stage, not just the surviving final.

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

# Suffixes to compare. .log and the bare `done` file carry timestamps;
# `time_and_err.log` ditto. Everything else is data.
DATA_SUFFIXES = (".csv", ".json", ".txt")
SKIP_NAMES = frozenset({"run.log", "time_and_err.log", "done"})


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _collect_data_files(root: Path) -> dict[str, str]:
    """Return {relpath: sha256} for every data file under `root`.

    Walks the entire run directory including `.state/<stage>/` so per-
    stage intermediates participate in the byte-equality check. Skips
    files whose names or extensions carry timestamps / logs.
    """
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in SKIP_NAMES:
            continue
        if p.suffix not in DATA_SUFFIXES:
            continue
        rel = p.relative_to(root).as_posix()
        out[rel] = _sha256_file(p)
    return out


def test_pythonhashseed_determinism(
    gen_spec, tmp_path_factory
):
    """Same input, same --seed, two PYTHONHASHSEED values → byte-equal
    output AND byte-equal `.state/` intermediates.
    """
    safe = gen_spec.name.replace("+", "_").replace("-", "_")
    hashes: dict[str, dict[str, str]] = {}
    out_dirs: dict[str, Path] = {}

    for hs in HASHSEEDS:
        env = _env()
        env["PYTHONHASHSEED"] = hs
        out_root = tmp_path_factory.mktemp(f"hashseed_{safe}_{hs}")
        proc = run_generator(gen_spec, out_root, env, extra=["--keep-state"])
        assert proc.returncode == 0, (
            f"{gen_spec.name} failed under PYTHONHASHSEED={hs}:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        out = run_dir(out_root, gen_spec.name)
        out_dirs[hs] = out
        hashes[hs] = _collect_data_files(out)

    a, b = HASHSEEDS
    diff: list[str] = []
    only_a = set(hashes[a]) - set(hashes[b])
    only_b = set(hashes[b]) - set(hashes[a])
    for name in sorted(only_a):
        diff.append(f"{name}: missing under PYTHONHASHSEED={b}")
    for name in sorted(only_b):
        diff.append(f"{name}: missing under PYTHONHASHSEED={a}")
    for name in sorted(set(hashes[a]) & set(hashes[b])):
        if hashes[a][name] != hashes[b][name]:
            diff.append(
                f"{name}: {hashes[a][name][:12]} vs {hashes[b][name][:12]}"
            )

    assert not diff, (
        f"{gen_spec.name}: bytes differ across PYTHONHASHSEED "
        f"({a} vs {b}):\n  " + "\n  ".join(diff)
        + f"\n\nrun_a={out_dirs[a]}\nrun_b={out_dirs[b]}"
    )

    # Cleanup both tmp roots.
    for hs in HASHSEEDS:
        shutil.rmtree(out_dirs[hs].parent.parent.parent.parent.parent,
                      ignore_errors=True)
