import os
import time
import logging
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import powerlaw

from utils import setup_logging


def run_lfr_generation(
    node_id_path,
    cluster_id_path,
    assignment_path,
    degree_path,
    cluster_sizes_path,
    mixing_param_path,
    lfr_binary,
    output_dir,
    seed,
):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    setup_logging(Path(output_dir) / "run.log")

    logging.info("Starting LFR Generation...")
    logging.info(f"Seed: {seed}")

    start = time.perf_counter()
    degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()
    cluster_sizes = pd.read_csv(cluster_sizes_path, header=None)[0].to_numpy()
    assignments = pd.read_csv(assignment_path, header=None)[0].to_numpy()
    with open(mixing_param_path) as f:
        mu = float(f.read().strip())
    n_outliers = int((assignments == -1).sum())

    N = len(degrees)
    k = float(np.mean(degrees))
    maxk = int(np.max(degrees))
    t1 = float(powerlaw.Fit(degrees, discrete=True, verbose=False).power_law.alpha)

    # Include outliers in the size distribution (each as a size-1 community).
    cs_full = np.concatenate([cluster_sizes, np.ones(n_outliers, dtype=int)])
    minc = max(int(np.min(cs_full)), 3)
    maxc = int(np.max(cs_full))
    t2 = float(powerlaw.Fit(cs_full, discrete=True, verbose=False, xmin=minc).power_law.alpha)
    logging.info(
        f"N={N} k={k} maxk={maxk} minc={minc} maxc={maxc} mu={mu} t1={t1} t2={t2} n_outliers={n_outliers}"
    )
    logging.info(f"Input loading + parameter computation elapsed: {time.perf_counter() - start:.4f} seconds")

    start = time.perf_counter()
    lfr_exec = Path(lfr_binary).resolve()
    if not lfr_exec.exists():
        logging.error(f"LFR binary not found at {lfr_exec}")
        raise FileNotFoundError(lfr_exec)
    cmd = (
        f"{lfr_exec} "
        f"-N {N} -k {k} -maxk {maxk} "
        f"-minc {minc} -maxc {maxc} "
        f"-mu {mu} -t1 {t1} -t2 {t2}"
    )
    logging.info(cmd)
    prev_cwd = os.getcwd()
    os.chdir(output_dir)
    rc = os.system(cmd)
    os.chdir(prev_cwd)
    if rc != 0:
        logging.error(f"LFR binary exited with code {rc}")
        raise RuntimeError("LFR binary failed")
    logging.info(f"Generation elapsed: {time.perf_counter() - start:.4f} seconds")

    start = time.perf_counter()
    community_dat = Path(output_dir) / "community.dat"
    network_dat = Path(output_dir) / "network.dat"
    if not community_dat.exists() or not network_dat.exists():
        logging.error("LFR outputs missing (community.dat / network.dat).")
        raise RuntimeError("LFR outputs missing")

    edge_df = pd.read_csv(network_dat, sep=r"\s+", header=None, names=["source", "target"])
    com_df = pd.read_csv(community_dat, sep=r"\s+", header=None, names=["node_id", "cluster_id"])

    edge_df.to_csv(Path(output_dir) / "edge.csv", index=False)
    com_df.to_csv(Path(output_dir) / "com.csv", index=False)
    logging.info(f"Export elapsed: {time.perf_counter() - start:.4f} seconds")
    logging.info("LFR generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="LFR Graph Generator")
    parser.add_argument("--node-id", type=str, required=True)
    parser.add_argument("--cluster-id", type=str, required=True)
    parser.add_argument("--assignment", type=str, required=True)
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--cluster-sizes", type=str, required=True)
    parser.add_argument("--mixing-parameter", type=str, required=True)
    parser.add_argument("--lfr-binary", type=str, required=True,
                        help="Path to LFR benchmark executable (unweighted_undirected/benchmark)")
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    run_lfr_generation(
        args.node_id,
        args.cluster_id,
        args.assignment,
        args.degree,
        args.cluster_sizes,
        args.mixing_parameter,
        args.lfr_binary,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
