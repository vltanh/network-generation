"""nPSO profile.

Outputs:
  - degree.csv          per-node degree, sorted desc (downstream match_degree).
  - cluster_sizes.csv   per-cluster size, sorted desc.
  - derived.txt         scalar + vector inputs gen.py consumes.

`derived.txt` is the minimal set of things gen.py needs from the original
network: N, m, gamma, c, target_ccoeff, mixing_proportions. It uses the
same `key=value` shape as params.txt so the shell pipeline can read it
with a plain while-loop. gen.py itself doesn't know about this file: its
CLI takes each scalar as a separate flag, so a user with values from
somewhere else can invoke gen.py without ever running profile.py.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import powerlaw
import networkit as nk

from params_common import _parse_bool, read_params, resolve_param
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


DEFAULT_OUTLIER_MODE = "singleton"
DEFAULT_DROP_OO = False

DERIVED_FILENAME = "derived.txt"


def _compute_global_ccoeff(edgelist_path):
    """Exact global clustering coefficient of the simplified edgelist."""
    elr = nk.graphio.EdgeListReader(",", 1, continuous=False, directed=False)
    g = elr.read(str(edgelist_path))
    g.removeMultiEdges()
    g.removeSelfLoops()
    return float(nk.globals.ClusteringCoefficient.exactGlobal(g))


def _fit_gamma(degrees):
    """Hill-MLE power-law exponent, floored at 2.0 (nPSO's lower bound)."""
    alpha = powerlaw.Fit(degrees, discrete=True, verbose=False).power_law.alpha
    return float(max(alpha, 2.0))


def _mixing_proportions(comm_size_sorted):
    """rho_k = size_k / sum(size). Order matches comm_size_sorted (size desc)."""
    total = sum(sz for _, sz in comm_size_sorted)
    if total == 0:
        return []
    return [sz / total for _, sz in comm_size_sorted]


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

    with timed("Derived params computation"):
        degrees = np.array([deg for _, deg in node_deg_sorted])
        N = int(len(node_deg_sorted))
        if N > 0:
            m = max(1, int(round(float(np.mean(degrees)) / 2)))
            gamma = _fit_gamma(degrees)
        else:
            m = 1
            gamma = 2.0
        c = int(len(comm_size_sorted))
        target_ccoeff = _compute_global_ccoeff(edgelist_path)
        rho = _mixing_proportions(comm_size_sorted)

    with timed("Outputs export"):
        export_degree(output_dir, node_deg_sorted)
        export_comm_size(output_dir, comm_size_sorted)
        _export_derived(output_dir, N, m, gamma, c, target_ccoeff, rho)


def _export_derived(out_dir, N, m, gamma, c, target_ccoeff, rho):
    """Write the key=value file gen.py's pipeline wrapper unpacks.

    Same shape as params.txt (one `key=value` per line, sorted). The
    `mixing_proportions` value is a comma-separated list of floats so
    the whole contract fits in the plain key=value format and bash can
    read it with a while-loop.
    """
    rho_csv = ",".join(repr(float(x)) for x in rho)
    kv = {
        "N": int(N),
        "c": int(c),
        "m": int(m),
        "gamma": repr(float(gamma)),
        "target_ccoeff": repr(float(target_ccoeff)),
        "mixing_proportions": rho_csv,
    }
    path = Path(out_dir) / DERIVED_FILENAME
    path.write_text("\n".join(f"{k}={kv[k]}" for k in sorted(kv)) + "\n")
    logging.info(
        f"Derived params -> {path}: N={N} m={m} gamma={gamma:.4f} "
        f"c={c} target_ccoeff={target_ccoeff:.4f} "
        f"mixing_proportions=[{rho_csv}]"
    )


def parse_args():
    parser = argparse.ArgumentParser(description="nPSO profile extractor")
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--params-file", type=str, default=None)
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
