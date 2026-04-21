"""SBM profile: builds the inputs sbm/gen.py consumes.

Output contract (all written under --output-folder):
    node_id.csv, cluster_id.csv, assignment.csv, degree.csv,
    edge_counts.csv, outlier_mode.txt

SBM's default outlier policy is `(combined, drop_oo=false)` — every true
outlier folds into one mega-cluster (`__outliers__`) so every edge
(including outlier-outlier and clustered-outlier) is routed through the
same block structure as the rest of the network.

Deps: stdlib + pandas (via profile_common + pipeline_common).
"""
from __future__ import annotations

import argparse

from pipeline_common import standard_setup, timed
from profile_common import (
    OUTLIER_MODES,
    apply_outlier_mode,
    compute_comm_size,
    compute_edge_count,
    compute_node_degree,
    export_assignment,
    export_cluster_id,
    export_degree,
    export_edge_count,
    export_node_id,
    export_outlier_mode,
    identify_outliers,
    read_clustering,
    read_edgelist,
)


def setup_inputs(edgelist_path, clustering_path, output_dir,
                 outlier_mode="combined", drop_outlier_outlier_edges=False):
    output_dir = standard_setup(output_dir)

    with timed("Input reading"):
        nodes, node2com, cluster_counts = read_clustering(clustering_path)
        nodes, neighbors = read_edgelist(edgelist_path, nodes)

    with timed("Outlier transform"):
        outliers = identify_outliers(nodes, node2com, cluster_counts)
        apply_outlier_mode(
            nodes, node2com, cluster_counts, neighbors, outliers,
            mode=outlier_mode,
            drop_outlier_outlier_edges=drop_outlier_outlier_edges,
        )

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
        export_outlier_mode(output_dir, outlier_mode, drop_outlier_outlier_edges)


def parse_args():
    parser = argparse.ArgumentParser(description="SBM profile extractor")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument(
        "--outlier-mode", choices=OUTLIER_MODES, default="combined",
    )
    oo = parser.add_mutually_exclusive_group()
    oo.add_argument("--drop-outlier-outlier-edges",
                    dest="drop_oo", action="store_true")
    oo.add_argument("--keep-outlier-outlier-edges",
                    dest="drop_oo", action="store_false")
    parser.set_defaults(drop_oo=False)
    return parser.parse_args()


def main():
    args = parse_args()
    setup_inputs(
        args.edgelist, args.clustering, args.output_folder,
        outlier_mode=args.outlier_mode,
        drop_outlier_outlier_edges=args.drop_oo,
    )


if __name__ == "__main__":
    main()
