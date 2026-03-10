import argparse
import logging
import heapq
import time
import random
from collections import deque
from pathlib import Path

import pandas as pd

from utils import setup_logging


def parse_args():
    parser = argparse.ArgumentParser(description="Degree Matching")
    parser.add_argument("--input-edgelist", type=str, required=True)
    parser.add_argument("--ref-edgelist", type=str, required=True)
    parser.add_argument("--ref-clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument(
        "--algorithm",
        type=str,
        choices=["greedy", "true_greedy", "random_greedy", "rewire"],
        default="true_greedy",
        help="Choose 'greedy' for max-heap, 'true_greedy' for true greedy matching, 'random_greedy' for unbiased dynamic filtering, or 'rewire' for the configuration model.",
    )
    return parser.parse_args()


def load_reference_topologies(orig_edgelist_fp, orig_clustering_fp):
    df_orig_edges = pd.read_csv(orig_edgelist_fp, dtype=str)
    df_orig_clusters = pd.read_csv(orig_clustering_fp, dtype=str)

    all_orig_nodes = (
        set(df_orig_edges["source"])
        .union(set(df_orig_edges["target"]))
        .union(set(df_orig_clusters["node_id"]))
    )

    node_id2iid = {u: i for i, u in enumerate(all_orig_nodes)}
    node_iid2id = {i: u for u, i in node_id2iid.items()}
    out_degs = {iid: 0 for iid in node_iid2id.keys()}

    for src, tgt in zip(df_orig_edges["source"], df_orig_edges["target"]):
        out_degs[node_id2iid[src]] += 1
        out_degs[node_id2iid[tgt]] += 1

    return node_id2iid, node_iid2id, out_degs


def subtract_existing_edges(exist_edgelist_fp, node_id2iid, out_degs):
    df_exist_edges = pd.read_csv(exist_edgelist_fp, dtype=str)
    exist_neighbor = {iid: set() for iid in node_id2iid.values()}

    for src, tgt in zip(df_exist_edges["source"], df_exist_edges["target"]):
        src_iid, tgt_iid = node_id2iid[src], node_id2iid[tgt]
        if tgt_iid in exist_neighbor[src_iid]:
            continue

        exist_neighbor[src_iid].add(tgt_iid)
        exist_neighbor[tgt_iid].add(src_iid)

        out_degs[src_iid] = max(0, out_degs[src_iid] - 1)
        out_degs[tgt_iid] = max(0, out_degs[tgt_iid] - 1)

    return exist_neighbor, out_degs


def match_missing_degrees_random_greedy(out_degs, exist_neighbor):
    logging.info("Starting Randomized Greedy matching algorithm...")

    available_degrees = {k: v for k, v in out_degs.items() if v > 0}
    available_nodes = list(available_degrees.keys())

    initial_missing_stubs = sum(available_degrees.values())
    logging.info(
        f"Initial missing stubs: {initial_missing_stubs} (Target edges: {initial_missing_stubs // 2})"
    )

    degree_edges = set()
    stuck_nodes = set()

    # Loop until no nodes are left to pair
    while available_nodes:
        # 1. Pick 'u' randomly, weighted by its remaining missing degree
        weights = [available_degrees[n] for n in available_nodes]
        u = random.choices(available_nodes, weights=weights, k=1)[0]

        # 2. Dynamically filter for legally valid targets
        invalid_targets = exist_neighbor.get(u, set())
        valid_targets = [
            n for n in available_nodes if n != u and n not in invalid_targets
        ]

        # 3. If gridlocked, mark as stuck and remove from pool
        if not valid_targets:
            available_nodes.remove(u)
            stuck_nodes.add(u)
            continue

        # 4. Pick 'v' randomly from valid targets, weighted by its remaining missing degree
        v_weights = [available_degrees[n] for n in valid_targets]
        v = random.choices(valid_targets, weights=v_weights, k=1)[0]

        # 5. Add the edge and update the topology
        degree_edges.add((min(u, v), max(u, v)))
        exist_neighbor[u].add(v)
        exist_neighbor[v].add(u)

        available_degrees[u] -= 1
        available_degrees[v] -= 1

        # 6. Remove nodes from the active pool if their degrees are fulfilled
        if available_degrees[u] == 0:
            available_nodes.remove(u)
        if available_degrees[v] == 0:
            available_nodes.remove(v)

    if stuck_nodes:
        stuck_stubs = sum(available_degrees[n] for n in stuck_nodes)
        logging.warning(
            f"Finished with {len(stuck_nodes)} physically gridlocked nodes. {stuck_stubs} missing stubs dropped."
        )

    return degree_edges


