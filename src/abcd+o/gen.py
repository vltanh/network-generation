import os
import time
import logging
import argparse
from pathlib import Path

import pandas as pd

from utils import setup_logging


def run_abcdo_generation(
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

    logging.info("Starting ABCD+o Generation...")
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

    # ABCD+o: pass ALL degrees (including outliers), and prepend n_outliers as
    # the first line of cs (see synnet/gen_abcd+o.py).
    deg_tsv = Path(output_dir) / "deg.tsv"
    cs_tsv = Path(output_dir) / "cs_with_outliers.tsv"
    edge_tsv = Path(output_dir) / "edge.tsv"
    com_tsv = Path(output_dir) / "com.tsv"

    # Reorder node_ids so clustered nodes come first, outliers last. This keeps
    # the first N_kept iids aligned with the clusters (as in profile.py's
    # node_id.csv — clustered nodes appear first since degree-sorted with
    # outliers interleaved by degree). Instead we explicitly partition now.
    clustered_mask = assignments != -1
    order = list(range(len(node_id_s)))
    clustered_first = [i for i in order if clustered_mask[i]] + [i for i in order if not clustered_mask[i]]
    reordered_degrees = degrees[clustered_first]
    reordered_node_ids = node_id_s.iloc[clustered_first].tolist()

    pd.DataFrame(reordered_degrees).to_csv(deg_tsv, sep="\t", header=False, index=False)

    cs_rows = []
    if n_outliers > 0:
        cs_rows.append([n_outliers])
    cs_rows.extend([[s] for s in cluster_sizes])
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
        f"xi {xi} false false {seed} {n_outliers}"
    )
    logging.info(cmd)
    rc = os.system(cmd)
    if rc != 0:
        logging.error(f"ABCD sampler exited with code {rc}")
        raise RuntimeError("ABCD sampler failed")
    logging.info(f"Generation elapsed: {time.perf_counter() - start:.4f} seconds")

    start = time.perf_counter()
    # ABCD+o emits cluster_id=1 for outliers (that's the first row in cs with
    # n_outliers prepended). Subsequent cluster iids are offset by 1.
    edge_df = pd.read_csv(edge_tsv, sep="\t", header=None, names=["source", "target"])
    com_df = pd.read_csv(com_tsv, sep="\t", header=None, names=["node_id", "cluster_id"])

    # Node iid 1..N_total maps to reordered_node_ids.
    node_label_by_iid1 = {i + 1: reordered_node_ids[i] for i in range(len(reordered_node_ids))}

    # Cluster iid → original label. With n_outliers>0, iid=1 is the outlier
    # bucket; drop those rows from com.csv. Otherwise iid=1..N_kept maps to
    # cluster_id_s in order.
    if n_outliers > 0:
        cluster_label_by_iid1 = {
            i + 2: cluster_id_s.iloc[i] for i in range(len(cluster_id_s))
        }
        # Outlier bucket label not emitted in com.csv.
        com_df = com_df[com_df["cluster_id"] != 1]
    else:
        cluster_label_by_iid1 = {
            i + 1: cluster_id_s.iloc[i] for i in range(len(cluster_id_s))
        }

    edge_df["source"] = edge_df["source"].map(node_label_by_iid1).fillna(edge_df["source"].astype(str))
    edge_df["target"] = edge_df["target"].map(node_label_by_iid1).fillna(edge_df["target"].astype(str))
    com_df["node_id"] = com_df["node_id"].map(node_label_by_iid1).fillna(com_df["node_id"].astype(str))
    com_df["cluster_id"] = com_df["cluster_id"].map(cluster_label_by_iid1).fillna(com_df["cluster_id"].astype(str))

    edge_df.to_csv(Path(output_dir) / "edge.csv", index=False)
    com_df.to_csv(Path(output_dir) / "com.csv", index=False)
    logging.info(f"Export elapsed: {time.perf_counter() - start:.4f} seconds")
    logging.info("ABCD+o generation complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="ABCD+o Graph Generator")
    parser.add_argument("--node-id", type=str, required=True)
    parser.add_argument("--cluster-id", type=str, required=True)
    parser.add_argument("--assignment", type=str, required=True)
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--cluster-sizes", type=str, required=True)
    parser.add_argument("--mixing-parameter", type=str, required=True)
    parser.add_argument("--abcd-dir", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    run_abcdo_generation(
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
