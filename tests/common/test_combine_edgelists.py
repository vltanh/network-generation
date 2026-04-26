"""Tests for ``src/combine_edgelists.py``.

Covers:
  - ``load_annotated_edgelist``: provenance via ``--name-*`` fallback and
    via a JSON range map (1-based inclusive).
  - end-to-end ``main``: undirected dedup (keep the first occurrence of
    each canonical pair), self-loop drop, ``sources.json`` with
    contiguous ranges, stage-4 contract for simple_pipeline (stage-2
    edges + match_degree edges → combined edge.csv).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from combine_edgelists import load_annotated_edgelist  # noqa: E402

COMBINE = REPO_ROOT / "src" / "combine_edgelists.py"
ENV = {
    "PATH": "/home/vltanh/miniconda3/envs/nwbench/bin:/usr/bin:/bin",
    "PYTHONPATH": str(REPO_ROOT / "src"),
    "PYTHONHASHSEED": "0",
}


def _write(path: Path, edges) -> None:
    pd.DataFrame(edges, columns=["source", "target"]).to_csv(path, index=False)


# ---------------------------------------------------------------------------
# load_annotated_edgelist
# ---------------------------------------------------------------------------

def test_load_annotated_falls_back_to_name_when_no_json(tmp_path):
    p = tmp_path / "gen.csv"
    _write(p, [("a", "b"), ("c", "d")])
    df = load_annotated_edgelist(p, "stage2", None)
    assert list(df["prov"]) == ["stage2", "stage2"]


def test_load_annotated_falls_back_to_stem_when_name_missing(tmp_path):
    p = tmp_path / "stage2_edges.csv"
    _write(p, [("a", "b")])
    df = load_annotated_edgelist(p, None, None)
    assert df["prov"].iloc[0] == "stage2_edges"


def test_load_annotated_uses_json_ranges(tmp_path):
    """1-based inclusive ranges like `{"S1": [1,2], "S2": [3,4]}` map to
    row provenances [S1, S1, S2, S2]."""
    p = tmp_path / "e.csv"
    _write(p, [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")])
    j = tmp_path / "src.json"
    j.write_text(json.dumps({"S1": [1, 2], "S2": [3, 4]}))
    df = load_annotated_edgelist(p, None, j)
    assert list(df["prov"]) == ["S1", "S1", "S2", "S2"]


def test_load_annotated_missing_json_falls_back_to_stem(tmp_path):
    """If the JSON path is provided but doesn't exist, fall through to the
    stem-based provenance (treat the file as a single unnamed source)."""
    p = tmp_path / "e.csv"
    _write(p, [("a", "b")])
    df = load_annotated_edgelist(p, None, tmp_path / "does_not_exist.json")
    assert df["prov"].iloc[0] == "e"


# ---------------------------------------------------------------------------
# main — end-to-end CLI
# ---------------------------------------------------------------------------

def _run_combine(tmp_path, e1, e2, *, name1="gen", name2="match"):
    out = tmp_path / "out"
    out.mkdir()
    result = subprocess.run(
        ["python", str(COMBINE),
         "--edgelist-1", str(e1), "--name-1", name1,
         "--edgelist-2", str(e2), "--name-2", name2,
         "--output-folder", str(out),
         "--output-filename", "edge.csv"],
        env=ENV, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return out


def test_combine_undirected_dedup_across_sources(tmp_path):
    """An edge (u,v) in list 1 and (v,u) in list 2 is the same undirected
    edge; output keeps the first occurrence."""
    e1 = tmp_path / "e1.csv"
    e2 = tmp_path / "e2.csv"
    _write(e1, [("a", "b")])
    _write(e2, [("b", "a"), ("c", "d")])
    out = _run_combine(tmp_path, e1, e2)

    df = pd.read_csv(out / "edge.csv")
    assert len(df) == 2  # duplicate removed
    rows = {(r.source, r.target) for r in df.itertuples()}
    assert ("a", "b") in rows
    assert ("c", "d") in rows


def test_combine_drops_self_loops(tmp_path):
    e1 = tmp_path / "e1.csv"
    e2 = tmp_path / "e2.csv"
    _write(e1, [("a", "a"), ("a", "b")])
    _write(e2, [("c", "c")])
    out = _run_combine(tmp_path, e1, e2)
    df = pd.read_csv(out / "edge.csv")
    assert len(df) == 1
    assert (df["source"].iloc[0], df["target"].iloc[0]) == ("a", "b")


def test_combine_sources_json_reflects_contiguous_ranges(tmp_path):
    """After dedup + sort-by-source, sources.json records 1-based inclusive
    ranges that partition the output rows."""
    e1 = tmp_path / "e1.csv"
    e2 = tmp_path / "e2.csv"
    _write(e1, [("a", "b"), ("a", "c")])
    _write(e2, [("d", "e"), ("b", "a")])  # (b,a) dedups with (a,b) from e1
    out = _run_combine(tmp_path, e1, e2, name1="gen", name2="match")

    sources = json.loads((out / "sources.json").read_text())
    # 3 unique edges: (a,b) gen, (a,c) gen, (d,e) match.
    total_rows = sum((hi - lo + 1) for (lo, hi) in sources.values())
    df = pd.read_csv(out / "edge.csv")
    assert total_rows == len(df) == 3
    # Must be a partition starting at row 1.
    ranges = sorted(sources.values())
    assert ranges[0][0] == 1
    # Contiguous.
    for (lo_a, hi_a), (lo_b, _hi_b) in zip(ranges, ranges[1:]):
        assert hi_a + 1 == lo_b


def test_combine_stable_on_only_one_nonempty_source(tmp_path):
    """Stage 3 (match_degree) may emit an empty degree_matching_edge.csv
    when nothing needs matching. Stage 4 must still produce a valid
    combined output with the stage-2 edges intact."""
    e1 = tmp_path / "e1.csv"
    e2 = tmp_path / "e2.csv"
    _write(e1, [("a", "b"), ("c", "d")])
    _write(e2, [])
    out = _run_combine(tmp_path, e1, e2, name1="gen", name2="match")

    df = pd.read_csv(out / "edge.csv")
    assert len(df) == 2
    sources = json.loads((out / "sources.json").read_text())
    assert "gen" in sources
    assert "match" not in sources  # empty source omitted