def match_missing_degrees_greedy(out_degs, exist_neighbor):
    logging.info("Starting Original Greedy matching algorithm...")
    available_node_set = {node_iid for node_iid, deg in out_degs.items() if deg > 0}
    available_node_degrees = {
        node_iid: deg for node_iid, deg in out_degs.items() if deg > 0
    }

    initial_missing_stubs = sum(available_node_degrees.values())
    logging.info(f"Initial missing stubs: {initial_missing_stubs}")

    max_heap = [(-degree, node) for node, degree in available_node_degrees.items()]
    heapq.heapify(max_heap)

    degree_edges = set()

    while max_heap:
        _, available_c_node = heapq.heappop(max_heap)

        if available_c_node not in available_node_degrees:
            continue

        invalid_targets = exist_neighbor.get(available_c_node, set()).copy()
        invalid_targets.add(available_c_node)
        available_non_neighbors = available_node_set - invalid_targets

        avail_k = min(
            available_node_degrees[available_c_node], len(available_non_neighbors)
        )

        for _ in range(avail_k):
            edge_end = available_non_neighbors.pop()
            degree_edges.add((available_c_node, edge_end))

            exist_neighbor[available_c_node].add(edge_end)
            exist_neighbor[edge_end].add(available_c_node)

            available_node_degrees[edge_end] -= 1
            if available_node_degrees[edge_end] == 0:
                available_node_set.remove(edge_end)
                del available_node_degrees[edge_end]

        del available_node_degrees[available_c_node]
        available_node_set.remove(available_c_node)

    return degree_edges


def match_missing_degrees_true_greedy(out_degs, exist_neighbor):
    logging.info("Starting True Dynamic Greedy matching algorithm...")

    # Track the real-time degree of each node
    current_degrees = {n: d for n, d in out_degs.items() if d > 0}

    initial_missing_stubs = sum(current_degrees.values())
    logging.info(
        f"Initial missing stubs: {initial_missing_stubs} (Target edges: {initial_missing_stubs // 2})"
    )

    # Initial heap population
    heap = [(-deg, n) for n, deg in current_degrees.items()]
    heapq.heapify(heap)

    degree_edges = set()
    stuck_nodes = set()

    while heap:
        neg_deg, u = heapq.heappop(heap)
        deg_u = -neg_deg

        # Lazy Deletion: Ignore stale heap entries
        if u not in current_degrees or deg_u != current_degrees[u]:
            continue

        # Dynamically filter for legally valid targets
        invalid_targets = exist_neighbor.get(u, set())
        valid_targets = [
            n for n in current_degrees if n != u and n not in invalid_targets
        ]

        if not valid_targets:
            # Node is structurally gridlocked
            stuck_nodes.add(u)
            del current_degrees[u]
            continue

        # TRUE GREEDY: Pick the valid target 'v' that has the HIGHEST remaining degree
        v = max(valid_targets, key=lambda x: current_degrees[x])

        # Make the edge
        degree_edges.add((min(u, v), max(u, v)))
        exist_neighbor[u].add(v)
        exist_neighbor[v].add(u)

        # Update real-time degrees
        current_degrees[u] -= 1
        current_degrees[v] -= 1

        # Push updated states back onto the heap (if they still need edges)
        if current_degrees[u] > 0:
            heapq.heappush(heap, (-current_degrees[u], u))
        else:
            del current_degrees[u]

        if current_degrees[v] > 0:
            heapq.heappush(heap, (-current_degrees[v], v))
        elif v in current_degrees:
            del current_degrees[v]

    if stuck_nodes:
        stuck_stubs = sum(out_degs[n] for n in stuck_nodes) - sum(
            current_degrees.get(n, 0) for n in stuck_nodes
        )  # Estimate
        logging.warning(f"Finished with {len(stuck_nodes)} gridlocked nodes.")

    return degree_edges


