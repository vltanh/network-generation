"""LFR profile: builds the inputs lfr/gen.py consumes.

Output contract: degree.csv, cluster_sizes.csv, mixing_parameter.txt.

LFR treats true outliers implicitly as singletons (same cluster_sizes
shape as ABCD).  Mixing parameter is the mean of per-node µ_i (not the
global ratio).  numpy is pulled in by compute_mixing_parameter's lfr
branch only — lazy-imported, so this module stays numpy-free at load time.

Deps: stdlib + pandas at load time; numpy during setup_inputs (for the
mixing parameter).  powerlaw is a dep of lfr/gen.py, not this module.
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
            nodes, neighbors, node2com, "lfr",
        )
        export_mixing_param(output_dir, mixing_param)


def parse_args():
    parser = argparse.ArgumentParser(description="LFR profile extractor")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    setup_inputs(args.edgelist, args.clustering, args.output_folder)


if __name__ == "__main__":
    main()
