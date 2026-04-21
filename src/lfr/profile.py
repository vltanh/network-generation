"""LFR profile: builds the inputs lfr/gen.py consumes.

Output contract: degree.csv, cluster_sizes.csv, mixing_parameter.txt.

LFR's default outlier policy is `(singleton, drop_oo=false)` — same
cluster_sizes shape as ABCD. Mixing parameter is the mean of per-node µ_i
(not the global ratio). numpy is pulled in lazily by
compute_mixing_parameter's mean branch.

CLI precedence: individual flags (``--outlier-mode``/...) win over
``--params-file`` when both are given. The pipeline writes params.txt
itself and passes only ``--params-file``; standalone users pass per-knob
flags directly.

Deps: stdlib + pandas at load time; numpy during setup_inputs.
"""
from __future__ import annotations

import argparse

from params_common import _parse_bool, read_params, resolve_param
from pipeline_common import standard_setup, timed
from profile_common import (
    OUTLIER_MODES,
    apply_outlier_mode,
    compute_comm_size,
    compute_mixing_parameter,
    compute_node_degree,
    export_comm_size,
    export_degree,
    export_mixing_param,
    identify_outliers,
    read_clustering,
    read_edgelist,
)


DEFAULT_OUTLIER_MODE = "singleton"
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
        node_deg_sorted, _ = compute_node_degree(nodes, neighbors)
        comm_size_sorted, _ = compute_comm_size(cluster_counts)

    with timed("Outputs export"):
        export_degree(output_dir, node_deg_sorted)
        export_comm_size(output_dir, comm_size_sorted)
        mixing_param = compute_mixing_parameter(
            nodes, neighbors, node2com, reduction="mean",
        )
        export_mixing_param(output_dir, mixing_param)


def parse_args():
    parser = argparse.ArgumentParser(description="LFR profile extractor")
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
