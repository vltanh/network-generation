import logging
import argparse
from collections import defaultdict

import numpy as np
import pandas as pd
from pymincut.pygraph import PyGraph

from pipeline_common import standard_setup, timed


def read_clustering(clustering_path):
    """
    Read a clustering CSV and return node membership structures.

    Returns:
        nodes: Set of node IDs that appear in the clustering.
        node2com: Dict mapping node_id (str) to cluster_id (str).
        cluster_counts: Dict mapping cluster_id to its member count.
    """
    df = pd.read_csv(clustering_path, usecols=[0, 1], dtype=str).dropna()

    node2com = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
    cluster_counts = df.iloc[:, 1].value_counts().to_dict()
    nodes = set(node2com.keys())

    return nodes, node2com, cluster_counts


def read_edgelist(edgelist_path, nodes):
    """
    Read an edgelist CSV into a bidirectional adjacency structure.

    Self-loops are ignored.  `nodes` is extended in-place with any network
    nodes that were absent from the clustering (true outliers).

    Returns:
        nodes: Updated set containing all nodes in clustering ∪ edgelist.
        neighbors: defaultdict(set) of undirected adjacency lists.
    """
    neighbors = defaultdict(set)
    df = pd.read_csv(edgelist_path, usecols=[0, 1], dtype=str).dropna()

    for u, v in zip(df.iloc[:, 0], df.iloc[:, 1]):
        if u != v:  # Ignore self-loops
            neighbors[u].add(v)
            neighbors[v].add(u)
            nodes.add(u)
            nodes.add(v)

    return nodes, neighbors


def compute_node_degree(nodes, neighbors):
    """Return nodes sorted by degree descending and a node_id → iid mapping."""
    node_degree_sorted = sorted(
        [(u, len(neighbors[u])) for u in nodes], reverse=True, key=lambda x: x[1]
    )
    node_id2iid = {u: i for i, (u, _) in enumerate(node_degree_sorted)}
    return node_degree_sorted, node_id2iid


def compute_comm_size(cluster_counts):
    """Return clusters sorted by size descending and a cluster_id → iid mapping."""
    comm_size_sorted = sorted(cluster_counts.items(), reverse=True, key=lambda x: x[1])
    cluster_id2iid = {c: i for i, (c, _) in enumerate(comm_size_sorted)}
    return comm_size_sorted, cluster_id2iid


def export_node_id(out_dir, node_degree_sorted):
    """Write node IDs in degree-descending order to node_id.csv (no header)."""
    pd.DataFrame([u for u, _ in node_degree_sorted]).to_csv(
        f"{out_dir}/node_id.csv", index=False, header=False
    )


def export_cluster_id(out_dir, comm_size_sorted):
    """Write cluster IDs in size-descending order to cluster_id.csv (no header)."""
    pd.DataFrame([c for c, _ in comm_size_sorted]).to_csv(
        f"{out_dir}/cluster_id.csv", index=False, header=False
    )


def export_assignment(out_dir, node_degree_sorted, node2com, cluster_id2iid):
    """
    Write per-node cluster iid to assignment.csv (no header).
    Unclustered nodes (true outliers) are assigned -1.
    """
    assignments = [
        cluster_id2iid[node2com.get(u)] if u in node2com else -1
        for u, _ in node_degree_sorted
    ]
    pd.DataFrame(assignments).to_csv(
        f"{out_dir}/assignment.csv", index=False, header=False
    )


def export_degree(out_dir, node_degree_sorted):
    """Write per-node degree values (aligned with node_id.csv order) to degree.csv."""
    pd.DataFrame([deg for _, deg in node_degree_sorted]).to_csv(
        f"{out_dir}/degree.csv", index=False, header=False
    )


def compute_edge_count(nodes, neighbors, node2com, cluster_id2iid):
    """
    Count directed inter-cluster edge occurrences for every cluster pair (c_i, c_j).

    Both directions are counted independently (probs[i,j] and probs[j,i]),
    matching the dok_matrix convention used by gen_clustered.py.
    Edges incident to unclustered nodes are ignored.
    """
    edge_counts = defaultdict(int)
    for u in nodes:
        cu = node2com.get(u)
        if cu is None:
            continue
        c_iid_u = cluster_id2iid[cu]

        for v in neighbors[u]:
            cv = node2com.get(v)
            if cv is not None:
                c_iid_v = cluster_id2iid[cv]
                edge_counts[(c_iid_u, c_iid_v)] += 1
    return edge_counts


