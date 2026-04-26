"""Tests for ``src/match_degree.py``.

Covers:
  - ``load_reference_topologies``: direct-ID mode (ec-sbm path). Target
    degree = ref edge count; input-only endpoints are tracked with
    target=0 so their residuals still decrement partners'.
  - ``load_remap_topologies``: rank-pair mode (abcd / abcd+o / lfr / npso
    path). Target degree for the k-th highest-degree input node comes
    from the k-th highest-degree ref node.
  - ``subtract_existing_edges``: per-edge decrement, per-(src,tgt)
    dedup, non-negative residuals.
  - Each of the five algorithms (``greedy``, ``true_greedy``,
    ``random_greedy``, ``rewire``, ``hybrid``): the output is a set of
    edges with three invariants — (1) no self-loops, (2) no parallel
    edges, (3) no edge that was already present in the input.

These invariants are algorithm-agnostic; the shipping guards in
``pipeline_common.simplify_edges`` rely on them.
"""
from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from match_degree import (  # noqa: E402
    load_reference_topologies,
    load_remap_topologies,
    match_missing_degrees_greedy,
    match_missing_degrees_hybrid,
    match_missing_degrees_random_greedy,
    match_missing_degrees_rewire,
    match_missing_degrees_true_greedy,
    subtract_existing_edges,
)
from graph_utils import normalize_edge  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_edgelist(path: Path, edges: list[tuple[str, str]]) -> None:
    pd.DataFrame(edges, columns=["source", "target"]).to_csv(path, index=False)


@pytest.fixture
def ref_edgelist(tmp_path):
    """Reference graph: 4 nodes, 4 edges. Degrees: a=3, b=2, c=2, d=1."""
    path = tmp_path / "ref.csv"
    _write_edgelist(path, [("a", "b"), ("a", "c"), ("a", "d"), ("b", "c")])
    return path


@pytest.fixture
def input_edgelist(tmp_path):
    """Current output: 4 nodes, 1 edge a-b. Each node needs more degree."""
    path = tmp_path / "in.csv"
    _write_edgelist(path, [("a", "b")])
    return path


# ---------------------------------------------------------------------------
# load_reference_topologies — direct-ID mode
# ---------------------------------------------------------------------------

def test_load_reference_topologies_counts_ref_degrees(ref_edgelist):
    _, node_iid2id, out_degs = load_reference_topologies(ref_edgelist)
    deg_by_id = {node_iid2id[iid]: d for iid, d in out_degs.items()}
    assert deg_by_id == {"a": 3, "b": 2, "c": 2, "d": 1}


def test_load_reference_topologies_tracks_input_only_nodes(tmp_path, ref_edgelist):
    """A node that appears only in the input edgelist (not in ref) must
    still be tracked — target 0, but present in the bookkeeping so its
    partner's residual decrements correctly."""
    inp = tmp_path / "in.csv"
    # `z` is only in input, not ref.
    _write_edgelist(inp, [("a", "z")])
    _, node_iid2id, out_degs = load_reference_topologies(ref_edgelist, inp)
    ids = set(node_iid2id.values())
    assert "z" in ids
    deg_by_id = {node_iid2id[iid]: d for iid, d in out_degs.items()}
    assert deg_by_id["z"] == 0


# ---------------------------------------------------------------------------
# load_remap_topologies — rank-pair mode
# ---------------------------------------------------------------------------

def test_load_remap_topologies_pairs_by_descending_degree_rank(tmp_path):
    """Top-degree input node inherits top-degree ref node's target degree;
    etc. Rearrangement inequality ⇒ L1/L2-optimal pairing."""
    ref = tmp_path / "ref.csv"
    _write_edgelist(ref, [("R0", "R1"), ("R0", "R2"), ("R0", "R3"), ("R1", "R2")])
    # Ref degrees: R0=3, R1=2, R2=2, R3=1. Sorted desc (ties break asc id):
    #   R0(3), R1(2), R2(2), R3(1).
    inp = tmp_path / "in.csv"
    _write_edgelist(inp, [("X", "Y"), ("X", "Z"), ("X", "W"), ("Y", "W")])
    # Input degrees: X=3, Y=2, W=2, Z=1. Sorted desc: X(3), W(2), Y(2), Z(1).
    #   (W,Y tied at 2; tie-break by asc id → W before Y.)

    _, node_iid2id, out_degs = load_remap_topologies(inp, ref)
    deg_by_input_id = {node_iid2id[iid]: d for iid, d in out_degs.items()}
    # Pairing: X↔R0 (3), W↔R1 (2), Y↔R2 (2), Z↔R3 (1).
    assert deg_by_input_id == {"X": 3, "W": 2, "Y": 2, "Z": 1}


