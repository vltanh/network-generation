import os
import time
import logging
import argparse
from pathlib import Path

import pandas as pd

from utils import setup_logging


def run_abcd_generation(
    node_id_path,
    cluster_id_path,
    assignment_path,
    degree_path,
    cluster_sizes_path,
    mixing_param_path,
    abcd_dir,
    output_dir,
    seed,
):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    setup_logging(Path(output_dir) / "run.log")

    logging.info("Starting ABCD Generation...")
    logging.info(f"Seed: {seed}")

    start = time.perf_counter()
    node_id_s = pd.read_csv(node_id_path, header=None, dtype=str)[0]
    cluster_id_s = pd.read_csv(cluster_id_path, header=None, dtype=str)[0]
    assignments = pd.read_csv(assignment_path, header=None)[0].to_numpy()
    degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()
    cluster_sizes = pd.read_csv(cluster_sizes_path, header=None)[0].to_numpy()
    with open(mixing_param_path) as f:
        xi = float(f.read().strip())
    n_outliers = int((assignments == -1).sum())
    logging.info(f"xi={xi} n_outliers={n_outliers} kept_clusters={len(cluster_sizes)}")
    logging.info(f"Input loading elapsed: {time.perf_counter() - start:.4f} seconds")

    # ABCD works on clustered degrees only. Append n_outliers singleton clusters
    # at the end to match synnet's convention (see synnet/gen_abcd.py).
    clustered_mask = assignments != -1
    kept_node_ids = node_id_s[clustered_mask].tolist()
    kept_degrees = degrees[clustered_mask]

    deg_tsv = Path(output_dir) / "deg.tsv"
    cs_tsv = Path(output_dir) / "cs_with_outliers.tsv"
    edge_tsv = Path(output_dir) / "edge.tsv"
    com_tsv = Path(output_dir) / "com.tsv"

    pd.DataFrame(kept_degrees).to_csv(deg_tsv, sep="\t", header=False, index=False)
    cs_rows = list(cluster_sizes) + [1] * n_outliers
    pd.DataFrame(cs_rows).to_csv(cs_tsv, sep="\t", header=False, index=False)

    start = time.perf_counter()
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
    logging.info(cmd)
    rc = os.system(cmd)
    if rc != 0:
        logging.error(f"ABCD sampler exited with code {rc}")
        raise RuntimeError("ABCD sampler failed")
    logging.info(f"Generation elapsed: {time.perf_counter() - start:.4f} seconds")

    start = time.perf_counter()
    # ABCD emits 1-indexed node iids in edge.tsv/com.tsv aligned with the degree
    # sequence order we passed in (kept_node_ids order). Remap to original IDs.
    edge_df = pd.read_csv(edge_tsv, sep="\t", header=None, names=["source", "target"])
    com_df = pd.read_csv(com_tsv, sep="\t", header=None, names=["node_id", "cluster_id"])

    # Synnet's CS order is (kept clusters sorted by size) + (outliers as size-1 clusters).
    # Assign a cluster_id label for each. For kept clusters, reuse the original
    # cluster_id values (in the same order as cluster_sizes). For outliers, use
    # synthetic labels "outlier_0", "outlier_1", ...
    num_kept = len(cluster_sizes)
    cluster_label_by_iid1 = {
        i + 1: cluster_id_s.iloc[i] for i in range(num_kept)
    }
    for j in range(n_outliers):
        cluster_label_by_iid1[num_kept + 1 + j] = f"outlier_{j}"

    node_label_by_iid1 = {
        i + 1: kept_node_ids[i] for i in range(len(kept_node_ids))
    }
    # ABCD places outliers as additional nodes; extend mapping if needed.
    # (When generator is invoked with outlier_count=0, no extra nodes are expected.)

    edge_df["source"] = edge_df["source"].map(node_label_by_iid1).fillna(edge_df["source"].astype(str))
    edge_df["target"] = edge_df["target"].map(node_label_by_iid1).fillna(edge_df["target"].astype(str))
    com_df["node_id"] = com_df["node_id"].map(node_label_by_iid1).fillna(com_df["node_id"].astype(str))
    com_df["cluster_id"] = com_df["cluster_id"].map(cluster_label_by_iid1).fillna(com_df["cluster_id"].astype(str))

    edge_df.to_csv(Path(output_dir) / "edge.csv", index=False)
    com_df.to_csv(Path(output_dir) / "com.csv", index=False)
    logging.info(f"Export elapsed: {time.perf_counter() - start:.4f} seconds")
    logging.info("ABCD generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="ABCD Graph Generator")
    parser.add_argument("--node-id", type=str, required=True)
    parser.add_argument("--cluster-id", type=str, required=True)
    parser.add_argument("--assignment", type=str, required=True)
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
        args.node_id,
        args.cluster_id,
        args.assignment,
        args.degree,
        args.cluster_sizes,
        args.mixing_parameter,
        args.abcd_dir,
        args.output_folder,
        args.seed,
    )


if __name__ == "__main__":
    main()
