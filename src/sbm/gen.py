import logging
import argparse

import numpy as np
import pandas as pd
import graph_tool.all as gt

from pipeline_common import (
    standard_setup,
    timed,
    drop_singleton_clusters,
    load_probs_matrix,
)


def load_inputs(node_id_path, num_clusters_path, assignment_path, degree_path, edge_counts_path):
    node_ids = pd.read_csv(node_id_path, header=None, dtype=str)[0].tolist()
    num_clusters = sum(1 for _ in open(num_clusters_path))
    assignments = pd.read_csv(assignment_path, header=None)[0].to_numpy()
    degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()

    probs = load_probs_matrix(edge_counts_path, num_clusters)

    return node_ids, assignments, degrees, probs.tocsr()


def run_sbm_generation(node_id_path, cluster_id_path, assignment_path, degree_path, edge_counts_path, input_clustering_path, output_dir, seed, n_threads):
    output_dir = standard_setup(output_dir)

    logging.info("Starting SBM Generation...")
    logging.info(f"Seed: {seed} n_threads: {n_threads}")
    np.random.seed(seed)
    gt.seed_rng(seed)
    gt.openmp_set_num_threads(n_threads)

    with timed("Input loading"):
        node_ids, assignments, degrees, probs = load_inputs(
            node_id_path, cluster_id_path, assignment_path, degree_path, edge_counts_path
        )
        logging.info(f"N={len(node_ids)} clusters={probs.shape[0]}")

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

        # generate_sbm returns a multigraph with self-loops; enforce simple-graph invariant.
        gt.remove_parallel_edges(g)
        gt.remove_self_loops(g)

    with timed("Export"):
        edges = [(node_ids[int(s)], node_ids[int(t)]) for s, t in g.iter_edges()]
        pd.DataFrame(edges, columns=["source", "target"]).to_csv(
            output_dir / "edge.csv", index=False
        )
        com_df = pd.read_csv(input_clustering_path)
        com_df = drop_singleton_clusters(com_df)
        com_df.to_csv(output_dir / "com.csv", index=False)

    logging.info("SBM generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="SBM Graph Generator (graph-tool)")
    parser.add_argument("--node-id", type=str, required=True)
    parser.add_argument("--cluster-id", type=str, required=True)
    parser.add_argument("--assignment", type=str, required=True)
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--edge-counts", type=str, required=True)
    parser.add_argument("--input-clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n-threads", type=int, default=1)
    return parser.parse_args()


def main():
    args = parse_args()
    run_sbm_generation(
        args.node_id,
        args.cluster_id,
        args.assignment,
        args.degree,
        args.edge_counts,
        args.input_clustering,
        args.output_folder,
        args.seed,
        args.n_threads,
    )


if __name__ == "__main__":
    main()
