"""nPSO profile: builds the inputs npso/gen.py consumes.

Output contract: degree.csv, cluster_sizes.csv, outlier_mode.txt.

nPSO's default outlier policy is `(singleton, drop_oo=false)` — same
cluster_sizes shape as ABCD/LFR. nPSO does not consume a mixing parameter
(the nPSO MATLAB model derives its own cross-cluster behavior from
degree + cluster sizes).

Deps: stdlib + pandas. powerlaw/networkit are deps of npso/gen.py, not
this module.
"""
from __future__ import annotations

import argparse

from params_common import write_params
from pipeline_common import standard_setup, timed
from profile_common import (
    OUTLIER_MODES,
    apply_outlier_mode,
    compute_comm_size,
    compute_node_degree,
    export_comm_size,
    export_degree,
    identify_outliers,
    read_clustering,
    read_edgelist,
)


def setup_inputs(edgelist_path, clustering_path, output_dir,
                 outlier_mode="singleton", drop_outlier_outlier_edges=False):
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
        node_deg_sorted, _ = compute_node_degree(nodes, neighbors)
        comm_size_sorted, _ = compute_comm_size(cluster_counts)

    with timed("Outputs export"):
        export_degree(output_dir, node_deg_sorted)
        export_comm_size(output_dir, comm_size_sorted)
        write_params(
            output_dir,
            outlier_mode=outlier_mode,
            drop_outlier_outlier_edges=drop_outlier_outlier_edges,
        )


def parse_args():
    parser = argparse.ArgumentParser(description="nPSO profile extractor")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument(
        "--outlier-mode", choices=OUTLIER_MODES, default="singleton",
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
