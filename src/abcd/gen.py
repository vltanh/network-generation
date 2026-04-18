import os
import logging
import argparse
from pathlib import Path

import pandas as pd

from pipeline_common import standard_setup, timed


def run_abcd_generation(
    degree_path,
    cluster_sizes_path,
    mixing_param_path,
    abcd_dir,
    output_dir,
    seed,
):
    output_dir = standard_setup(output_dir)

    logging.info("Starting ABCD Generation...")
    logging.info(f"Seed: {seed}")

    with timed("Input loading"):
        degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()
        cluster_sizes = pd.read_csv(cluster_sizes_path, header=None)[0].to_numpy()
        with open(mixing_param_path) as f:
            xi = float(f.read().strip())
        logging.info(f"xi={xi} total_clusters={len(cluster_sizes)}")

    deg_tsv = output_dir / "deg.tsv"
    cs_tsv = output_dir / "cs.tsv"
    edge_tsv = output_dir / "edge.tsv"
    com_tsv = output_dir / "com.tsv"

    pd.DataFrame(degrees).to_csv(deg_tsv, sep="\t", header=False, index=False)
    pd.DataFrame(cluster_sizes).to_csv(cs_tsv, sep="\t", header=False, index=False)

    with timed("Generation"):
        sampler = Path(abcd_dir) / "utils" / "graph_sampler.jl"
        if not sampler.exists():
            logging.error(f"ABCD sampler not found at {sampler}")
            raise FileNotFoundError(sampler)
        cmd = (
            f"julia {sampler} "
            f"{edge_tsv} {com_tsv} "
            f"{deg_tsv} {cs_tsv} "
            f"xi {xi} false false {seed} 0"
        )
        rc = os.system(cmd)
        if rc != 0:
            logging.error(f"ABCD sampler exited with code {rc}")
            raise RuntimeError("ABCD sampler failed")

    with timed("Export"):
        edge_df = pd.read_csv(edge_tsv, sep="\t", header=None, names=["source", "target"])
        com_df = pd.read_csv(com_tsv, sep="\t", header=None, names=["node_id", "cluster_id"])
        edge_df.to_csv(output_dir / "edge.csv", index=False)
        com_df.to_csv(output_dir / "com.csv", index=False)

        for p in (edge_tsv, com_tsv, deg_tsv, cs_tsv):
            p.unlink(missing_ok=True)
    logging.info("ABCD generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="ABCD Graph Generator")
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--cluster-sizes", type=str, required=True)
    parser.add_argument("--mixing-parameter", type=str, required=True)
    parser.add_argument("--abcd-dir", type=str, required=True,
                        help="Path to ABCDGraphGenerator.jl checkout")
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    run_abcd_generation(
        args.degree,
        args.cluster_sizes,
        args.mixing_parameter,
        args.abcd_dir,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