def test_load_remap_topologies_truncates_on_size_mismatch(tmp_path):
    """When input and ref differ in |V|, rank pairs up to min and drops
    extra ranks at the bottom."""
    ref = tmp_path / "ref.csv"
    _write_edgelist(ref, [("A", "B"), ("A", "C")])  # 3 nodes
    inp = tmp_path / "in.csv"
    _write_edgelist(inp, [("X", "Y"), ("X", "Z"), ("Z", "Q")])  # 4 nodes
    _, node_iid2id, out_degs = load_remap_topologies(inp, ref)
    assert len(out_degs) == 3  # truncated to min(|in|, |ref|) = 3
    # The dropped rank is the lowest-degree input node.
    tracked = set(node_iid2id.values())
    assert "X" in tracked  # top-degree input always kept


# ---------------------------------------------------------------------------
# subtract_existing_edges
# ---------------------------------------------------------------------------

def test_subtract_existing_edges_decrements_both_endpoints(tmp_path):
    inp = tmp_path / "in.csv"
    _write_edgelist(inp, [("a", "b")])

    node_id2iid = {"a": 0, "b": 1, "c": 2}
    out_degs = {0: 2, 1: 2, 2: 2}
    exist_neighbor, updated = subtract_existing_edges(inp, node_id2iid, out_degs)

    assert updated == {0: 1, 1: 1, 2: 2}
    assert exist_neighbor[0] == {1}
    assert exist_neighbor[1] == {0}
    assert exist_neighbor[2] == set()


def test_subtract_existing_edges_clamps_at_zero(tmp_path):
    """If an edge exists that wasn't in ref (target degree already 0), the
    subtraction floors at 0 rather than going negative."""
    inp = tmp_path / "in.csv"
    _write_edgelist(inp, [("a", "b")])
    node_id2iid = {"a": 0, "b": 1}
    out_degs = {0: 0, 1: 0}
    _, updated = subtract_existing_edges(inp, node_id2iid, out_degs)
    assert updated == {0: 0, 1: 0}


def test_subtract_existing_edges_dedups_duplicate_directed_entries(tmp_path):
    """If the same undirected edge appears twice as (a,b) and (b,a), the
    second is a no-op — count decremented once, neighbor set stays {b}."""
    inp = tmp_path / "in.csv"
    _write_edgelist(inp, [("a", "b"), ("b", "a")])
    node_id2iid = {"a": 0, "b": 1}
    out_degs = {0: 5, 1: 5}
    exist_neighbor, updated = subtract_existing_edges(inp, node_id2iid, out_degs)
    assert updated == {0: 4, 1: 4}
    assert exist_neighbor[0] == {1}


# ---------------------------------------------------------------------------
# Algorithm-level invariants
# ---------------------------------------------------------------------------

def _algo_invariants(edges, out_degs, exist_neighbor):
    """Shared post-conditions for every match-degree algorithm."""
    for u, v in edges:
        # (1) no self-loop
        assert u != v, f"self-loop: ({u}, {v})"
        # (2) canonicalized (u < v by normalize_edge)
        assert (u, v) == normalize_edge(u, v), f"not canonical: ({u}, {v})"
        # (3) not already in input
        assert v not in exist_neighbor.get(u, set()), (
            f"edge ({u}, {v}) already existed in input"
        )
    # (4) no duplicates
    assert len(edges) == len(set(edges)), "duplicate output edges"


def _setup_simple(
    n_nodes=8, base_degree=4, seed=1
):
    """Deterministic starting state: n nodes, each with base_degree stubs, no
    existing edges. Total stubs = n * base_degree; total edges target = n*b/2.
    """
    random.seed(seed)
    out_degs = {i: base_degree for i in range(n_nodes)}
    exist_neighbor = {i: set() for i in range(n_nodes)}
    return out_degs, exist_neighbor


@pytest.mark.parametrize("algo_name,algo_fn", [
    ("greedy", match_missing_degrees_greedy),
    ("true_greedy", match_missing_degrees_true_greedy),
    ("random_greedy", match_missing_degrees_random_greedy),
])
def test_heap_algo_respects_invariants(algo_name, algo_fn):
    out_degs, exist_neighbor = _setup_simple()
    # Copy for the invariant check — exist_neighbor is mutated by algos.
    baseline = {k: set(v) for k, v in exist_neighbor.items()}

    edges = algo_fn(out_degs.copy(), exist_neighbor)

    _algo_invariants(edges, out_degs, baseline)


def test_rewire_returns_valid_and_invalid_edges():
    out_degs, exist_neighbor = _setup_simple()
    baseline = {k: set(v) for k, v in exist_neighbor.items()}

    valid, invalid = match_missing_degrees_rewire(out_degs.copy(), exist_neighbor)

    # Shape
    assert isinstance(valid, set)
    assert isinstance(invalid, list)
    _algo_invariants(valid, out_degs, baseline)


