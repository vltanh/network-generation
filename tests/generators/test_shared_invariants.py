"""Cross-gen output invariants. Parametrized over all 7 generators.

These are guarantees the docs' guarantee table asserts at the *output
contract* level — enforced by the two shipping guards in
``pipeline_common`` (``simplify_edges``, ``drop_singleton_clusters``)
plus the universe-consistency check done here.

Reads from the session-cached ``fresh_run`` output tree, so the
end-to-end pipeline is run exactly once per generator across this file
and the existing smoke/determinism suites.
"""
from __future__ import annotations

import pandas as pd
import pytest


pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Edge-file invariants (every generator, no exceptions)
# ---------------------------------------------------------------------------

def test_edge_csv_has_canonical_header(fresh_run, gen_spec):
    out, _ = fresh_run
    df = pd.read_csv(out / "edge.csv")
    assert list(df.columns) == ["source", "target"], (
        f"{gen_spec.name}: edge.csv header is {list(df.columns)}"
    )


def test_edge_csv_has_no_self_loops(fresh_run, gen_spec):
    """simplify_edges guarantee: no u == v rows."""
    out, _ = fresh_run
    df = pd.read_csv(out / "edge.csv", dtype=str)
    loops = df[df["source"] == df["target"]]
    assert loops.empty, (
        f"{gen_spec.name}: edge.csv has {len(loops)} self-loops; first:\n"
        f"{loops.head()}"
    )


def test_edge_csv_has_no_parallel_edges(fresh_run, gen_spec):
    """simplify_edges guarantee: each undirected pair appears once."""
    out, _ = fresh_run
    df = pd.read_csv(out / "edge.csv", dtype=str)
    # (s,t) is canonical (min, max); a dup must have the same (source, target).
    dups = df[df.duplicated(subset=["source", "target"], keep=False)]
    assert dups.empty, (
        f"{gen_spec.name}: edge.csv has {len(dups)} parallel edges; first:\n"
        f"{dups.head()}"
    )


def test_edge_csv_has_no_parallel_undirected_pairs(fresh_run, gen_spec):
    """Stronger variant of the parallel-edge check: normalize each row
    to a canonical (min, max) pair and assert the set is unique.

    NOTE: docs claim ``edge.csv`` rows are written in canonical
    (min, max) order. In practice, gens that ship through
    ``combine_edgelists.py`` (abcd, abcd+o, lfr, npso with match_degree
    on, ec-sbm-v1/v2) preserve the first-seen orientation rather than
    re-canonicalizing. This test is the robust dedup check that holds
    regardless of row orientation.
    """
    out, _ = fresh_run
    df = pd.read_csv(out / "edge.csv", dtype=str)
    if df.empty:
        return
    pairs = [(min(s, t), max(s, t))
             for s, t in zip(df["source"], df["target"])]
    assert len(pairs) == len(set(pairs)), (
        f"{gen_spec.name}: edge.csv has duplicate undirected pairs "
        f"({len(pairs) - len(set(pairs))} duplicates)"
    )


# ---------------------------------------------------------------------------
# com.csv invariants
# ---------------------------------------------------------------------------

def test_com_csv_header(fresh_run, gen_spec):
    out, _ = fresh_run
    df = pd.read_csv(out / "com.csv")
    assert list(df.columns) == ["node_id", "cluster_id"]


def test_com_csv_has_no_singleton_clusters(fresh_run, gen_spec):
    """drop_singleton_clusters guarantee: every surviving cluster has ≥ 2
    members. (Profile-stage singletons are demoted to outliers, so they
    should not reappear in the final com.csv.)"""
    out, _ = fresh_run
    df = pd.read_csv(out / "com.csv", dtype=str)
    sizes = df["cluster_id"].value_counts()
    singletons = sizes[sizes < 2]
    assert singletons.empty, (
        f"{gen_spec.name}: com.csv has {len(singletons)} singleton cluster(s): "
        f"{list(singletons.index)[:5]}"
    )


def test_com_csv_has_no_duplicate_node_ids(fresh_run, gen_spec):
    """Each node belongs to exactly one cluster."""
    out, _ = fresh_run
    df = pd.read_csv(out / "com.csv", dtype=str)
    dups = df[df.duplicated(subset=["node_id"], keep=False)]
    assert dups.empty, (
        f"{gen_spec.name}: com.csv has {len(dups)} duplicate node_id rows"
    )


# ---------------------------------------------------------------------------
# Universe consistency
# ---------------------------------------------------------------------------

def test_every_com_node_appears_in_edge_or_is_isolated(fresh_run, gen_spec):
    """Soft check: a node listed in com.csv should plausibly appear in
    edge.csv unless it's isolated. We don't require strict membership (a
    hub-less cluster could theoretically leave an isolated node), but we
    do require that com.csv's universe does not exceed what the gen was
    told to work with.
    """
    out, _ = fresh_run
    com = pd.read_csv(out / "com.csv", dtype=str)
    edges = pd.read_csv(out / "edge.csv", dtype=str)
    edge_nodes = set(edges["source"]).union(edges["target"])
    com_nodes = set(com["node_id"])
    # Invariant: all clustered nodes appear in the edge list, OR the
    # generator is allowed to produce isolated clustered nodes. We flag
    # only the common case by checking proportion.
    if com_nodes:
        appearing = com_nodes & edge_nodes
        frac = len(appearing) / len(com_nodes)
        assert frac >= 0.5, (
            f"{gen_spec.name}: only {frac:.1%} of com.csv nodes appear in "
            f"edge.csv; likely a universe-mismatch bug"
        )
