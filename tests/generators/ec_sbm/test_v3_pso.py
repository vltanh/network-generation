"""EC-SBM v3 (per-cluster PSO) sanity tests.

The headline guarantee carries over from v1 / v2: every cluster's
intra-cluster subgraph is at least k-edge-connected, where k is the
empirical per-cluster min-cut. v3 enforces this by setting
``m >= k`` in the PSO call (the first ``k+1`` nodes form a
``K_{k+1}`` clique, every later node attaches to ``m`` existing nodes,
so the induced mincut cannot drop below ``k``).

These tests cover the standalone ``pso_cluster_edges`` helper and the
``_resolve_m`` policy. End-to-end pipeline correctness is exercised by
the dnc smoke run in :mod:`tools/viz_check/ec_sbm`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
EC_SBM_SRC = REPO_ROOT / "externals" / "ec-sbm" / "src"


def _load(mod_name, fname):
    gen_dir = str(EC_SBM_SRC)
    sys.path.insert(0, gen_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            f"ec_sbm_{mod_name}", str(EC_SBM_SRC / fname),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(gen_dir)


@pytest.fixture(scope="module")
def pso_core():
    return _load("gen_pso_core", "gen_pso_core.py")


@pytest.fixture(scope="module")
def gen_v3():
    # gen_clustered_v3 imports gen_pso_core + gen_kec_core; load it first
    # so its dependencies sit on sys.path.
    return _load("gen_clustered_v3", "gen_clustered_v3.py")


@pytest.mark.parametrize("N,m", [(20, 3), (50, 4), (100, 5), (15, 7), (8, 3)])
def test_pso_min_degree_at_least_m(pso_core, N, m):
    edges = pso_core.pso_cluster_edges(N, m, 0.5, 3.0, 1)
    deg = [0] * N
    for u, v in edges:
        deg[u] += 1
        deg[v] += 1
    assert min(deg) >= m, (
        f"PSO(m={m}) min degree should be >= m; got {min(deg)} for N={N}"
    )


@pytest.mark.parametrize("N,m", [(20, 3), (50, 4), (8, 3)])
def test_pso_is_k_edge_connected(pso_core, N, m):
    pymc = pytest.importorskip("pymincut.pygraph")
    edges = pso_core.pso_cluster_edges(N, m, 0.5, 3.0, 1)
    g = pymc.PyGraph(list(range(N)), edges)
    cut = g.mincut("noi", "bqueue", False)[2]
    assert cut >= m, f"PSO(m={m}) should be {m}-edge-connected; got mincut {cut}"


def test_pso_ccoeff_decreasing_in_T(pso_core):
    cc_vals = []
    for T in (0.0, 0.1, 0.3, 0.6, 0.9):
        e = pso_core.pso_cluster_edges(80, 5, T, 3.0, 7)
        cc_vals.append(pso_core.induced_global_ccoeff(80, e))
    # Strict monotone is too strong (sampling noise); require a clear
    # drop from low T to high T plus monotone-ish behaviour.
    assert cc_vals[0] - cc_vals[-1] > 0.03, (
        f"expected ccoeff to fall as T grows; got {cc_vals}"
    )
    assert cc_vals[0] >= cc_vals[2] >= cc_vals[4] - 0.05


def test_pso_complete_graph_when_n_le_m_plus_1(pso_core):
    # N == m + 1 → "connect to all" branch always fires → full clique.
    edges = pso_core.pso_cluster_edges(5, 4, 0.5, 3.0, 0)
    assert len(edges) == 5 * 4 // 2
    assert pso_core.induced_global_ccoeff(5, edges) == pytest.approx(1.0)


def test_pso_singleton_returns_no_edges(pso_core):
    assert pso_core.pso_cluster_edges(1, 1, 0.5, 3.0, 0) == []


def test_resolve_m_floor_policy(gen_v3):
    # floor: ignore empirical mean degree.
    assert gen_v3._resolve_m(k=2, n=10, m_policy="floor", m_floor=1, empirical_mean_deg=8.0) == 2
    assert gen_v3._resolve_m(k=1, n=10, m_policy="floor", m_floor=3, empirical_mean_deg=8.0) == 3


def test_resolve_m_auto_policy(gen_v3):
    # auto: lift to round(empirical_mean_deg / 2) when above k & m_floor.
    assert gen_v3._resolve_m(k=1, n=20, m_policy="auto", m_floor=1, empirical_mean_deg=8.0) == 4
    # auto: still respects k floor.
    assert gen_v3._resolve_m(k=5, n=20, m_policy="auto", m_floor=1, empirical_mean_deg=2.0) == 5


def test_resolve_m_capped_at_n_minus_1(gen_v3):
    assert gen_v3._resolve_m(k=10, n=4, m_policy="floor", m_floor=1, empirical_mean_deg=0) == 3
