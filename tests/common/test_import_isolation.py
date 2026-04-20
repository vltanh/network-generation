"""Guard tests: heavy/optional deps must not leak into modules that simple
generators load.

Motivating fact: `abcd`, `abcd+o`, `lfr`, `npso` do not compute min-cut and
do not build a sparse probs matrix.  If `src/pipeline_common.py` or the
per-generator `profile.py` imports `pymincut` / `scipy.sparse` at module top,
those generators can't be installed without those packages — defeating the
"install only what you need" contract.

The guards below are *static* (they parse the source rather than import
the module) so they run without needing any of the heavy deps installed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"


def _top_level_imports(py_path: Path) -> set[str]:
    """Return the set of module names imported at module top level.

    Imports inside functions/classes/try blocks are *not* included — those
    are lazy and do not gate `import <module>`.
    """
    tree = ast.parse(py_path.read_text(), filename=str(py_path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


# ---------------------------------------------------------------------------
# pipeline_common — loaded by EVERY generator; must be the lightest module.
# ---------------------------------------------------------------------------

def test_pipeline_common_does_not_top_import_scipy():
    """pipeline_common is imported by every generator; scipy.sparse is only
    needed for load_probs_matrix (sbm + ec-sbm), so that import must be
    lazy, not at module top."""
    top = _top_level_imports(SRC / "pipeline_common.py")
    assert "scipy" not in top, (
        "scipy must not be imported at module top level in pipeline_common.py; "
        "load it lazily inside load_probs_matrix so generators that don't "
        "call it (lfr, npso, abcd, abcd+o) don't require scipy to install."
    )


def test_pipeline_common_does_not_top_import_numpy():
    """pipeline_common has no numpy call sites."""
    top = _top_level_imports(SRC / "pipeline_common.py")
    assert "numpy" not in top


def test_pipeline_common_does_not_top_import_pymincut():
    """pipeline_common has no pymincut call sites."""
    top = _top_level_imports(SRC / "pipeline_common.py")
    assert "pymincut" not in top


# ---------------------------------------------------------------------------
# profile_common — shared primitives module (no generator-specific code).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (SRC / "profile_common.py").exists(),
    reason="profile_common.py not yet created",
)
def test_profile_common_does_not_top_import_pymincut():
    top = _top_level_imports(SRC / "profile_common.py")
    assert "pymincut" not in top, (
        "pymincut is ec-sbm only; profile_common must stay generator-agnostic."
    )


@pytest.mark.skipif(
    not (SRC / "profile_common.py").exists(),
    reason="profile_common.py not yet created",
)
def test_profile_common_does_not_top_import_numpy():
    top = _top_level_imports(SRC / "profile_common.py")
    assert "numpy" not in top, (
        "numpy is only needed by the lfr mixing-parameter branch; "
        "load it lazily where used."
    )


# ---------------------------------------------------------------------------
# Per-generator profile.py modules — each must import only its own deps.
# ---------------------------------------------------------------------------

_FORBIDDEN = {
    "sbm":    {"pymincut"},
    "abcd":   {"pymincut", "scipy", "numpy"},
    "abcd+o": {"pymincut", "scipy", "numpy"},
    "lfr":    {"pymincut", "scipy"},
    "npso":   {"pymincut", "scipy"},
}


@pytest.mark.parametrize("gen,forbidden", sorted(_FORBIDDEN.items()))
def test_simple_gen_profile_does_not_top_import_forbidden(gen, forbidden):
    prof = SRC / gen / "profile.py"
    if not prof.exists():
        pytest.skip(f"{gen}/profile.py not yet created")
    top = _top_level_imports(prof)
    leaked = top & forbidden
    assert not leaked, (
        f"{gen}/profile.py top-imports forbidden modules {sorted(leaked)}; "
        f"move them lazy or drop them."
    )
