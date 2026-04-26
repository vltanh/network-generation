"""Tests for ``src/graph_utils.py``.

Covers:
  - ``normalize_edge``: the undirected (min, max) canonicalizer used by
    ec-sbm's constructive core, match_degree's pairing, combine_edgelists's
    dedup — every place where an edge ID-pair must have a single spelling.
  - ``run_rewire_attempts``: the 2-opt retry driver shared between ec-sbm's
    block-preserving rewire and match_degree's configuration-model pairing.
    Its contract is: retry up to ``max_retries`` passes, early-exit when the
    deque empties, end a pass on stagnation (deque length unchanged after a
    full pass), and honor the ``process_one_edge -> True`` early-break.
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from graph_utils import normalize_edge, run_rewire_attempts  # noqa: E402


# ---------------------------------------------------------------------------
# normalize_edge
# ---------------------------------------------------------------------------

def test_normalize_edge_sorts_ints():
    assert normalize_edge(3, 1) == (1, 3)
    assert normalize_edge(1, 3) == (1, 3)


def test_normalize_edge_idempotent():
    e = normalize_edge(7, 2)
    assert normalize_edge(*e) == e


def test_normalize_edge_equal_inputs_yield_self_loop_tuple():
    """Callers use the (u,u) result to flag self-loops in downstream checks."""
    assert normalize_edge(5, 5) == (5, 5)


def test_normalize_edge_works_on_strings():
    """String node IDs (abcd/lfr integer-strings, dnc SHA-looking IDs) use
    lexicographic order."""
    assert normalize_edge("b", "a") == ("a", "b")
    assert normalize_edge("10", "2") == ("10", "2")  # lex not numeric


# ---------------------------------------------------------------------------
# run_rewire_attempts — empty / early-exit paths
# ---------------------------------------------------------------------------

def test_empty_deque_noop():
    """No invalid edges → never call the callback, return silently."""
    calls = []

    def cb(e, invalid):
        calls.append(e)
        return False

    run_rewire_attempts(deque(), cb, max_retries=3)
    assert calls == []


def test_callback_returning_true_breaks_current_pass():
    """A True return ends the inner while. The outer retry loop then starts
    a fresh pass on whatever's still in the deque."""
    d = deque([("a", "b"), ("c", "d"), ("e", "f")])
    seen = []

    def cb(e, invalid):
        seen.append(e)
        # First edge ends the current pass; deque still has 2 edges.
        return True

    # Only one max_retry — so we see exactly one edge visited, rest remain.
    run_rewire_attempts(d, cb, max_retries=1)
    assert seen == [("a", "b")]
    assert list(d) == [("c", "d"), ("e", "f")]


# ---------------------------------------------------------------------------
# run_rewire_attempts — resolution path
# ---------------------------------------------------------------------------

def test_callback_resolving_every_edge_drains_deque():
    """Callback that consumes each edge (returns False without re-appending)
    drains the deque in a single pass."""
    d = deque([("a", "b"), ("c", "d"), ("e", "f")])
    resolved = []

    def cb(e, invalid):
        resolved.append(e)
        return False  # consumed, not re-queued

    run_rewire_attempts(d, cb, max_retries=5)
    assert not d
    assert resolved == [("a", "b"), ("c", "d"), ("e", "f")]


def test_partial_resolution_shrinks_deque_across_passes():
    """Pass 1 resolves half; pass 2 resolves rest. Tests the "shrunk ->
    reset recycle counter" branch in the driver."""
    d = deque([(i, i + 10) for i in range(4)])

    pass_count = {"n": 0}

    def cb(e, invalid):
        # Every odd iteration: keep edge in deque (simulates stuck edge).
        # Every even: drop it.
        pass_count["n"] += 1
        if pass_count["n"] % 2 == 0:
            invalid.append(e)
        return False

    run_rewire_attempts(d, cb, max_retries=5)
    # Exact drain depends on order, but it must make progress.
    assert len(d) < 4


# ---------------------------------------------------------------------------
# run_rewire_attempts — stagnation
# ---------------------------------------------------------------------------

def test_stagnation_ends_inner_loop_even_without_true_return():
    """If a full pass makes no net progress (every edge re-appended), the
    stagnation detector breaks the inner while and starts the next retry.
    max_retries=1 then stops the outer loop, leaving edges behind."""
    d = deque([(0, 1), (2, 3), (4, 5)])

    def cb(e, invalid):
        # Never resolve: always re-append.
        invalid.append(e)
        return False

    run_rewire_attempts(d, cb, max_retries=1)
    # All 3 edges still in the deque — driver did not loop forever.
    assert len(d) == 3


def test_max_retries_bounds_total_passes():
    """With a callback that never resolves, we must get exactly
    ``max_retries`` passes over the deque, not more."""
    d = deque([("u", "v")])
    per_edge_visits = {"u,v": 0}

    def cb(e, invalid):
        per_edge_visits["u,v"] += 1
        invalid.append(e)
        return False

    run_rewire_attempts(d, cb, max_retries=4)
    # One edge, four retries, each retry ends by stagnation after one visit.
    assert per_edge_visits["u,v"] == 4
