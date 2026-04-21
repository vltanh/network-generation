"""ABCD+o profile: builds the inputs abcd+o/gen.py consumes.

Output contract: degree.csv, cluster_sizes.csv, mixing_parameter.txt,
n_outliers.txt, params.txt.

ABCD+o's default outlier policy is `(singleton, drop_oo=true)` — outlier-
outlier edges are dropped from both mu and degree accounting, since the
Julia sampler physically cannot produce them. Mixing parameter uses the
global ratio ξ over the post-drop graph. cluster_sizes.csv carries real
clusters only; gen.py prepends n_outliers as the mega-cluster itself.

Deps: stdlib + pandas (no numpy, scipy, or pymincut).
"""
from __future__ import annotations

import argparse

from params_common import write_params
from pipeline_common import standard_setup, timed
from profile_common import (
    COMBINED_OUTLIER_CLUSTER_ID,
    OUTLIER_MODES,
    apply_outlier_mode,
    compute_comm_size,
    compute_mixing_parameter,
    compute_node_degree,
    export_comm_size,
    export_degree,
    export_mixing_param,
    export_n_outliers,
    identify_outliers,
    read_clustering,
    read_edgelist,
)


def _is_outlier_cluster(cid):
    """Sentinel cluster ids inserted by apply_outlier_mode under singleton
    (`__outlier_<nodeid>__`) or combined (`__outliers__`). Filtered out of
    cluster_sizes.csv so gen.py consumes real clusters only."""
    return cid == COMBINED_OUTLIER_CLUSTER_ID or (
        isinstance(cid, str) and cid.startswith("__outlier_")
    )


def setup_inputs(edgelist_path, clustering_path, output_dir,
                 outlier_mode="singleton", drop_outlier_outlier_edges=True):
    output_dir = standard_setup(output_dir)

    with timed("Input reading"):
        nodes, node2com, cluster_counts = read_clustering(clustering_path)
        nodes, neighbors = read_edgelist(edgelist_path, nodes)

    with timed("Outlier transform"):
        outliers = identify_outliers(nodes, node2com, cluster_counts)
        n_outliers = len(outliers)
        apply_outlier_mode(
            nodes, node2com, cluster_counts, neighbors, outliers,
            mode=outlier_mode,
            drop_outlier_outlier_edges=drop_outlier_outlier_edges,
        )

    with timed("Mappings computation"):
        node_deg_sorted, _ = compute_node_degree(nodes, neighbors)
        comm_size_sorted, _ = compute_comm_size(cluster_counts)
        # gen.py expects cluster_sizes.csv to carry real clusters only; it
        # prepends n_outliers as the mega-cluster itself. Strip the sentinel
        # ids apply_outlier_mode may have added so gen is a straight pass-through.
        real_clusters_sorted = [
            (cid, sz) for cid, sz in comm_size_sorted if not _is_outlier_cluster(cid)
        ]

    with timed("Outputs export"):
        export_degree(output_dir, node_deg_sorted)
        export_comm_size(output_dir, real_clusters_sorted)
        export_n_outliers(output_dir, n_outliers)
        mixing_param = compute_mixing_parameter(
            nodes, neighbors, node2com, reduction="global",
        )
        export_mixing_param(output_dir, mixing_param)
        write_params(
            output_dir,
            outlier_mode=outlier_mode,
            drop_outlier_outlier_edges=drop_outlier_outlier_edges,
        )


def parse_args():
    parser = argparse.ArgumentParser(description="ABCD+o profile extractor")
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
    parser.set_defaults(drop_oo=True)
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
