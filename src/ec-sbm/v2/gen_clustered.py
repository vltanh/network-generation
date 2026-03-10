import time
import logging
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import dok_matrix

from utils import setup_logging


def create_edge(u, v):
    return (min(u, v), max(u, v))


def generate_cluster(cluster_nodes, k, deg, probs, node2cluster):
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
        if probs[node2cluster[u], node2cluster[v]] == 0 or int_deg[v] == 0:
            int_deg[u] += 1
            int_deg[v] += 1
            probs[node2cluster[u], node2cluster[v]] += 1
            probs[node2cluster[v], node2cluster[u]] += 1

    def apply_edge(u, v):
        edges.add(create_edge(u, v))
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

    probs = dok_matrix((num_clusters, num_clusters), dtype=int)
    try:
        edge_counts_df = pd.read_csv(
            edge_counts_path, header=None, names=["r", "c", "w"]
        )
        for _, row in edge_counts_df.iterrows():
            probs[int(row["r"]), int(row["c"])] = int(row["w"])
    except pd.errors.EmptyDataError:
        logging.warning(
            f"Edge counts file ({edge_counts_path}) is empty. Assuming completely disconnected clusters."
        )

    return node_id2id, node2cluster, clustering, deg, mcs, probs


def generate_internal_edges(clustering, mcs, deg, probs, node2cluster):
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
    if not edges:
        edge_out_df = pd.DataFrame(columns=["source", "target"])
    else:
        edge_out_df = pd.DataFrame(
            [(node_id2id[src], node_id2id[tgt]) for src, tgt in edges],
            columns=["source", "target"],
        )
    edge_out_df.to_csv(Path(output_dir) / "edge.csv", index=False)


def run_ecsbm_generation(
    node_id_path,
    cluster_id_path,
    assignment_path,
    degree_path,
    mincut_path,
    edge_counts_path,
    output_dir,
):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    setup_logging(Path(output_dir) / "run.log")

    logging.info("Starting Core Clustered Generation Pipeline...")

    start = time.perf_counter()
    node_id2id, node2cluster, clustering, deg, mcs, probs = load_inputs(
        node_id_path,
        cluster_id_path,
        assignment_path,
        degree_path,
        mincut_path,
        edge_counts_path,
    )
    logging.info(f"Input loading elapsed: {time.perf_counter() - start:.4f} seconds")

    start = time.perf_counter()
    edges = generate_internal_edges(clustering, mcs, deg, probs, node2cluster)
    logging.info(
        f"Generation of k-edge-connected graphs elapsed: {time.perf_counter() - start:.4f} seconds"
    )

    start = time.perf_counter()
    export_outputs(output_dir, edges, node_id2id)
    logging.info(
        f"Exporting {len(edges)} internal edges elapsed: {time.perf_counter() - start:.4f} seconds"
    )
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
    )


if __name__ == "__main__":
    main()
