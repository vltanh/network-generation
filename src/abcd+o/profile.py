"""ABCD+o profile: builds the inputs abcd+o/gen.py consumes.

Output contract: degree.csv, cluster_sizes.csv, mixing_parameter.txt,
n_outliers.txt.

ABCD+o forbids outlier-outlier edges, so each outlier's reported degree
is its count of *clustered* neighbors only (not its total degree in the
input graph).  Mixing parameter uses the global ratio ξ = Σ_out / Σ_total
but drops outlier-outlier edges from both numerator and denominator.

Deps: stdlib + pandas (no numpy, scipy, or pymincut).
"""
from __future__ import annotations

import argparse

from pipeline_common import standard_setup, timed
from profile_common import (
    compute_comm_size,
    compute_mixing_parameter,
    compute_node_degree,
    export_comm_size,
    export_degree,
    export_mixing_param,
    export_n_outliers,
    read_clustering,
    read_edgelist,
)


def _adjusted_degree_sorted(node_deg_sorted, nodes, neighbors, node2com):
    """Replace each outlier's degree with its count of clustered neighbors."""
    outlier_degrees = {
        u: sum(1 for v in neighbors[u] if v in node2com)
        for u in nodes if u not in node2com
    }
    return sorted(
        (
            (u, outlier_degrees[u] if u in outlier_degrees else d)
            for u, d in node_deg_sorted
        ),
        key=lambda x: x[1], reverse=True,
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
        adjusted_deg = _adjusted_degree_sorted(
            node_deg_sorted, nodes, neighbors, node2com,
        )
        export_degree(output_dir, adjusted_deg)
        export_comm_size(output_dir, comm_size_sorted)
        export_n_outliers(output_dir, n_outliers)
        mixing_param = compute_mixing_parameter(
            nodes, neighbors, node2com, "abcd+o",
        )
        export_mixing_param(output_dir, mixing_param)


def parse_args():
    parser = argparse.ArgumentParser(description="ABCD+o profile extractor")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    setup_inputs(args.edgelist, args.clustering, args.output_folder)


if __name__ == "__main__":
    main()
