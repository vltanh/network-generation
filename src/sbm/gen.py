import logging
import argparse

import numpy as np
import pandas as pd
import graph_tool.all as gt
from scipy.sparse import dok_matrix

from pipeline_common import standard_setup, timed
from profile import SBM_OUTLIER_CLUSTER_ID


def load_inputs(node_id_path, cluster_id_path, assignment_path, degree_path, edge_counts_path):
    node_ids = pd.read_csv(node_id_path, header=None, dtype=str)[0].tolist()
    cluster_ids = pd.read_csv(cluster_id_path, header=None, dtype=str)[0].tolist()
    assignments = pd.read_csv(assignment_path, header=None)[0].to_numpy()
    degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()

    num_clusters = len(cluster_ids)
    probs = dok_matrix((num_clusters, num_clusters), dtype=int)
    try:
        ec = pd.read_csv(edge_counts_path, header=None, names=["r", "c", "w"])
        for _, row in ec.iterrows():
            probs[int(row["r"]), int(row["c"])] = int(row["w"])
    except pd.errors.EmptyDataError:
        logging.warning(f"{edge_counts_path} is empty. Generating a disconnected graph.")

    return node_ids, cluster_ids, assignments, degrees, probs.tocsr()


def run_sbm_generation(node_id_path, cluster_id_path, assignment_path, degree_path, edge_counts_path, output_dir, seed):
    output_dir = standard_setup(output_dir)

    logging.info("Starting SBM Generation...")
    logging.info(f"Seed: {seed}")
    np.random.seed(seed)
    gt.seed_rng(seed)

    with timed("Input loading"):
        node_ids, cluster_ids, assignments, degrees, probs = load_inputs(
            node_id_path, cluster_id_path, assignment_path, degree_path, edge_counts_path
        )
        logging.info(f"N={len(node_ids)} clusters={len(cluster_ids)}")

    with timed("Generation"):
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

    with timed("Export"):
        edges = [(node_ids[int(s)], node_ids[int(t)]) for s, t in g.iter_edges()]
        pd.DataFrame(edges, columns=["source", "target"]).to_csv(
            output_dir / "edge.csv", index=False
        )

        com_rows = [
            (node_ids[i], cluster_ids[int(assignments[i])])
            for i in range(len(node_ids))
            if cluster_ids[int(assignments[i])] != SBM_OUTLIER_CLUSTER_ID
        ]
        pd.DataFrame(com_rows, columns=["node_id", "cluster_id"]).to_csv(
            output_dir / "com.csv", index=False
        )

    logging.info("SBM generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="SBM Graph Generator (graph-tool)")
    parser.add_argument("--node-id", type=str, required=True)
    parser.add_argument("--cluster-id", type=str, required=True)
    parser.add_argument("--assignment", type=str, required=True)
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--edge-counts", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    run_sbm_generation(
        args.node_id,
        args.cluster_id,
        args.assignment,
        args.degree,
        args.edge_counts,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
