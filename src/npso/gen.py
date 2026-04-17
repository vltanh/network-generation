import os
import time
import logging
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import powerlaw
import networkit as nk

from utils import setup_logging


def compute_global_ccoeff_from_edgelist(edgelist_path):
    """Compute the exact global clustering coefficient of an undirected edgelist."""
    elr = nk.graphio.EdgeListReader(",", 1, continuous=False, directed=False)
    g = elr.read(str(edgelist_path))
    g.removeMultiEdges()
    g.removeSelfLoops()
    return nk.globals.ClusteringCoefficient.exactGlobal(g)


def run_npso_generation(
    input_edgelist,
    node_id_path,
    cluster_id_path,
    assignment_path,
    degree_path,
    cluster_sizes_path,
    npso_dir,
    output_dir,
    seed,
):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    setup_logging(Path(output_dir) / "run.log")

    logging.info("Starting nPSO Generation...")
    logging.info(f"Seed: {seed}")

    start = time.perf_counter()
    degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()
    cluster_sizes = pd.read_csv(cluster_sizes_path, header=None)[0].to_numpy()
    assignments = pd.read_csv(assignment_path, header=None)[0].to_numpy()
    n_outliers = int((assignments == -1).sum())

    N = len(degrees)
    m = int(np.round(np.mean(degrees) / 2))
    gamma = float(np.max([
        powerlaw.Fit(degrees, discrete=True, verbose=False).power_law.alpha,
        2.0,
    ]))
    c = int(len(cluster_sizes) + n_outliers)

    target_global_ccoeff = compute_global_ccoeff_from_edgelist(input_edgelist)
    logging.info(f"N={N} m={m} gamma={gamma} c={c} target_ccoeff={target_global_ccoeff}")
    logging.info(f"Input loading + parameter computation elapsed: {time.perf_counter() - start:.4f} seconds")

    min_T, max_T = 0.0, 1.0
    best_T = None
    best_global_ccoeff = None
    best_diff = None
    prev_global_ccoeff, global_ccoeff = None, None
    max_iters = 100
    npso_dir_abs = Path(npso_dir).resolve()

    for it in range(max_iters):
        T = min_T + (max_T - min_T) / 2
        if T < 0.0005:
            break
        logging.info(f"[iter {it}] T={T}")

        gen_start = time.perf_counter()
        prefix = Path(output_dir) / f"{T:.5f}_"
        cmd = (
            f"matlab -nodisplay -nosplash -nodesktop -r "
            f"\"try, addpath(genpath('{npso_dir_abs}')), "
            f"run_npso({N}, {m}, {T}, {gamma}, {c}, '{prefix}'), "
            f"catch e, fprintf(1, e.message), end, quit\""
        )
        logging.info(cmd)
        os.system(cmd)
        logging.info(f"Generation elapsed: {time.perf_counter() - gen_start:.4f} seconds")

        edge_path = Path(output_dir) / f"{T:.5f}_edge.tsv"
        com_path = Path(output_dir) / f"{T:.5f}_com.tsv"
        if not edge_path.exists() or not com_path.exists():
            logging.error(f"Missing MATLAB outputs at T={T}")
            global_ccoeff = None
        else:
            elr = nk.graphio.EdgeListReader("\t", 0, continuous=False, directed=False)
            graph = elr.read(str(edge_path))
            graph.removeMultiEdges()
            graph.removeSelfLoops()
            prev_global_ccoeff = global_ccoeff
            global_ccoeff = nk.globals.ClusteringCoefficient.exactGlobal(graph)
            logging.info(f"Global clustering coefficient: {global_ccoeff}")

        diff = abs(global_ccoeff - target_global_ccoeff) if global_ccoeff is not None else 2.0
        step = abs(prev_global_ccoeff - global_ccoeff) if prev_global_ccoeff is not None and global_ccoeff is not None else 2.0

        if best_global_ccoeff is None or diff < best_diff:
            if best_T is not None and best_T != T:
                _safe_remove(Path(output_dir) / f"{best_T:.5f}_edge.tsv")
                _safe_remove(Path(output_dir) / f"{best_T:.5f}_com.tsv")
            best_T = T
            best_global_ccoeff = global_ccoeff
            best_diff = diff
        else:
            if best_T is not None and best_T != T:
                _safe_remove(Path(output_dir) / f"{T:.5f}_edge.tsv")
                _safe_remove(Path(output_dir) / f"{T:.5f}_com.tsv")

        logging.info(f"Step: {step}  Best T: {best_T}  Best ccoeff: {best_global_ccoeff}  Best diff: {best_diff}")
        if best_diff is not None and best_diff < 0.005:
            break
        if step < 0.0001:
            break

        if global_ccoeff is not None and global_ccoeff < target_global_ccoeff:
            max_T = T
        else:
            min_T = T

    if best_T is None:
        raise RuntimeError("nPSO produced no viable output.")

    best_edge = Path(output_dir) / f"{best_T:.5f}_edge.tsv"
    best_com = Path(output_dir) / f"{best_T:.5f}_com.tsv"
    if not best_edge.exists() or not best_com.exists():
        raise RuntimeError(f"Best nPSO output missing at T={best_T}")

    edge_df = pd.read_csv(best_edge, sep="\t", header=None, names=["source", "target"])
    com_df = pd.read_csv(best_com, sep="\t", header=None, names=["node_id", "cluster_id"])
    # Drop outlier bucket (cluster_id == 1 matches synnet convention).
    com_df = com_df[com_df["cluster_id"] > 1]

    edge_df.to_csv(Path(output_dir) / "edge.csv", index=False)
    com_df.to_csv(Path(output_dir) / "com.csv", index=False)

    # Cleanup the per-T files.
    for p in Path(output_dir).glob("*_edge.tsv"):
        _safe_remove(p)
    for p in Path(output_dir).glob("*_com.tsv"):
        _safe_remove(p)

    logging.info("nPSO generation complete.")


def _safe_remove(p):
    try:
        Path(p).unlink()
    except FileNotFoundError:
        pass


def parse_args():
    parser = argparse.ArgumentParser(description="nPSO Graph Generator")
    parser.add_argument("--input-edgelist", type=str, required=True,
                        help="Original empirical edgelist (used to measure target global clustering coefficient)")
    parser.add_argument("--node-id", type=str, required=True)
    parser.add_argument("--cluster-id", type=str, required=True)
    parser.add_argument("--assignment", type=str, required=True)
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--cluster-sizes", type=str, required=True)
    parser.add_argument("--npso-dir", type=str, required=True,
                        help="Path to the nPSO_model checkout (containing run_npso.m)")
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    run_npso_generation(
        args.input_edgelist,
        args.node_id,
        args.cluster_id,
        args.assignment,
        args.degree,
        args.cluster_sizes,
        args.npso_dir,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
