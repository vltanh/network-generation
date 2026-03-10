import time
import random
import logging
import argparse
from pathlib import Path
from collections import defaultdict, deque

import numpy as np
import pandas as pd
import graph_tool.all as gt
from scipy.sparse import dok_matrix

from utils import setup_logging


def load_network_data(orig_edgelist_fp, orig_clustering_fp, exist_edgelist_fp):
    df_orig = pd.read_csv(orig_edgelist_fp, dtype=str)
    df_clust = pd.read_csv(orig_clustering_fp, dtype=str)

    try:
        df_exist = pd.read_csv(exist_edgelist_fp, dtype=str)
    except pd.errors.EmptyDataError:
        df_exist = pd.DataFrame(columns=["source", "target"])

    node2cluster_str = dict(zip(df_clust["node_id"], df_clust["cluster_id"]))

    all_nodes = set(df_orig["source"]).union(set(df_orig["target"]))
    outliers = all_nodes - set(node2cluster_str.keys())

    return df_orig, df_exist, node2cluster_str, all_nodes, outliers


def prepare_residual_sbm_inputs(
    df_orig, df_exist, node2cluster_str, all_nodes, outliers, outlier_mode
):
    node_id2iid = {u: i for i, u in enumerate(all_nodes)}
    node_iid2id = {i: u for u, i in node_id2iid.items()}

    unique_clusters = set(node2cluster_str.values())
    cluster_id2iid = {c: i for i, c in enumerate(unique_clusters)}

    b = np.empty(len(all_nodes), dtype=int)
    current_c_iid = len(cluster_id2iid)

    if outliers and outlier_mode == "combined":
        combined_block = current_c_iid
        current_c_iid += 1

    for u in all_nodes:
        u_iid = node_id2iid[u]
        if u in outliers:
            if outlier_mode == "combined":
                b[u_iid] = combined_block
            else:
                b[u_iid] = current_c_iid
                current_c_iid += 1
        else:
            b[u_iid] = cluster_id2iid[node2cluster_str[u]]

    num_clusters = current_c_iid
    num_nodes = len(all_nodes)

    out_degs = np.zeros(num_nodes, dtype=int)

    u_orig = np.where(
        df_orig["source"] < df_orig["target"], df_orig["source"], df_orig["target"]
    )
    v_orig = np.where(
        df_orig["source"] > df_orig["target"], df_orig["source"], df_orig["target"]
    )
    df_orig_dedup = pd.DataFrame({"u": u_orig, "v": v_orig}).drop_duplicates()

    for u_id, v_id in zip(df_orig_dedup["u"], df_orig_dedup["v"]):
        u, v = node_id2iid[u_id], node_id2iid[v_id]
        out_degs[u] += 1
        out_degs[v] += 1

    if not df_exist.empty:
        u_ext = np.where(
            df_exist["source"] < df_exist["target"],
            df_exist["source"],
            df_exist["target"],
        )
        v_ext = np.where(
            df_exist["source"] > df_exist["target"],
            df_exist["source"],
            df_exist["target"],
        )
        df_exist_dedup = pd.DataFrame({"u": u_ext, "v": v_ext}).drop_duplicates()

        for u_id, v_id in zip(df_exist_dedup["u"], df_exist_dedup["v"]):
            if u_id not in node_id2iid or v_id not in node_id2iid:
                continue
            u, v = node_id2iid[u_id], node_id2iid[v_id]
            out_degs[u] = max(0, out_degs[u] - 1)
            out_degs[v] = max(0, out_degs[v] - 1)

    probs = dok_matrix((num_clusters, num_clusters), dtype=int)

    for u_id, v_id in zip(df_orig_dedup["u"], df_orig_dedup["v"]):
        u, v = node_id2iid[u_id], node_id2iid[v_id]
        b_u, b_v = b[u], b[v]
        if b_u != b_v:
            probs[b_u, b_v] += 1
            probs[b_v, b_u] += 1

    probs_csr = probs.tocsr()
    row_sums = np.array(probs_csr.sum(axis=1)).flatten()

    for k in range(num_clusters):
        nodes_in_k = np.where(b == k)[0]
        if len(nodes_in_k) == 0:
            continue

        D_k = np.sum(out_degs[nodes_in_k])
        E_inter_k = row_sums[k]
        diff = D_k - E_inter_k

        if diff < 0:
            deficit = abs(diff)
            for i in range(deficit):
                out_degs[nodes_in_k[i % len(nodes_in_k)]] += 1
            probs[k, k] = 0
        else:
            probs[k, k] = diff
            if probs[k, k] % 2 != 0:
                probs[k, k] += 1
                out_degs[nodes_in_k[0]] += 1

    return b, probs.tocsr(), out_degs, node_iid2id