def export_edge_count(out_dir, edge_counts):
    """Write (row, col, weight) triples to edge_counts.csv (no header)."""
    data = [[r, c, w] for (r, c), w in edge_counts.items()]
    pd.DataFrame(data).to_csv(f"{out_dir}/edge_counts.csv", index=False, header=False)


def compute_mincut(nodes, neighbors, node2com, comm_size_sorted, node_id2iid):
    """
    Compute the minimum edge cut for every cluster's induced subgraph.

    For each cluster, the induced subgraph (only intra-cluster edges) is
    passed to PyGraph.mincut.  Single-node clusters get min-cut 0.  The
    result list is aligned with comm_size_sorted (index = cluster iid).
    """
    clusters_by_id = defaultdict(list)
    for u, c in node2com.items():
        clusters_by_id[c].append(u)

    mcs = []
    for c, _ in comm_size_sorted:
        c_nodes_str = clusters_by_id[c]

        if len(c_nodes_str) <= 1:
            mcs.append([0])
            continue

        c_nodes_iid = [node_id2iid[u] for u in c_nodes_str]
        c_nodes_set = set(c_nodes_iid)
        c_edges = []

        for u in c_nodes_str:
            u_iid = node_id2iid[u]
            for v in neighbors[u]:
                v_iid = node_id2iid.get(v)
                if v_iid is not None and v_iid in c_nodes_set:
                    c_edges.append((u_iid, v_iid))

        sub_G = PyGraph(c_nodes_iid, c_edges)
        min_cut = sub_G.mincut("noi", "bqueue", False)[2]
        mcs.append([min_cut])

    return mcs


def export_mincut(out_dir, mcs):
    """Write per-cluster min-cut values (aligned with cluster_id.csv order) to mincut.csv."""
    pd.DataFrame(mcs).to_csv(f"{out_dir}/mincut.csv", index=False, header=False)


def compute_mixing_parameter(nodes, neighbors, node2com, generator_type):
    """
    Compute the empirical mixing parameter that matches each generator's model
    of how outliers relate to clusters:

      "lfr"    — outliers become singleton clusters in the synthetic output, so
                 each outlier is in its own cluster: every incident edge is
                 cross-cluster (out).  Reduction: mean of per-node µ_i.
      "abcd"   — same singleton-cluster model as LFR; reduction is global
                 ξ = Σ_out / Σ_total.
      "abcd+o" — outliers cannot connect to each other in the model, so
                 outlier-outlier edges are dropped entirely (not counted as in
                 or out); clustered↔outlier counts as out.  Reduction is
                 global ξ over the remaining edges.

    In LFR/ABCD/ABCD+o, clustered↔outlier edges count as out on both endpoints.
    """
    treat_outliers_as_singletons = generator_type in ("lfr", "abcd")

    in_degree = defaultdict(int)
    out_degree = defaultdict(int)

    for u in nodes:
        u_clustered = u in node2com
        for v in neighbors[u]:
            v_clustered = v in node2com

            if not u_clustered and not v_clustered:
                # Two outliers: singletons (lfr/abcd) → different clusters (out);
                # abcd+o → edge forbidden by the model, skip entirely.
                if treat_outliers_as_singletons:
                    out_degree[u] += 1
                # else: abcd+o drops outlier-outlier edges
            elif not u_clustered or not v_clustered:
                out_degree[u] += 1
            elif node2com[u] == node2com[v]:
                in_degree[u] += 1
            else:
                out_degree[u] += 1

    if generator_type == "lfr":
        mus = [
            out_degree[u] / (in_degree[u] + out_degree[u])
            for u in nodes
        ]
        return float(np.mean(mus))
    else:
        outs_sum = sum(out_degree.values())
        total_sum = outs_sum + sum(in_degree.values())
        return outs_sum / total_sum


