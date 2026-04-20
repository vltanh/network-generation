"""ABCD profile: builds the inputs abcd/gen.py consumes.

Output contract: degree.csv, cluster_sizes.csv, mixing_parameter.txt.

ABCD treats true outliers as singleton clusters — they're folded into
cluster_sizes (size-1 rows) so gen.py doesn't need a separate count.
Mixing parameter uses the global ratio ξ = Σ_out / Σ_total.

Deps: stdlib + pandas (no numpy, scipy, or pymincut).
"""
from __future__ import annotations

import argparse

from pipeline_common import standard_setup, timed
from profile_common import (
    compute_comm_size,
    compute_mixing_parameter,
    compute_node_degree,
    export_cluster_sizes_with_singleton_outliers,
    export_degree,
    export_mixing_param,
    read_clustering,
    read_edgelist,
)


def setup_inputs(edgelist_path, clustering_path, output_dir):
    output_dir = standard_setup(output_dir)

    with timed("Input reading"):
        nodes, node2com, cluster_counts = read_clustering(clustering_path)
        nodes, neighbors = read_edgelist(edgelist_path, nodes)

    with timed("Mappings computation"):
        node_deg_sorted, _ = compute_node_degree(nodes, neighbors)
        comm_size_sorted, _ = compute_comm_size(cluster_counts)

    n_outliers = sum(1 for u in nodes if u not in node2com)

    with timed("Outputs export"):
        export_degree(output_dir, node_deg_sorted)
        export_cluster_sizes_with_singleton_outliers(
            output_dir, comm_size_sorted, n_outliers,
        )
        mixing_param = compute_mixing_parameter(
            nodes, neighbors, node2com, "abcd",
        )
        export_mixing_param(output_dir, mixing_param)


def parse_args():
    parser = argparse.ArgumentParser(description="ABCD profile extractor")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    setup_inputs(args.edgelist, args.clustering, args.output_folder)


if __name__ == "__main__":
    main()
