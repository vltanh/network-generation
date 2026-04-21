"""Tests for ``src/params_common.py``.

The helper writes and reads per-stage ``params.txt`` files.  Format:
``key=value`` per line, sorted keys, bools rendered as lowercase
``true``/``false``.  Values are stringified on write; the reader returns a
plain ``dict[str, str]`` and callers parse types themselves.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from params_common import read_params, write_params  # noqa: E402


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------

def test_write_params_creates_file(tmp_path):
    write_params(str(tmp_path), seed=1, n_threads=2)
    assert (tmp_path / "params.txt").is_file()


def test_roundtrip_str_values(tmp_path):
    write_params(str(tmp_path), outlier_mode="combined",
                 drop_outlier_outlier_edges=False)
    got = read_params(str(tmp_path / "params.txt"))
    assert got == {
        "outlier_mode": "combined",
        "drop_outlier_outlier_edges": "false",
    }


def test_roundtrip_int_values(tmp_path):
    write_params(str(tmp_path), seed=42, n_threads=8)
    got = read_params(str(tmp_path / "params.txt"))
    assert got == {"seed": "42", "n_threads": "8"}


@pytest.mark.parametrize("value,rendered", [
    (True, "true"), (False, "false"),
])
def test_bool_rendering(tmp_path, value, rendered):
    write_params(str(tmp_path), flag=value)
    got = read_params(str(tmp_path / "params.txt"))
    assert got == {"flag": rendered}


# ---------------------------------------------------------------------------
# Format invariants: sorted keys, key=value per line
# ---------------------------------------------------------------------------

def test_keys_sorted_alphabetically(tmp_path):
    write_params(str(tmp_path), zulu=1, alpha=2, mike=3)
    content = (tmp_path / "params.txt").read_text()
    assert content == "alpha=2\nmike=3\nzulu=1\n"


def test_one_key_per_line_format(tmp_path):
    write_params(str(tmp_path), seed=1, n_threads=2)
    lines = (tmp_path / "params.txt").read_text().splitlines()
    assert lines == ["n_threads=2", "seed=1"]
    for ln in lines:
        assert ln.count("=") == 1


# ---------------------------------------------------------------------------
# Reader robustness
# ---------------------------------------------------------------------------

def test_read_ignores_blank_lines(tmp_path):
    p = tmp_path / "params.txt"
    p.write_text("seed=1\n\nn_threads=2\n")
    assert read_params(str(p)) == {"seed": "1", "n_threads": "2"}


def test_read_rejects_malformed_line(tmp_path):
    p = tmp_path / "params.txt"
    p.write_text("seed=1\nnotakeyvalueline\n")
    with pytest.raises(ValueError, match="expected key=value"):
        read_params(str(p))


def test_read_rejects_duplicate_key(tmp_path):
    p = tmp_path / "params.txt"
    p.write_text("seed=1\nseed=2\n")
    with pytest.raises(ValueError, match="duplicate key"):
        read_params(str(p))


def test_write_rejects_empty(tmp_path):
    # A params.txt with no keys isn't meaningful — catch silent misuse.
    with pytest.raises(ValueError, match="at least one"):
        write_params(str(tmp_path))


def test_value_with_equals_sign_roundtrip(tmp_path):
    # A value containing '=' should round-trip; splitter uses first '=' only.
    write_params(str(tmp_path), expression="a=b")
    got = read_params(str(tmp_path / "params.txt"))
    assert got == {"expression": "a=b"}