def export_comm_size(out_dir, comm_size_sorted):
    """Write cluster sizes (aligned with cluster_id.csv order) to cluster_sizes.csv."""
    pd.DataFrame([size for _, size in comm_size_sorted]).to_csv(
        f"{out_dir}/cluster_sizes.csv", index=False, header=False
    )


def export_mixing_param(out_dir, mixing_param):
    """Write the scalar mixing parameter to mixing_parameter.txt."""
    with open(f"{out_dir}/mixing_parameter.txt", "w") as f:
        f.write(str(mixing_param))


def export_n_outliers(out_dir, n_outliers):
    """Write the scalar outlier count to n_outliers.txt."""
    with open(f"{out_dir}/n_outliers.txt", "w") as f:
        f.write(str(n_outliers))


SBM_OUTLIER_CLUSTER_ID = "__outliers__"


# --- Per-generator pre-profile hooks -----------------------------------
# Run before node-degree / cluster-size compute.  May mutate node2com and
# cluster_counts in place.  Return nothing.

def _preprofile_sbm(nodes, node2com, cluster_counts):
    # SBM routes outlier edges through the same block structure as the rest
    # of the network by folding true outliers into one mega-cluster.
    outliers = [u for u in nodes if u not in node2com]
    if outliers:
        for u in outliers:
            node2com[u] = SBM_OUTLIER_CLUSTER_ID
        cluster_counts[SBM_OUTLIER_CLUSTER_ID] = len(outliers)


# --- Per-generator exporters -------------------------------------------
# Each takes the pre-computed context and writes its generator's outputs.
# Context fields:
#   output_dir        — destination directory
#   nodes, neighbors  — network structure
#   node2com          — possibly mutated by a pre-profile hook
#   node_deg_sorted   — [(node_id, degree)] sorted by degree desc
#   comm_size_sorted  — [(cluster_id, size)] sorted by size desc
#   node_id2iid       — node_id (str) → integer iid
#   cluster_id2iid    — cluster_id (str) → integer iid
#   n_outliers        — count of nodes not in node2com

def _export_sbm(ctx):
    _export_sbm_core(ctx)


def _export_ecsbm(ctx):
    _export_sbm_core(ctx)
    mcs = compute_mincut(
        ctx["nodes"], ctx["neighbors"], ctx["node2com"],
        ctx["comm_size_sorted"], ctx["node_id2iid"],
    )
    export_mincut(ctx["output_dir"], mcs)


def _export_sbm_core(ctx):
    out = ctx["output_dir"]
    export_node_id(out, ctx["node_deg_sorted"])
    export_cluster_id(out, ctx["comm_size_sorted"])
    export_assignment(
        out, ctx["node_deg_sorted"], ctx["node2com"], ctx["cluster_id2iid"],
    )
    export_degree(out, ctx["node_deg_sorted"])
    edge_counts = compute_edge_count(
        ctx["nodes"], ctx["neighbors"], ctx["node2com"], ctx["cluster_id2iid"],
    )
    export_edge_count(out, edge_counts)


def _export_abcd(ctx):
    # ABCD treats outliers as singleton clusters — fold them into
    # cluster_sizes so gen.py doesn't need a separate count.
    export_degree(ctx["output_dir"], ctx["node_deg_sorted"])
    _export_cluster_sizes_with_singleton_outliers(ctx)
    _export_mixing_parameter_for(ctx, "abcd")


def _export_abcd_o(ctx):
    # ABCD+o forbids outlier-outlier edges, so each outlier's reported
    # degree is its count of clustered neighbors only.
    nodes = ctx["nodes"]
    neighbors = ctx["neighbors"]
    node2com = ctx["node2com"]
    outlier_degrees = {
        u: sum(1 for v in neighbors[u] if v in node2com)
        for u in nodes if u not in node2com
    }
    adjusted_deg = sorted(
        (
            (u, outlier_degrees[u] if u in outlier_degrees else d)
            for u, d in ctx["node_deg_sorted"]
        ),
        key=lambda x: x[1], reverse=True,
    )
    out = ctx["output_dir"]
    export_degree(out, adjusted_deg)
    export_comm_size(out, ctx["comm_size_sorted"])
    export_n_outliers(out, ctx["n_outliers"])
    _export_mixing_parameter_for(ctx, "abcd+o")


