"""ABCD+o profile: builds the inputs abcd+o/gen.py consumes.

Output contract: degree.csv, cluster_sizes.csv, mixing_parameter.txt,
n_outliers.txt.

ABCD+o's default outlier policy is `(singleton, drop_oo=true)` — outlier-
outlier edges are dropped from both mu and degree accounting, since the
Julia sampler physically cannot produce them. Mixing parameter uses the
global ratio ξ over the post-drop graph. cluster_sizes.csv carries real
clusters only; gen.py prepends n_outliers as the mega-cluster itself.

CLI precedence: individual flags (``--outlier-mode``/...) win over
``--params-file`` when both are given. The pipeline writes params.txt
itself and passes only ``--params-file``; standalone users pass per-knob
flags directly.

Deps: stdlib + pandas (no numpy, scipy, or pymincut).
"""
from __future__ import annotations

import argparse

from params_common import _parse_bool, read_params, resolve_param
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


DEFAULT_OUTLIER_MODE = "singleton"
DEFAULT_DROP_OO = True


def _is_outlier_cluster(cid):
    """Sentinel cluster ids inserted by apply_outlier_mode under singleton
    (`__outlier_<nodeid>__`) or combined (`__outliers__`). Filtered out of
    cluster_sizes.csv so gen.py consumes real clusters only."""
    return cid == COMBINED_OUTLIER_CLUSTER_ID or (
        isinstance(cid, str) and cid.startswith("__outlier_")
    )


def setup_inputs(edgelist_path, clustering_path, output_dir,
                 outlier_mode=DEFAULT_OUTLIER_MODE,
                 drop_outlier_outlier_edges=DEFAULT_DROP_OO):
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


def parse_args():
    parser = argparse.ArgumentParser(description="ABCD+o profile extractor")
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
