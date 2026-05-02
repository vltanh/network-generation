"""End-to-end checks for ec-sbm with --match-degree-mode cluster_preserving.

Slow because it spins up the full pipeline; gated under @pytest.mark.slow
and skipped without the ec-sbm submodule.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_GENERATOR = REPO_ROOT / "run_generator.sh"
EXAMPLES_IN = REPO_ROOT / "examples" / "input"
INP_EDGE = EXAMPLES_IN / "empirical_networks" / "networks" / "dnc" / "dnc.csv"
INP_COM = (
    EXAMPLES_IN / "reference_clusterings" / "clusterings"
    / "sbm-flat-best+cc" / "dnc" / "com.csv"
)
EC_SBM_DIR = REPO_ROOT / "externals" / "ec-sbm"


@pytest.fixture(params=["ec-sbm-v1", "ec-sbm-v2", "ec-sbm-v3"])
def gen_name(request):
    if not EC_SBM_DIR.exists():
        pytest.skip("ec-sbm submodule missing")
    return request.param


def _run_dir(out_root, gen):
    return out_root / "networks" / gen / "sbm-flat-best+cc" / "dnc" / "0"


@pytest.mark.slow
def test_ec_sbm_cluster_preserving_hybrid_emits_two_bands(gen_name, tmp_path):
    out_root = tmp_path / "synth"
    out_root.mkdir()
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "1"
    cmd = [
        str(RUN_GENERATOR),
        "--generator", gen_name,
        "--run-id", "0",
        "--input-edgelist", str(INP_EDGE),
        "--input-clustering", str(INP_COM),
        "--output-dir", str(out_root),
        "--network", "dnc",
        "--clustering-id", "sbm-flat-best+cc",
        "--seed", "1",
        "--n-threads", "1",
        "--ec-sbm-dir", str(EC_SBM_DIR),
        "--match-degree-mode", "cluster_preserving",
        "--match-degree-algorithm", "cluster_preserving_hybrid",
        "--keep-state",
    ]
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"{gen_name}: cluster_preserving run failed:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )

    out_dir = _run_dir(out_root, gen_name)
    md_sources = (
        out_dir / ".state" / "match_degree" / "edges" / "sources.json"
    )
    assert md_sources.exists(), f"missing {md_sources}"
    bands = json.loads(md_sources.read_text())
    expected_keys = {
        "match_degree_cluster_preserving_hybrid_rewire",
        "match_degree_cluster_preserving_hybrid_true_greedy",
    }
    assert expected_keys & set(bands.keys()), (
        f"{gen_name}: cluster_preserving_hybrid produced no expected bands; "
        f"got {set(bands.keys())}"
    )

    # Block-pair budget invariant: each match_degree edge sits in a bp
    # whose budget was non-zero before placement.
    md_edges_fp = (
        out_dir / ".state" / "match_degree" / "edges"
        / "degree_matching_edge.csv"
    )
    md_edges = pd.read_csv(md_edges_fp, dtype=str)

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from match_degree import (
        build_bp_budget_direct,
        load_reference_topologies,
    )

    pre_match_edge_fp = out_dir / ".state" / "gen_outlier" / "edge.csv"
    node_id2iid, _, _ = load_reference_topologies(
        str(INP_EDGE), str(pre_match_edge_fp),
    )
    b, budget = build_bp_budget_direct(
        str(pre_match_edge_fp), str(INP_EDGE),
        str(INP_COM), "combined", node_id2iid,
    )

    from collections import defaultdict
    md_bp = defaultdict(int)
    for s, t in zip(md_edges["source"], md_edges["target"]):
        if s not in node_id2iid or t not in node_id2iid:
            continue
        u, v = node_id2iid[s], node_id2iid[t]
        bu, bv = int(b[u]), int(b[v])
        md_bp[(min(bu, bv), max(bu, bv))] += 1

    for key, cnt in md_bp.items():
        assert cnt <= budget.get(key, 0), (
            f"{gen_name}: bp {key} budget overshoot: "
            f"placed {cnt} > budget {budget.get(key, 0)}"
        )