def match_missing_degrees_rewire(out_degs, exist_neighbor, max_retries=10):
    logging.info("Starting Rewire (Configuration Model) matching algorithm...")

    stubs = []
    for node_iid, deg in out_degs.items():
        stubs.extend([node_iid] * int(deg))

    if len(stubs) % 2 != 0:
        logging.warning(
            "Odd number of total missing stubs. Dropping one to maintain parity."
        )
        stubs.pop()

    logging.info(
        f"Total missing stubs to pair: {len(stubs)} (Target edges: {len(stubs)//2})"
    )
    random.shuffle(stubs)

    valid_edges = set()
    invalid_edges = deque()

    def make_edge(u, v):
        return (int(min(u, v)), int(max(u, v)))

    for i in range(0, len(stubs), 2):
        u, v = stubs[i], stubs[i + 1]
        e = make_edge(u, v)

        if u == v or e in valid_edges or v in exist_neighbor.get(u, set()):
            invalid_edges.append(e)
        else:
            valid_edges.add(e)

    logging.info(
        f"Initial pairing complete -> Valid edges: {len(valid_edges)} | Bad edges to rewire: {len(invalid_edges)}"
    )

    valid_pool = list(valid_edges)

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

            e1 = invalid_edges.popleft()

            if not valid_pool:
                invalid_edges.append(e1)
                break

            idx = random.randrange(len(valid_pool))
            e2 = valid_pool[idx]

            if random.random() < 0.5:
                new_e1 = make_edge(e1[0], e2[0])
                new_e2 = make_edge(e1[1], e2[1])
            else:
                new_e1 = make_edge(e1[0], e2[1])
                new_e2 = make_edge(e1[1], e2[0])

            def is_valid(e):
                u, v = e
                return (
                    u != v
                    and e not in valid_edges
                    and v not in exist_neighbor.get(u, set())
                )

            if is_valid(new_e1) and is_valid(new_e2) and new_e1 != new_e2:
                valid_edges.remove(e2)
                valid_pool[idx] = valid_pool[-1]
                valid_pool.pop()

                valid_edges.add(new_e1)
                valid_edges.add(new_e2)
                valid_pool.append(new_e1)
                valid_pool.append(new_e2)
            else:
                invalid_edges.append(e1)

        logging.info(
            f"After attempt {attempt + 1}: {len(invalid_edges)} bad edges remain."
        )

    if invalid_edges:
        logging.warning(
            f"Finished {max_retries} retries. {len(invalid_edges)} bad edges remain unresolved and will be dropped."
        )

    return valid_edges


def export_degree_matched_edgelist(degree_edges, node_iid2id, output_dir):
    df_out = pd.DataFrame(
        [(node_iid2id[src], node_iid2id[tgt]) for src, tgt in degree_edges],
        columns=["source", "target"],
    )
    df_out.to_csv(output_dir / "degree_matching_edge.csv", index=False)


def main():
    args = parse_args()
    setup_logging(Path(args.output_folder) / "run.log")
    logging.info(
        f"--- Starting Stage 6: Degree Matching ({args.algorithm.upper()} mode) ---"
    )

    start = time.perf_counter()
    node_id2iid, node_iid2id, out_degs = load_reference_topologies(
        args.ref_edgelist, args.ref_clustering
    )
    logging.info(f"Loaded reference topologies: {time.perf_counter() - start:.4f}s")

    start = time.perf_counter()
    exist_neighbor, updated_out_degs = subtract_existing_edges(
        args.input_edgelist, node_id2iid, out_degs
    )
    logging.info(f"Subtracted existing edges: {time.perf_counter() - start:.4f}s")

    start = time.perf_counter()

    # Route to the appropriate algorithm based on CLI argument
    if args.algorithm == "greedy":
        degree_edges = match_missing_degrees_greedy(updated_out_degs, exist_neighbor)
    elif args.algorithm == "true_greedy":
        degree_edges = match_missing_degrees_true_greedy(
            updated_out_degs, exist_neighbor
        )
    elif args.algorithm == "random_greedy":
        degree_edges = match_missing_degrees_random_greedy(
            updated_out_degs, exist_neighbor
        )
    else:
        degree_edges = match_missing_degrees_rewire(
            updated_out_degs, exist_neighbor, max_retries=10
        )

    logging.info(
        f"Degree matching complete. Added {len(degree_edges)} edges: {time.perf_counter() - start:.4f}s"
    )

    start = time.perf_counter()
    export_degree_matched_edgelist(degree_edges, node_iid2id, Path(args.output_folder))
    logging.info(f"Exported edgelist: {time.perf_counter() - start:.4f}s")


if __name__ == "__main__":
    main()
