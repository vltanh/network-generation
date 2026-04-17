import time
import logging
import argparse
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import graph_tool.all as gt
from scipy.sparse import dok_matrix

from utils import setup_logging


OUTLIER_CLUSTER_ID = "__outliers__"


def profile_inputs(edgelist_path, clustering_path):
    """
    Read edgelist + clustering and produce SBM inputs, treating all outliers
    (nodes absent from the clustering) as a single synthetic cluster.
    """
    clustering_df = pd.read_csv(clustering_path, usecols=[0, 1], dtype=str).dropna()
    node2com = dict(zip(clustering_df.iloc[:, 0], clustering_df.iloc[:, 1]))

    neighbors = defaultdict(set)
    nodes = set(node2com.keys())
    edge_df = pd.read_csv(edgelist_path, usecols=[0, 1], dtype=str).dropna()
    for u, v in zip(edge_df.iloc[:, 0], edge_df.iloc[:, 1]):
        if u != v:
            neighbors[u].add(v)
            neighbors[v].add(u)
            nodes.add(u)
            nodes.add(v)

    # Assign every node without a real cluster to the outlier mega-cluster.
    has_outliers = any(u not in node2com for u in nodes)
    if has_outliers:
        for u in nodes:
            if u not in node2com:
                node2com[u] = OUTLIER_CLUSTER_ID

    cluster_counts = defaultdict(int)
    for c in node2com.values():
        cluster_counts[c] += 1

    comm_size_sorted = sorted(cluster_counts.items(), reverse=True, key=lambda x: x[1])
    cluster_ids = [c for c, _ in comm_size_sorted]
    cluster_id2iid = {c: i for i, c in enumerate(cluster_ids)}

    node_degree_sorted = sorted(
        ((u, len(neighbors[u])) for u in nodes), reverse=True, key=lambda x: x[1]
    )
    node_ids = [u for u, _ in node_degree_sorted]
    degrees = np.array([d for _, d in node_degree_sorted], dtype=int)
    assignments = np.array(
        [cluster_id2iid[node2com[u]] for u in node_ids], dtype=int
    )

    num_clusters = len(cluster_ids)
    probs = dok_matrix((num_clusters, num_clusters), dtype=int)
    for u in nodes:
        cu = cluster_id2iid[node2com[u]]
        for v in neighbors[u]:
            cv = cluster_id2iid[node2com[v]]
            probs[cu, cv] += 1

    logging.info(f"Profiled {len(node_ids)} nodes, {num_clusters} clusters.")
    if has_outliers:
        outlier_iid = cluster_id2iid[OUTLIER_CLUSTER_ID]
        logging.info(
            f"Outlier mega-cluster iid={outlier_iid}, size={cluster_counts[OUTLIER_CLUSTER_ID]}."
        )

    return node_ids, cluster_ids, assignments, degrees, probs.tocsr()


def run_sbm_generation(edgelist_path, clustering_path, output_dir, seed):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    setup_logging(Path(output_dir) / "run.log")

    logging.info("Starting SBM Generation...")
    logging.info(f"Seed: {seed}")
    np.random.seed(seed)
    gt.seed_rng(seed)

    start = time.perf_counter()
    node_ids, cluster_ids, assignments, degrees, probs = profile_inputs(
        edgelist_path, clustering_path
    )
    logging.info(f"Profiling elapsed: {time.perf_counter() - start:.4f} seconds")

    start = time.perf_counter()
    if degrees.sum() > 0:
        g = gt.generate_sbm(
            assignments,
            probs,
            out_degs=degrees,
            micro_ers=True,
            micro_degs=True,
            directed=False,
        )
    else:
        g = gt.Graph(directed=False)
    logging.info(f"Generation elapsed: {time.perf_counter() - start:.4f} seconds")

    start = time.perf_counter()
    edges = [(node_ids[int(src)], node_ids[int(tgt)]) for src, tgt in g.iter_edges()]
    pd.DataFrame(edges, columns=["source", "target"]).to_csv(
        Path(output_dir) / "edge.csv", index=False
    )

    # com.csv preserves original cluster ids; outliers remain unclustered.
    com_rows = [
        (node_ids[i], cluster_ids[int(assignments[i])])
        for i in range(len(node_ids))
        if cluster_ids[int(assignments[i])] != OUTLIER_CLUSTER_ID
    ]
    pd.DataFrame(com_rows, columns=["node_id", "cluster_id"]).to_csv(
        Path(output_dir) / "com.csv", index=False
    )
    logging.info(f"Export elapsed: {time.perf_counter() - start:.4f} seconds")
    logging.info("SBM generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="SBM Graph Generator (graph-tool)")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    run_sbm_generation(
        args.edgelist,
        args.clustering,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
