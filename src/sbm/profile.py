"""SBM profile: builds the inputs sbm/gen.py consumes.

Output contract (all written under --output-folder):
    node_id.csv, cluster_id.csv, assignment.csv, degree.csv, edge_counts.csv

SBM folds true outliers into one mega-cluster so every edge (including
outlier-outlier and clustered-outlier) is routed through the same block
structure as the rest of the network.

Deps: stdlib + pandas (via profile_common + pipeline_common).
"""
from __future__ import annotations

import argparse

from pipeline_common import standard_setup, timed
from profile_common import (
    compute_comm_size,
    compute_edge_count,
    compute_node_degree,
    export_assignment,
    export_cluster_id,
    export_degree,
    export_edge_count,
    export_node_id,
    read_clustering,
    read_edgelist,
)

OUTLIER_CLUSTER_ID = "__outliers__"


def _fold_outliers_into_mega_cluster(nodes, node2com, cluster_counts):
    """SBM routes outlier edges through the same block structure as the rest
    of the network by folding true outliers into one mega-cluster."""
    outliers = [u for u in nodes if u not in node2com]
    if outliers:
        for u in outliers:
            node2com[u] = OUTLIER_CLUSTER_ID
        cluster_counts[OUTLIER_CLUSTER_ID] = len(outliers)


def setup_inputs(edgelist_path, clustering_path, output_dir):
    output_dir = standard_setup(output_dir)

    with timed("Input reading"):
        nodes, node2com, cluster_counts = read_clustering(clustering_path)
        nodes, neighbors = read_edgelist(edgelist_path, nodes)

    _fold_outliers_into_mega_cluster(nodes, node2com, cluster_counts)

    with timed("Mappings computation"):
        node_deg_sorted, _node_id2iid = compute_node_degree(nodes, neighbors)
        comm_size_sorted, cluster_id2iid = compute_comm_size(cluster_counts)

    with timed("Outputs export"):
        export_node_id(output_dir, node_deg_sorted)
        export_cluster_id(output_dir, comm_size_sorted)
        export_assignment(output_dir, node_deg_sorted, node2com, cluster_id2iid)
        export_degree(output_dir, node_deg_sorted)
        edge_counts = compute_edge_count(
            nodes, neighbors, node2com, cluster_id2iid,
        )
        export_edge_count(output_dir, edge_counts)


def parse_args():
    parser = argparse.ArgumentParser(description="SBM profile extractor")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    setup_inputs(args.edgelist, args.clustering, args.output_folder)


if __name__ == "__main__":
    main()
