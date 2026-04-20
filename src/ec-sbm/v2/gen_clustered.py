import logging
import argparse
import random

import numpy as np
import pandas as pd

from pipeline_common import (
    standard_setup,
    timed,
    write_edge_tuples_csv,
    load_probs_matrix,
)
from utils import normalize_edge


def generate_cluster(cluster_nodes, k, deg, probs, node2cluster):
    """
    Generate a k-edge-connected subgraph for one cluster.

    Two-phase construction:
      Phase 1 — The first k+1 nodes (sorted by degree descending) form a
        complete graph, guaranteeing k-edge connectivity for the seed set.
      Phase 2 — Each remaining node connects to the k highest-degree already-
        processed nodes (greedy), falling back to degree-weighted random
        sampling from the remainder if fewer than k high-degree candidates
        remain.

    Degree capacity and inter-cluster probability budgets (deg, probs) are
    tracked in-place via ensure_edge_capacity / apply_edge.  When either
    budget is exhausted for a required edge, ensure_edge_capacity inflates
    both budgets by 1 to allow the edge unconditionally.

    Args:
        cluster_nodes: Iterable of node iids belonging to this cluster.
        k: Target edge connectivity (min-cut value from the empirical network).
        deg: Mutable numpy array of per-node remaining degree budgets.
            Updated in-place; reflects consumed capacity after this call.
        probs: Mutable dok_matrix of remaining inter-cluster edge counts.
            Updated in-place.
        node2cluster: Dict mapping node iid to cluster iid.

    Returns:
        Set of (min, max) edge tuples representing intra-cluster edges.
    """
    n = len(cluster_nodes)
    if n == 0 or k == 0:
        return set()
    k = min(k, n - 1)

    int_deg = deg.copy()
    cluster_nodes_ordered = sorted(
        cluster_nodes, key=lambda n_iid: int_deg[n_iid], reverse=True
    )

    processed_nodes = set()
    edges = set()

    def ensure_edge_capacity(u, v):
        """Inflate degree and probability budgets if either is exhausted, so the edge can always be placed."""
        if probs[node2cluster[u], node2cluster[v]] == 0 or int_deg[v] == 0:
            int_deg[u] += 1
            int_deg[v] += 1
            probs[node2cluster[u], node2cluster[v]] += 1
            probs[node2cluster[v], node2cluster[u]] += 1

    def apply_edge(u, v):
        """Record edge (u, v) and decrement both nodes' degree and probability budgets."""
        edges.add(normalize_edge(u, v))
        int_deg[u] -= 1
        int_deg[v] -= 1
        probs[node2cluster[u], node2cluster[v]] -= 1
        probs[node2cluster[v], node2cluster[u]] -= 1

    i = 0
    while i <= k:
        u = cluster_nodes_ordered[i]
        for v in processed_nodes:
            ensure_edge_capacity(u, v)
            apply_edge(u, v)
        processed_nodes.add(u)
        i += 1

    while i < n:
        u = cluster_nodes_ordered[i]
        processed_nodes_ordered = sorted(
            processed_nodes, key=lambda n_iid: int_deg[n_iid], reverse=True
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
            list_cands = list(candidates)
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


def load_inputs(
    node_id_path,
    cluster_id_path,
    assignment_path,
    degree_path,
    mincut_path,
    edge_counts_path,
):
    """
    Load all profile.py outputs and return generation-ready data structures.

    Returns:
        node_id2id: Dict mapping node iid (int) to original string node ID.
        node2cluster: Dict mapping node iid to cluster iid (outliers excluded).
        clustering: Dict mapping cluster iid to list of member node iids.
        deg: numpy array of per-node degree targets (mutable, passed to generate_cluster).
        mcs: numpy array of per-cluster min-cut values (index = cluster iid).
        probs: dok_matrix of inter-cluster edge counts (mutable budget).
    """
    node_id2id = pd.read_csv(node_id_path, header=None, dtype=str)[0].to_dict()
    cluster_id2id = pd.read_csv(cluster_id_path, header=None, dtype=str)[0].to_dict()

    num_clusters = len(cluster_id2id)
    node2cluster = {}
    clustering = {}

    assignment_df = pd.read_csv(assignment_path, header=None)
    for node_iid, c_iid in enumerate(assignment_df[0]):
        if c_iid != -1:
            node2cluster[node_iid] = c_iid
            clustering.setdefault(c_iid, []).append(node_iid)

    deg = pd.read_csv(degree_path, header=None)[0].to_numpy(copy=True)
    mcs = pd.read_csv(mincut_path, header=None)[0].to_numpy(copy=True)

    probs = load_probs_matrix(edge_counts_path, num_clusters)

    return node_id2id, node2cluster, clustering, deg, mcs, probs


def generate_internal_edges(clustering, mcs, deg, probs, node2cluster):
    """Iterate over all clusters and collect edges produced by generate_cluster."""
    edges = set()
    for cluster_iid, cluster_nodes in clustering.items():
        logging.info(
            f"Generating cluster {cluster_iid} (N={len(cluster_nodes)} | k={mcs[cluster_iid]})"
        )
        edges.update(
            generate_cluster(cluster_nodes, mcs[cluster_iid], deg, probs, node2cluster)
        )
    return edges


def export_outputs(output_dir, edges, node_id2id):
    """Write generated edges to edge.csv, mapping iids back to original node IDs."""
    write_edge_tuples_csv(output_dir / "edge.csv", edges, node_id2id)


def run_ecsbm_generation(
    node_id_path,
    cluster_id_path,
    assignment_path,
    degree_path,
    mincut_path,
    edge_counts_path,
    output_dir,
    seed,
):
    """Load profiled inputs, generate intra-cluster edges for all clusters, and export edge.csv."""
    output_dir = standard_setup(output_dir)

    random.seed(seed)
    np.random.seed(seed)

    logging.info("Starting Core Clustered Generation Pipeline...")

    with timed("Input loading"):
        node_id2id, node2cluster, clustering, deg, mcs, probs = load_inputs(
            node_id_path,
            cluster_id_path,
            assignment_path,
            degree_path,
            mincut_path,
            edge_counts_path,
        )

    with timed("Generation of k-edge-connected graphs"):
        edges = generate_internal_edges(clustering, mcs, deg, probs, node2cluster)

    with timed(f"Exporting {len(edges)} internal edges"):
        export_outputs(output_dir, edges, node_id2id)

    logging.info("Core generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="Core Clustered Graph Generator")
    parser.add_argument("--node-id", type=str, required=True)
    parser.add_argument("--cluster-id", type=str, required=True)
    parser.add_argument("--assignment", type=str, required=True)
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--mincut", type=str, required=True)
    parser.add_argument("--edge-counts", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument(
        "--seed",
        type=int,
        default=1,
        help="RNG seed for numpy/random",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    run_ecsbm_generation(
        args.node_id,
        args.cluster_id,
        args.assignment,
        args.degree,
        args.mincut,
        args.edge_counts,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
