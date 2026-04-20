"""Generator-agnostic profiling primitives.

Each generator has its own `profile.py` under `src/<gen>/` (and
`src/ec-sbm/common/profile.py` for the v1+v2 shared ecsbm profile).
Those modules compose the building blocks below to produce their
generator-specific output set.

Deps: stdlib + pandas only.  numpy is pulled in lazily inside the
lfr mixing-parameter branch (the only numpy call site in profiling);
pymincut lives with the ec-sbm profile module, not here.
"""
from __future__ import annotations

import logging
from collections import defaultdict

import pandas as pd


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

def compute_node_degree(nodes, neighbors):
    """Return nodes sorted by degree descending and a node_id → iid mapping.

    Tie-break on node id ascending so the output is stable across processes
    (iteration over a Python set depends on PYTHONHASHSEED).
    """
    node_degree_sorted = sorted(
        ((u, len(neighbors[u])) for u in nodes), key=lambda x: (-x[1], x[0])
    )
    node_id2iid = {u: i for i, (u, _) in enumerate(node_degree_sorted)}
    return node_degree_sorted, node_id2iid


def compute_comm_size(cluster_counts):
    """Return clusters sorted by size descending and a cluster_id → iid mapping.

    Tie-break on cluster id ascending for cross-process stability.
    """
    comm_size_sorted = sorted(
        cluster_counts.items(), key=lambda x: (-x[1], x[0])
    )
    cluster_id2iid = {c: i for i, (c, _) in enumerate(comm_size_sorted)}
    return comm_size_sorted, cluster_id2iid


# ---------------------------------------------------------------------------
# Exporters — pass-through dumps of the precomputed structures
# ---------------------------------------------------------------------------

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


def export_com_csv(out_dir, node2com):
    """Write node_id,cluster_id pairs to com.csv in input-clustering row order.

    `node2com` is built from `dict(zip(...))` over the input CSV and (for ecsbm)
    pruned in place by the pre-profile hook, so iteration order traces back to
    the input file's row order. Matches the pass-through convention used by
    sbm/abcd/abcd+o/lfr/npso, which preserve their source's row order.
    """
    pd.DataFrame(node2com.items(), columns=["node_id", "cluster_id"]).to_csv(
        f"{out_dir}/com.csv", index=False
    )


# ---------------------------------------------------------------------------
# Edge-count matrix (sbm + ec-sbm share this)
# ---------------------------------------------------------------------------

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
    """Write (row, col, weight) triples to edge_counts.csv (no header).

    Rows are sorted by (row iid, col iid) so the output is stable regardless
    of dict insertion order (which reflects the upstream node-iteration order).
    """
    data = [[r, c, w] for (r, c), w in sorted(edge_counts.items())]
    pd.DataFrame(data).to_csv(f"{out_dir}/edge_counts.csv", index=False, header=False)


# ---------------------------------------------------------------------------
# Mixing parameter (abcd / abcd+o / lfr variants)
# ---------------------------------------------------------------------------

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
        # np is imported lazily so abcd/abcd+o/npso profilers stay numpy-free.
        # Sort for cross-process determinism: floating-point sum varies with
        # summation order even though the mean is mathematically order-free.
        import numpy as np

        mus = [
            out_degree[u] / (in_degree[u] + out_degree[u])
            for u in sorted(nodes)
        ]
        return float(np.mean(mus))
    else:
        outs_sum = sum(out_degree.values())
        total_sum = outs_sum + sum(in_degree.values())
        return outs_sum / total_sum


def export_cluster_sizes_with_singleton_outliers(out_dir, comm_size_sorted, n_outliers):
    """Write cluster sizes with `n_outliers` appended as size-1 clusters.

    Shared by abcd/lfr/npso which treat outliers implicitly as singletons.
    """
    cs_with_outliers = list(comm_size_sorted) + [
        (f"outlier_{i}", 1) for i in range(n_outliers)
    ]
    export_comm_size(out_dir, cs_with_outliers)