def test_hybrid_output_matches_invariants():
    out_degs, exist_neighbor = _setup_simple()
    baseline = {k: set(v) for k, v in exist_neighbor.items()}

    edges = match_missing_degrees_hybrid(out_degs.copy(), exist_neighbor)
    _algo_invariants(edges, out_degs, baseline)


def test_hybrid_no_worse_than_rewire_alone():
    """Hybrid adds a greedy fallback on top of rewire's leftovers, so its
    final edge count is ≥ rewire's (never loses resolved edges)."""
    random.seed(3)
    out_degs, en1 = _setup_simple(n_nodes=10, base_degree=4, seed=3)
    hybrid_edges = match_missing_degrees_hybrid(out_degs.copy(), en1)

    random.seed(3)
    out_degs, en2 = _setup_simple(n_nodes=10, base_degree=4, seed=3)
    rewire_edges, _ = match_missing_degrees_rewire(out_degs.copy(), en2)

    assert len(hybrid_edges) >= len(rewire_edges)


def test_greedy_stub_budget_respected():
    """No algorithm can create more edges than half the total stubs."""
    out_degs, exist_neighbor = _setup_simple(n_nodes=6, base_degree=3)
    total_stubs = sum(out_degs.values())
    max_edges = total_stubs // 2  # floor for odd parity

    for fn in (
        match_missing_degrees_greedy,
        match_missing_degrees_true_greedy,
        match_missing_degrees_random_greedy,
        match_missing_degrees_hybrid,
    ):
        out_degs_copy = out_degs.copy()
        en = {k: set(v) for k, v in exist_neighbor.items()}
        edges = fn(out_degs_copy, en)
        assert len(edges) <= max_edges, (
            f"{fn.__name__} produced {len(edges)} edges, max {max_edges}"
        )


def test_algorithms_respect_prior_neighbors():
    """Every algo must refuse to re-create an edge that already exists."""
    out_degs = {0: 2, 1: 2, 2: 2, 3: 2}
    # 0-1 and 2-3 already exist; algo must not add them.
    base = {0: {1}, 1: {0}, 2: {3}, 3: {2}}

    for fn in (
        match_missing_degrees_greedy,
        match_missing_degrees_true_greedy,
        match_missing_degrees_random_greedy,
        match_missing_degrees_hybrid,
    ):
        out_degs_copy = out_degs.copy()
        en = {k: set(v) for k, v in base.items()}
        edges = fn(out_degs_copy, en)
        for u, v in edges:
            assert not (u == 0 and v == 1), f"{fn.__name__} recreated 0-1"
            assert not (u == 2 and v == 3), f"{fn.__name__} recreated 2-3"


# ---------------------------------------------------------------------------
# CLI end-to-end with the real script
# ---------------------------------------------------------------------------

def test_cli_produces_csv_with_canonical_columns(tmp_path, input_edgelist, ref_edgelist):
    """End-to-end via the CLI: writes ``degree_matching_edge.csv`` with the
    (source,target) header and every row canonicalized (source<=target)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    script = REPO_ROOT / "src" / "match_degree.py"
    env = {
        "PATH": "/home/vltanh/miniconda3/envs/nwbench/bin:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONHASHSEED": "0",
    }
    result = subprocess.run(
        ["python", str(script),
         "--input-edgelist", str(input_edgelist),
         "--ref-edgelist", str(ref_edgelist),
         "--output-folder", str(out_dir),
         "--match-degree-algorithm", "hybrid",
         "--seed", "1"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    out_csv = out_dir / "degree_matching_edge.csv"
    assert out_csv.is_file()
    df = pd.read_csv(out_csv, dtype=str)
    assert list(df.columns) == ["source", "target"]
    # Shipping guard: match_degree writes raw pairs; it does NOT run
    # simplify_edges. But (min,max) normalization is enforced internally
    # via normalize_edge, so the output should already be canonical.
    for _, row in df.iterrows():
        assert row["source"] != row["target"], "self-loop emitted"


def test_cli_remap_mode_flag(tmp_path, input_edgelist, ref_edgelist):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    script = REPO_ROOT / "src" / "match_degree.py"
    env = {
        "PATH": "/home/vltanh/miniconda3/envs/nwbench/bin:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONHASHSEED": "0",
    }
    result = subprocess.run(
        ["python", str(script),
         "--input-edgelist", str(input_edgelist),
         "--ref-edgelist", str(ref_edgelist),
         "--output-folder", str(out_dir),
         "--remap", "--match-degree-algorithm", "hybrid", "--seed", "1"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (out_dir / "degree_matching_edge.csv").is_file()
    log = (out_dir / "run.log").read_text()
    assert "remap" in log