def rewire_invalid_edges(g, b, max_retries=10):
    edges = g.get_edges()
    valid_pool = defaultdict(list)
    valid_set = set()
    invalid_edges = deque()

    def make_edge(u, v):
        return (int(min(u, v)), int(max(u, v)))

    def get_bp(u, v):
        return (int(min(b[u], b[v])), int(max(b[u], b[v])))

    for u, v in edges:
        e = make_edge(u, v)
        if u == v or e in valid_set:
            invalid_edges.append((u, v))
        else:
            bp = get_bp(u, v)
            valid_set.add(e)
            valid_pool[bp].append(e)

    logging.info(f"Initial bad edges before rewiring: {len(invalid_edges)}")

    for attempt in range(max_retries):
        if not invalid_edges:
            logging.info("All bad edges resolved! Exiting rewiring loop early.")
            break

        last_recycle = len(invalid_edges)
        recycle_counter = last_recycle

        while invalid_edges:
            recycle_counter -= 1
            if recycle_counter < 0:
                if len(invalid_edges) < last_recycle:
                    last_recycle = len(invalid_edges)
                    recycle_counter = last_recycle
                else:
                    break

            u, v = invalid_edges.popleft()
            bp = get_bp(u, v)
            pool = valid_pool[bp]

            if not pool:
                invalid_edges.append((u, v))
                continue

            idx = random.randrange(len(pool))
            x, y = pool[idx]
            A, B = bp

            if A != B:
                u_A = u if b[u] == A else v
                u_B = v if b[u] == A else u
                x_A = x if b[x] == A else y
                x_B = y if b[x] == A else x

                new_e1, new_e2 = make_edge(u_A, x_B), make_edge(x_A, u_B)
            else:
                if random.random() < 0.5:
                    new_e1, new_e2 = make_edge(u, x), make_edge(v, y)
                else:
                    new_e1, new_e2 = make_edge(u, y), make_edge(v, x)

            if (
                new_e1[0] != new_e1[1]
                and new_e2[0] != new_e2[1]
                and new_e1 not in valid_set
                and new_e2 not in valid_set
                and new_e1 != new_e2
            ):

                valid_set.remove(make_edge(x, y))
                pool[idx] = pool[-1]
                pool.pop()

                valid_set.add(new_e1)
                valid_set.add(new_e2)
                pool.append(new_e1)
                pool.append(new_e2)
            else:
                invalid_edges.append((u, v))

        logging.info(
            f"After attempt {attempt + 1}: {len(invalid_edges)} bad edges remain."
        )

    if invalid_edges:
        logging.warning(
            f"Finished {max_retries} retries. {len(invalid_edges)} bad edges remain unresolved and will be dropped."
        )

    return list(valid_set)


def generate_residual_subnetwork(b, probs, out_degs, edge_correction_mode):
    if out_degs.sum() > 0:
        g = gt.generate_sbm(
            b,
            probs,
            out_degs=out_degs,
            micro_ers=True,
            micro_degs=True,
            directed=False,
        )
    else:
        g = gt.Graph(directed=False)

    if edge_correction_mode == "rewire":
        valid_edges = rewire_invalid_edges(g, b, max_retries=10)
        g.clear_edges()
        g.add_edge_list(valid_edges)

    gt.remove_parallel_edges(g)
    gt.remove_self_loops(g)

    return [(int(src), int(tgt)) for src, tgt in g.iter_edges()]


def export_generated_edges(edges, node_iid2id, output_dir: Path):
    df_out = pd.DataFrame(
        [(node_iid2id[src], node_iid2id[tgt]) for src, tgt in edges],
        columns=["source", "target"],
    )
    df_out.to_csv(output_dir / "edge_outlier.csv", index=False)


def run_outlier_generation(
    orig_edgelist_fp,
    orig_clustering_fp,
    exist_edgelist_fp,
    outlier_mode,
    edge_correction_mode,
    output_folder,
):
    output_dir = Path(output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(output_dir / "run.log")

    logging.info("Starting Residual SBM Generation...")
    logging.info(
        f"Correction Mode: {edge_correction_mode} | Outlier Mode: {outlier_mode}"
    )

    start = time.perf_counter()
    df_orig, df_exist, node2cluster_str, all_nodes, outliers = load_network_data(
        orig_edgelist_fp, orig_clustering_fp, exist_edgelist_fp
    )
    b, probs, out_degs, node_iid2id = prepare_residual_sbm_inputs(
        df_orig, df_exist, node2cluster_str, all_nodes, outliers, outlier_mode
    )
    logging.info(f"Setup complete: {time.perf_counter() - start:.4f} seconds")

    start = time.perf_counter()
    edges = generate_residual_subnetwork(b, probs, out_degs, edge_correction_mode)
    logging.info(
        f"Generation ({len(edges)} edges): {time.perf_counter() - start:.4f} seconds"
    )

    export_generated_edges(edges, node_iid2id, output_dir)
    logging.info("Complete.")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--orig-edgelist", type=str, required=True)
    parser.add_argument("--orig-clustering", type=str, required=True)
    parser.add_argument("--exist-edgelist", type=str, required=True)
    parser.add_argument(
        "--outlier-mode",
        type=str,
        choices=["singleton", "combined"],
        default="combined",
    )
    parser.add_argument(
        "--edge-correction",
        type=str,
        choices=["drop", "rewire"],
        default="rewire",
        help="'drop' removes bad edges (faster but alters degree), 'rewire' swaps them (preserves degree).",
    )
    parser.add_argument("--output-folder", type=str, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    run_outlier_generation(
        args.orig_edgelist,
        args.orig_clustering,
        args.exist_edgelist,
        args.outlier_mode,
        args.edge_correction,
        args.output_folder,
    )


if __name__ == "__main__":
    main()
