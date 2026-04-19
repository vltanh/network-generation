"""Unit tests for src/pipeline_common.py helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from pipeline_common import load_probs_matrix  # noqa: E402


def test_load_probs_matrix_populated(tmp_path):
    ec = tmp_path / "edge_counts.csv"
    ec.write_text("0,0,12\n0,1,3\n1,1,7\n2,2,5\n")

    probs = load_probs_matrix(ec, num_clusters=3)

    dense = probs.toarray()
    assert dense[0, 0] == 12
    assert dense[0, 1] == 3
    assert dense[1, 1] == 7
    assert dense[2, 2] == 5
    # Missing entries default to 0.
    assert dense[1, 0] == 0
    assert dense[2, 0] == 0
    assert dense.shape == (3, 3)


def test_load_probs_matrix_empty_file(tmp_path):
    ec = tmp_path / "edge_counts.csv"
    ec.write_text("")

    probs = load_probs_matrix(ec, num_clusters=4)

    assert probs.shape == (4, 4)
    assert probs.nnz == 0


def test_load_probs_matrix_int_dtype(tmp_path):
    ec = tmp_path / "edge_counts.csv"
    ec.write_text("0,0,1\n")

    probs = load_probs_matrix(ec, num_clusters=2)
    assert probs.dtype.kind == "i"
