"""Generator-agnostic profiling primitives.

Each generator has its own `profile.py` under `src/<gen>/` (and
`src/ec-sbm/common/profile.py` for the v1+v2 shared ecsbm profile).
Those modules compose the building blocks below to produce their
generator-specific output set.

Outlier handling is a two-step preprocessing pipeline applied to the
output of `read_clustering` + `read_edgelist`:

  A. `identify_outliers` — unified definition: an outlier is any node
     that is unclustered OR assigned to a size-1 cluster. Mutates
     `node2com`/`cluster_counts` in place to demote size-1 clusters
     into the outlier pool.
  B. `apply_outlier_mode` — orthogonal transform over two dimensions:
       - mode ∈ {excluded, singleton, combined} (cluster shape)
       - drop_outlier_outlier_edges (OO-edge handling)

After these two steps, the remaining profile primitives see a clean
`(nodes, node2com, cluster_counts, neighbors)` whose outlier semantics
are already baked in; mu, edge counts, mincut etc. are generator-only.

Deps: stdlib + pandas only.  numpy is pulled in lazily inside the
lfr mixing-parameter branch (the only numpy call site in profiling);
pymincut lives with the ec-sbm profile module, not here.
"""
from __future__ import annotations

import logging
from collections import defaultdict

import pandas as pd


OUTLIER_MODES = ("excluded", "singleton", "combined")
COMBINED_OUTLIER_CLUSTER_ID = "__outliers__"


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
# Outlier identification + mode transform
# ---------------------------------------------------------------------------

def identify_outliers(nodes, node2com, cluster_counts):
    """Unified outlier identification — Step A of the profile pipeline.

    An outlier is any node that is unclustered OR assigned to a size-1
    cluster in the input. `node2com` and `cluster_counts` are mutated in
    place: every size-1 cluster is removed from `cluster_counts`, and its
    member node is removed from `node2com`. After this call, downstream
    code sees a single pool of unclustered nodes (unclustered-in-input ∪
    formerly-size-1-clustered), which is the input `apply_outlier_mode`
    expects.

    Returns:
        outliers: set of outlier node IDs.
    """
    outliers = {u for u in nodes if u not in node2com}
    singleton_clusters = [c for c, sz in cluster_counts.items() if sz == 1]
    for c in singleton_clusters:
        del cluster_counts[c]
    for u, c in list(node2com.items()):
        if c not in cluster_counts:
            del node2com[u]
            outliers.add(u)
    return outliers


def apply_outlier_mode(nodes, node2com, cluster_counts, neighbors, outliers,
                       mode, drop_outlier_outlier_edges=False):
    """Transform profile inputs per the chosen outlier mode — Step B.

    `outliers` is the set returned by `identify_outliers` (all nodes that
    are neither in a surviving multi-member cluster). This function
    mutates `nodes`, `node2com`, `cluster_counts`, and `neighbors` in
    place.

    If `drop_outlier_outlier_edges` is True, every outlier-outlier edge
    is pruned from `neighbors` first. This is a no-op under `excluded`
    mode (where OO edges are dropped anyway along with the outliers).

    Modes:
      - excluded:  drop outliers from `nodes`; drop every edge incident
                   to an outlier from `neighbors`. Profile sees a strictly
                   clustered subgraph.
      - singleton: give each outlier its own fresh cluster id of the form
                   `__outlier_<nodeid>__`, size 1. Every edge incident to
                   an outlier is inter-cluster.
      - combined:  fold all outliers into one shared cluster id
                   (`__outliers__`) of size |outliers|. Outlier-outlier
                   edges become intra-cluster.
    """
    if mode not in OUTLIER_MODES:
        raise ValueError(
            f"unknown outlier mode: {mode!r}; expected one of {OUTLIER_MODES}"
        )

    if drop_outlier_outlier_edges and mode != "excluded":
        for u in outliers:
            if u in neighbors:
                neighbors[u] = {v for v in neighbors[u] if v not in outliers}

    if mode == "excluded":
        for u in outliers:
            nodes.discard(u)
            if u in neighbors:
                del neighbors[u]
        for v in list(neighbors):
            neighbors[v] = {w for w in neighbors[v] if w not in outliers}
    elif mode == "singleton":
        for u in outliers:
            cid = f"__outlier_{u}__"
            node2com[u] = cid
            cluster_counts[cid] = 1
    elif mode == "combined":
        if outliers:
            for u in outliers:
                node2com[u] = COMBINED_OUTLIER_CLUSTER_ID
            cluster_counts[COMBINED_OUTLIER_CLUSTER_ID] = len(outliers)


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


def export_outlier_mode(out_dir, mode, drop_outlier_outlier_edges):
    """Write the chosen outlier handling to outlier_mode.txt.

    Two lines: mode, then drop_outlier_outlier_edges as lowercase true/false.
    """
    with open(f"{out_dir}/outlier_mode.txt", "w") as f:
        f.write(f"{mode}\n{'true' if drop_outlier_outlier_edges else 'false'}\n")


def read_outlier_mode(path):
    """Read an outlier_mode.txt produced by `export_outlier_mode`.

    Returns (mode, drop_outlier_outlier_edges) as (str, bool).
    """
    with open(path) as f:
        lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    if len(lines) != 2:
        raise ValueError(f"{path}: expected 2 non-empty lines, got {len(lines)}")
    mode, drop_oo = lines
    if mode not in OUTLIER_MODES:
        raise ValueError(f"{path}: unknown mode {mode!r}")
    if drop_oo not in ("true", "false"):
        raise ValueError(f"{path}: drop_outlier_outlier_edges must be true/false")
    return mode, drop_oo == "true"


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
# Mixing parameter
# ---------------------------------------------------------------------------

def compute_mixing_parameter(nodes, neighbors, node2com, reduction):
    """Compute the empirical mixing parameter over (nodes, neighbors, node2com).

    The outlier semantics (mode + drop_oo) are already baked into the inputs
    by `apply_outlier_mode`; this function is a clean pass that differs only
    in how it reduces per-node counts to a scalar:

      reduction="mean"   — mean of per-node µ_i = out_i / (in_i + out_i),
                           skipping 0-degree nodes. Matches LFR's convention.
      reduction="global" — global ξ = Σ_out / Σ_total. Matches ABCD/ABCD+o.

    Nodes not present in `node2com` (can happen under `excluded` mode where
    outliers have been dropped) are skipped implicitly — `neighbors` no
    longer references them. Under `singleton`/`combined`, every node is in
    `node2com` so every edge is counted.
    """
    if reduction not in ("mean", "global"):
        raise ValueError(
            f"unknown reduction: {reduction!r}; expected 'mean' or 'global'"
        )

    in_degree = defaultdict(int)
    out_degree = defaultdict(int)

    for u in nodes:
        cu = node2com.get(u)
        if cu is None:
            continue
        for v in neighbors[u]:
            cv = node2com.get(v)
            if cv is None:
                continue
            if cu == cv:
                in_degree[u] += 1
            else:
                out_degree[u] += 1

    if reduction == "mean":
        import numpy as np

        mus = []
        for u in sorted(nodes):
            total = in_degree[u] + out_degree[u]
            if total == 0:
                continue
            mus.append(out_degree[u] / total)
        if not mus:
            return 0.0
        return float(np.mean(mus))
    else:
        outs_sum = sum(out_degree.values())
        total_sum = outs_sum + sum(in_degree.values())
        if total_sum == 0:
            return 0.0
        return outs_sum / total_sum
