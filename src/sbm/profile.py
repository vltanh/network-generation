"""SBM profile: builds the inputs sbm/gen.py consumes.

Output contract (all written under --output-folder):
    node_id.csv, cluster_id.csv, assignment.csv, degree.csv, edge_counts.csv.

SBM's default outlier policy is `(combined, drop_oo=false)` — every true
outlier folds into one mega-cluster (`__outliers__`) so every edge
(including outlier-outlier and clustered-outlier) is routed through the
same block structure as the rest of the network.

CLI precedence: individual flags (``--outlier-mode``/...) win over
``--params-file`` when both are given. The pipeline writes params.txt
itself and passes only ``--params-file``; standalone users pass per-knob
flags directly.

Deps: stdlib + pandas (via profile_common + pipeline_common).
"""
from __future__ import annotations

import argparse

from params_common import _parse_bool, read_params, resolve_param
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
    identify_outliers,
    read_clustering,
    read_edgelist,
)


DEFAULT_OUTLIER_MODE = "combined"
DEFAULT_DROP_OO = False


def setup_inputs(edgelist_path, clustering_path, output_dir,
                 outlier_mode=DEFAULT_OUTLIER_MODE,
                 drop_outlier_outlier_edges=DEFAULT_DROP_OO):
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


def parse_args():
    parser = argparse.ArgumentParser(description="SBM profile extractor")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument(
        "--params-file", type=str, default=None,
        help="params.txt to read stage knobs from; CLI flags override."
    )
    parser.add_argument(
        "--outlier-mode", choices=OUTLIER_MODES, default=None,
    )
    oo = parser.add_mutually_exclusive_group()
    oo.add_argument("--drop-outlier-outlier-edges",
                    dest="drop_oo", action="store_true", default=None)
    oo.add_argument("--keep-outlier-outlier-edges",
                    dest="drop_oo", action="store_false")
    return parser.parse_args()


def main():
    args = parse_args()
    file_params = read_params(args.params_file) if args.params_file else None
    outlier_mode = resolve_param(
        args.outlier_mode, file_params, "outlier_mode",
        default=DEFAULT_OUTLIER_MODE,
    )
    drop_oo = resolve_param(
        args.drop_oo, file_params, "drop_outlier_outlier_edges",
        default=DEFAULT_DROP_OO, parser=_parse_bool,
    )
    setup_inputs(
        args.edgelist, args.clustering, args.output_folder,
        outlier_mode=outlier_mode,
        drop_outlier_outlier_edges=drop_oo,
    )


if __name__ == "__main__":
    main()
