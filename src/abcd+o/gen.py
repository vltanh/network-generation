import re
import subprocess
import logging
import argparse
from pathlib import Path

import pandas as pd

from pipeline_common import standard_setup, timed, drop_singleton_clusters, simplify_edges


OUTLIER_LIFT_WARNING = "outlier nodes form a community"


def run_abcdo_generation(
    degree_path,
    cluster_sizes_path,
    mixing_param_path,
    n_outliers_path,
    abcd_dir,
    output_dir,
    seed,
):
    output_dir = standard_setup(output_dir)

    logging.info("Starting ABCD+o Generation...")
    logging.info(f"Seed: {seed}")

    with timed("Input loading"):
        degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()
        cluster_sizes = pd.read_csv(cluster_sizes_path, header=None)[0].to_numpy()
        with open(mixing_param_path) as f:
            xi = float(f.read().strip())
        with open(n_outliers_path) as f:
            n_outliers = int(f.read().strip())

        logging.info(
            f"xi={xi} n_outliers={n_outliers} clusters={len(cluster_sizes)}"
        )

    deg_tsv = output_dir / "deg.tsv"
    cs_tsv = output_dir / "cs_with_outliers.tsv"
    edge_tsv = output_dir / "edge.tsv"
    com_tsv = output_dir / "com.tsv"

    # ABCD+o expects outlier mega-cluster size prepended to cluster_sizes (cluster iid=1).
    pd.DataFrame(degrees).to_csv(deg_tsv, sep="\t", header=False, index=False)
    cs_rows = []
    if n_outliers > 0:
        cs_rows.append([n_outliers])
    cs_rows.extend([[s] for s in cluster_sizes])
    pd.DataFrame(cs_rows).to_csv(cs_tsv, sep="\t", header=False, index=False)

    with timed("Generation"):
        sampler = Path(abcd_dir) / "utils" / "graph_sampler.jl"
        if not sampler.exists():
            logging.error(f"ABCD sampler not found at {sampler}")
            raise FileNotFoundError(sampler)
        cmd = [
            "julia", str(sampler),
            str(edge_tsv), str(com_tsv),
            str(deg_tsv), str(cs_tsv),
            "xi", str(xi), "false", "false", str(seed), str(n_outliers),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stderr:
            print(proc.stderr, end="", flush=True)
        if proc.stdout:
            print(proc.stdout, end="", flush=True)
        if proc.returncode != 0:
            logging.error(f"ABCD sampler exited with code {proc.returncode}")
            raise RuntimeError("ABCD sampler failed")

        # At low ξ, ABCD's stderr warns that outliers form their own community;
        # keep them as a real cluster instead of stripping.
        outliers_lifted = bool(re.search(OUTLIER_LIFT_WARNING, proc.stderr, re.IGNORECASE))
        if outliers_lifted:
            logging.warning(
                "ABCD reported that outliers form a community; keeping the "
                "outlier mega-cluster as cluster_id=1 in com.csv."
            )

    with timed("Export"):
        edge_df = pd.read_csv(edge_tsv, sep="\t", header=None, names=["source", "target"])
        com_df = pd.read_csv(com_tsv, sep="\t", header=None, names=["node_id", "cluster_id"])

        if n_outliers > 0 and not outliers_lifted:
            com_df = com_df[com_df["cluster_id"] != 1]

        edge_df = simplify_edges(edge_df)
        com_df = drop_singleton_clusters(com_df)
        edge_df.to_csv(output_dir / "edge.csv", index=False)
        com_df.to_csv(output_dir / "com.csv", index=False)

        for p in (edge_tsv, com_tsv, deg_tsv, cs_tsv):
            p.unlink(missing_ok=True)
    logging.info("ABCD+o generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="ABCD+o Graph Generator")
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--cluster-sizes", type=str, required=True)
    parser.add_argument("--mixing-parameter", type=str, required=True)
    parser.add_argument("--n-outliers", type=str, required=True)
    parser.add_argument("--abcd-dir", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=1)
    return parser.parse_args()


def main():
    args = parse_args()
    run_abcdo_generation(
        args.degree,
        args.cluster_sizes,
        args.mixing_parameter,
        args.n_outliers,
        args.abcd_dir,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
