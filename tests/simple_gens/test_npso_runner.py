"""Unit tests for the npso runner abstraction (Phase A).

These do NOT spin up MATLAB. They cover the import-guard and fallback logic
so the dispatch layer stays correct regardless of whether matlab.engine is
installed on the host.
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
NPSO_SRC = REPO_ROOT / "src"


@pytest.fixture
def npso_gen(monkeypatch):
    """Import src/npso/gen.py in an isolated state.

    Each test gets a fresh module so module-level `_matlab_engine`/
    `_ENGINE_IMPORT_ERROR` reflect the test's intended state.
    """
    monkeypatch.syspath_prepend(str(NPSO_SRC))
    # Ensure a clean re-import.
    for name in list(sys.modules):
        if name == "npso.gen" or name == "gen":
            del sys.modules[name]
    # Import as src/npso/gen.py path — matches what simple_pipeline.sh does.
    spec = importlib.util.spec_from_file_location("npso_gen", NPSO_SRC / "npso" / "gen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_loads_without_matlab_engine(npso_gen):
    """gen.py must import even when matlab.engine is absent (host w/o install)."""
    assert hasattr(npso_gen, "make_runner")
    assert hasattr(npso_gen, "SubprocessRunner")
    assert hasattr(npso_gen, "EngineRunner")


def test_engine_unavailable_picks_subprocess(npso_gen, monkeypatch, caplog):
    """When _matlab_engine is None, make_runner returns a SubprocessRunner and
    logs the reason exactly once per call."""
    monkeypatch.setattr(npso_gen, "_matlab_engine", None)
    monkeypatch.setattr(npso_gen, "_ENGINE_IMPORT_ERROR", ImportError("no matlab"))
    with caplog.at_level(logging.INFO):
        runner = npso_gen.make_runner(1, Path("/tmp/npso"), Path("/tmp/wrap"))
    assert isinstance(runner, npso_gen.SubprocessRunner)
    assert any("matlab.engine for Python not available" in rec.message for rec in caplog.records)


def test_engine_start_failure_falls_back(npso_gen, monkeypatch, caplog):
    """If matlab.engine is importable but start_matlab() blows up, make_runner
    must degrade to the subprocess path rather than crash the whole run."""
    class _Fake:
        class MatlabExecutionError(Exception):
            pass

        @staticmethod
        def start_matlab(*a, **kw):
            raise RuntimeError("license server down")

    monkeypatch.setattr(npso_gen, "_matlab_engine", _Fake)
    monkeypatch.setattr(npso_gen, "_ENGINE_IMPORT_ERROR", None)
    with caplog.at_level(logging.ERROR):
        runner = npso_gen.make_runner(1, Path("/tmp/npso"), Path("/tmp/wrap"))
    assert isinstance(runner, npso_gen.SubprocessRunner)
    assert any("MATLAB engine failed to start" in rec.message for rec in caplog.records)


def test_subprocess_runner_constructs(npso_gen):
    """Smoke: SubprocessRunner must accept the new constructor signature and
    expose close() so the teardown path is safe to call unconditionally."""
    runner = npso_gen.SubprocessRunner(1, Path("/tmp/npso"), Path("/tmp/wrap"))
    assert runner.n_threads == 1
    # close() must be a no-op (nothing persistent in the subprocess path).
    runner.close()