def _export_lfr(ctx):
    # LFR treats outliers implicitly as singletons.
    export_degree(ctx["output_dir"], ctx["node_deg_sorted"])
    _export_cluster_sizes_with_singleton_outliers(ctx)
    _export_mixing_parameter_for(ctx, "lfr")


def _export_npso(ctx):
    # nPSO: same singleton-outlier treatment as LFR but no mixing parameter.
    export_degree(ctx["output_dir"], ctx["node_deg_sorted"])
    _export_cluster_sizes_with_singleton_outliers(ctx)


def _export_cluster_sizes_with_singleton_outliers(ctx):
    cs_with_outliers = list(ctx["comm_size_sorted"]) + [
        (f"outlier_{i}", 1) for i in range(ctx["n_outliers"])
    ]
    export_comm_size(ctx["output_dir"], cs_with_outliers)


def _export_mixing_parameter_for(ctx, generator_type):
    mixing_param = compute_mixing_parameter(
        ctx["nodes"], ctx["neighbors"], ctx["node2com"], generator_type,
    )
    export_mixing_param(ctx["output_dir"], mixing_param)


# --- Registry ----------------------------------------------------------
# Maps generator name to (preprofile_hook_or_None, exporter).  Adding a
# new generator means adding one entry here + per-generator functions.
_GENERATOR_REGISTRY = {
    "sbm":    (_preprofile_sbm, _export_sbm),
    "ecsbm":  (None,            _export_ecsbm),
    "abcd":   (None,            _export_abcd),
    "abcd+o": (None,            _export_abcd_o),
    "lfr":    (None,            _export_lfr),
    "npso":   (None,            _export_npso),
}


def setup_generator_inputs(edgelist_path, clustering_path, output_dir, generator):
    """
    Profile an empirical network and write only the files each generator needs.

    Per-generator outputs:
      sbm    → node_id, cluster_id, assignment, degree, edge_counts
               (outliers are folded into a single mega-cluster)
      ecsbm  → node_id, cluster_id, assignment, degree, edge_counts, mincut
      abcd   → degree, cluster_sizes, mixing_parameter
               (outliers are folded into cluster_sizes as singletons)
      abcd+o → degree, cluster_sizes, mixing_parameter, n_outliers
               (outlier degrees are the count of *clustered* neighbors only —
               the model forbids outlier-outlier edges)
      lfr    → degree, cluster_sizes, mixing_parameter
      npso   → degree, cluster_sizes
    """
    if generator not in _GENERATOR_REGISTRY:
        raise ValueError(
            f"Unknown generator {generator!r}; "
            f"expected one of {sorted(_GENERATOR_REGISTRY)}"
        )
    preprofile, exporter = _GENERATOR_REGISTRY[generator]

    output_dir = standard_setup(output_dir)

    with timed("Input reading"):
        nodes, node2com, cluster_counts = read_clustering(clustering_path)
        nodes, neighbors = read_edgelist(edgelist_path, nodes)

    if preprofile is not None:
        preprofile(nodes, node2com, cluster_counts)

    with timed("Mappings computation"):
        node_deg_sorted, node_id2iid = compute_node_degree(nodes, neighbors)
        comm_size_sorted, cluster_id2iid = compute_comm_size(cluster_counts)

    ctx = {
        "output_dir": output_dir,
        "nodes": nodes,
        "neighbors": neighbors,
        "node2com": node2com,
        "node_deg_sorted": node_deg_sorted,
        "comm_size_sorted": comm_size_sorted,
        "node_id2iid": node_id2iid,
        "cluster_id2iid": cluster_id2iid,
        "n_outliers": sum(1 for u in nodes if u not in node2com),
    }

    with timed("Outputs export"):
        exporter(ctx)

    logging.info("Setup complete.")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--edgelist", type=str, required=True)
    parser.add_argument("--clustering", type=str, required=True)
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument(
        "--generator",
        type=str,
        default="ecsbm",
        choices=sorted(_GENERATOR_REGISTRY.keys()),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    setup_generator_inputs(
        args.edgelist, args.clustering, args.output_folder, args.generator
    )


if __name__ == "__main__":
    main()
