"""Instrumented canonical runner for ec_sbm stage-2 (gen_kec_core).

Wraps ``np.random.choice`` to log every (sorted-candidate-list, returned
index, returned value) tuple emitted by ``gen_kec_core.generate_cluster``.
The constructive core is the only randomized site of stage 2 with
``--no-sbm-overlay`` (v2): phase 1's K_{k+1} is deterministic, phase 2
prefers degree-sorted processed nodes deterministically, and only
falls back to weighted random sampling when the processed-node
pool can't supply enough non-zero-degree partners. We log only that
fallback site.

CLI: reads ``{profile_dir, seed}`` on stdin, writes
``{edges, trace, deg_final}`` on stdout.

The JS port at ``tools/viz_check/ec_sbm/kernel_check.mjs`` replays the
trace by reading ``trace[i].idx`` instead of doing any sampling.
"""
from __future__ import annotations

import json
import random as _random
import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[4]
EC_SBM_PY = REPO_ROOT / "externals" / "ec-sbm" / "src"
SHARED_PY = REPO_ROOT / "src"
sys.path.insert(0, str(EC_SBM_PY))
sys.path.insert(0, str(SHARED_PY))

import gen_kec_core as _kec  # noqa: E402


class TraceRecorder:
    """Record np.random.choice calls and the per-cluster processed-set
    iteration orders. The latter is needed because Python set iteration
    order under PYTHONHASHSEED=0 is hash-dependent and not portable to JS.
    """

    def __init__(self):
        self.trace = []
        self._real_choice = np.random.choice

    def install(self):
        np.random.choice = self._choice

    def restore(self):
        np.random.choice = self._real_choice

    def record_set_order(self, processed_nodes):
        self.trace.append({
            "site": "set_iter",
            "order": [int(v) for v in processed_nodes],
        })

    def _choice(self, a, *args, **kwargs):
        result = self._real_choice(a, *args, **kwargs)
        if hasattr(a, "__len__"):
            cands = list(a)
        else:
            cands = list(range(int(a)))
        rv = int(result)
        idx = cands.index(rv)
        self.trace.append({
            "site": "np_choice",
            "n": len(cands),
            "idx": idx,
            "value": rv,
        })
        return result


def _instrumented_generate_cluster(cluster_nodes, k, deg, probs, node2cluster,
                                   recorder):
    """Mirror of gen_kec_core.generate_cluster with set-iteration logging.

    Logic is line-for-line identical to canonical; the only addition is a
    `recorder.record_set_order(processed_nodes)` call before each set
    iteration so the JS port can replay the exact order.
    """
    from graph_utils import normalize_edge

    n = len(cluster_nodes)
    if n == 0 or k == 0:
        return set()
    k = min(k, n - 1)

    int_deg = deg.copy()
    cluster_nodes_ordered = sorted(
        cluster_nodes, key=lambda n_iid: (-int_deg[n_iid], n_iid)
    )

    processed_nodes = set()
    edges = set()

    def ensure_edge_capacity(u, v):
        if probs[node2cluster[u], node2cluster[v]] == 0 or int_deg[v] == 0:
            int_deg[u] += 1
            int_deg[v] += 1
            probs[node2cluster[u], node2cluster[v]] += 1
            probs[node2cluster[v], node2cluster[u]] += 1

    def apply_edge(u, v):
        edges.add(normalize_edge(u, v))
        int_deg[u] -= 1
        int_deg[v] -= 1
        probs[node2cluster[u], node2cluster[v]] -= 1
        probs[node2cluster[v], node2cluster[u]] -= 1

    i = 0
    while i <= k:
        u = cluster_nodes_ordered[i]
        recorder.record_set_order(processed_nodes)
        for v in processed_nodes:
            ensure_edge_capacity(u, v)
            apply_edge(u, v)
        processed_nodes.add(u)
        i += 1

    while i < n:
        u = cluster_nodes_ordered[i]
        processed_nodes_ordered = sorted(
            processed_nodes, key=lambda n_iid: (-int_deg[n_iid], n_iid)
        )
        candidates = set(processed_nodes)

        ii, iii = 0, 0
        while ii < k and iii < len(processed_nodes_ordered):
            v = processed_nodes_ordered[iii]
            iii += 1
            ensure_edge_capacity(u, v)
            if int_deg[v] == 0:
                continue
            apply_edge(u, v)
            candidates.remove(v)
            ii += 1

        while ii < k:
            list_cands = sorted(candidates)
            deg_sum = deg[list_cands].sum()
            weights = (
                deg[list_cands] / deg_sum
                if deg_sum > 0
                else np.ones(len(list_cands)) / len(list_cands)
            )
            v = np.random.choice(list_cands, p=weights)
            ensure_edge_capacity(u, v)
            apply_edge(u, v)
            candidates.remove(v)
            ii += 1

        processed_nodes.add(u)
        i += 1

    deg[:] = int_deg[:]
    return edges


def _instrumented_generate_internal_edges(clustering, mcs, deg, probs,
                                          node2cluster, recorder):
    edges = set()
    for cluster_iid, cluster_nodes in clustering.items():
        edges.update(
            _instrumented_generate_cluster(
                cluster_nodes, mcs[cluster_iid], deg, probs, node2cluster,
                recorder,
            )
        )
    return edges


def run_canonical(profile_dir: str, seed: int) -> dict:
    profile_dir = Path(profile_dir)
    node_id2id, node2cluster, clustering, deg, mcs, probs = _kec.load_inputs(
        profile_dir / "node_id.csv",
        profile_dir / "cluster_id.csv",
        profile_dir / "assignment.csv",
        profile_dir / "degree.csv",
        profile_dir / "mincut.csv",
        profile_dir / "edge_counts.csv",
    )

    _random.seed(seed)
    np.random.seed(seed)

    recorder = TraceRecorder()
    recorder.install()
    try:
        edges = _instrumented_generate_internal_edges(
            clustering, mcs, deg, probs, node2cluster, recorder,
        )
    finally:
        recorder.restore()

    edge_list = sorted((int(u), int(v)) for u, v in edges)
    return {
        "edges": edge_list,
        "deg_final": [int(x) for x in deg.tolist()],
        "trace": recorder.trace,
    }


def main():
    job = json.loads(sys.stdin.read())
    out = run_canonical(job["profile_dir"], int(job["seed"]))
    sys.stdout.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
