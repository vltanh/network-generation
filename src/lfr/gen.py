import os
import logging
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import powerlaw

from pipeline_common import standard_setup, timed


def run_lfr_generation(
    degree_path,
    cluster_sizes_path,
    mixing_param_path,
    lfr_binary,
    output_dir,
    seed,
):
    output_dir = standard_setup(output_dir)

    logging.info("Starting LFR Generation...")
    logging.info(f"Seed: {seed}")

    with timed("Input loading + parameter computation"):
        degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()
        cluster_sizes = pd.read_csv(cluster_sizes_path, header=None)[0].to_numpy()
        with open(mixing_param_path) as f:
            mu = float(f.read().strip())

        N = len(degrees)
        k = float(np.mean(degrees))
        maxk = int(np.max(degrees))
        t1 = float(powerlaw.Fit(degrees, discrete=True, verbose=False).power_law.alpha)

        minc = max(int(np.min(cluster_sizes)), 3)
        maxc = int(np.max(cluster_sizes))
        t2 = float(powerlaw.Fit(cluster_sizes, discrete=True, verbose=False, xmin=minc).power_law.alpha)
        logging.info(
            f"N={N} k={k} maxk={maxk} minc={minc} maxc={maxc} mu={mu} t1={t1} t2={t2}"
        )

    with timed("Generation"):
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
        # LFR reads its seed from ./time_seed.dat (and increments it on exit).
        (output_dir / "time_seed.dat").write_text(f"{seed}\n")
        prev_cwd = os.getcwd()
        os.chdir(output_dir)
        rc = os.system(cmd)
        os.chdir(prev_cwd)
        if rc != 0:
            logging.error(f"LFR binary exited with code {rc}")
            raise RuntimeError("LFR binary failed")

    with timed("Export"):
        community_dat = output_dir / "community.dat"
        network_dat = output_dir / "network.dat"
        if not community_dat.exists() or not network_dat.exists():
            logging.error("LFR outputs missing (community.dat / network.dat).")
            raise RuntimeError("LFR outputs missing")

        edge_df = pd.read_csv(network_dat, sep=r"\s+", header=None, names=["source", "target"])
        com_df = pd.read_csv(community_dat, sep=r"\s+", header=None, names=["node_id", "cluster_id"])

        # LFR writes each undirected edge twice (u,v) and (v,u); drop the reverse.
        u = edge_df[["source", "target"]].min(axis=1)
        v = edge_df[["source", "target"]].max(axis=1)
        edge_df = pd.DataFrame({"source": u, "target": v}).drop_duplicates().reset_index(drop=True)

        edge_df.to_csv(output_dir / "edge.csv", index=False)
        com_df.to_csv(output_dir / "com.csv", index=False)

        for name in ("community.dat", "network.dat", "statistics.dat", "time_seed.dat"):
            (output_dir / name).unlink(missing_ok=True)
    logging.info("LFR generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="LFR Graph Generator")
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
        args.degree,
        args.cluster_sizes,
        args.mixing_parameter,
        args.lfr_binary,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
