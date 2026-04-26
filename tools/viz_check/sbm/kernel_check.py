"""SBM kernel cross-check.

Three legs per (fixture, seed):

1. ``gt.generate_sbm(micro_ers=True, micro_degs=True)`` produces a
   multigraph whose per-block-pair edge counts match the input ``e_rs``
   exactly and whose per-node degree matches the input ``k_v`` exactly.
2. The standalone C++ reference kernel
   (``tools/viz_check/sbm/instrumented/kernel_check.cpp``, built to
   ``/tmp/sbm_kernel_check``) produces a multigraph that satisfies the
   same invariants. Same algorithm as gt; different PRNG family
   (``std::mt19937`` vs gt's ``pcg64_k1024``), so specific edges differ.
3. The JS port (``kernel_check.mjs``) consumes the C++ trace and
   replays edges byte-for-byte equal to the C++ output. This is the
   faithful-replay bar: ``js_replay_edges == cpp_edges``.

Byte-equality vs gt itself is out of scope for this check (would require
porting pcg64_k1024 or patching graph-tool source); leg 1 verifies gt
against the same algorithmic invariants instead.

Run:

    python tools/viz_check/sbm/kernel_check.py            # all fixtures
    python tools/viz_check/sbm/kernel_check.py --verbose  # dump traces

Fixtures: 20-node (matches netgen shared.js), 100-node 5-cluster random,
200-node 8-cluster random.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp


def small20_fixture() -> dict:
    """20-node, 40-edge fixture; exact match to vltanh.github.io/netgen/shared.js."""
    C1 = [1, 2, 3, 4, 5, 6, 7, 8]
    C2 = [9, 10, 11, 12, 13, 14]
    C3 = [15, 16, 17, 18]
    OUT = [19, 20]
    intra_C1 = [
        (1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4),
        (5, 6), (5, 7), (6, 7), (6, 8), (7, 8),
        (1, 5), (4, 8),
    ]
    intra_C2 = [
        (9, 10), (9, 11), (9, 12), (9, 13),
        (10, 11), (10, 12), (10, 14),
        (11, 12), (11, 14),
        (12, 13),
    ]
    intra_C3 = [(15, 16), (15, 17), (16, 17), (16, 18)]
    inter = [
        (1, 9), (2, 10), (3, 11), (5, 12),
        (9, 15), (11, 16),
        (1, 15), (4, 17),
    ]
    out_edges = [(19, 1), (19, 9), (20, 5), (20, 16), (19, 20)]
    edges = intra_C1 + intra_C2 + intra_C3 + inter + out_edges
    nodes = C1 + C2 + C3 + OUT
    cluster_of = {}
    for n in C1: cluster_of[n] = "C1"
    for n in C2: cluster_of[n] = "C2"
    for n in C3: cluster_of[n] = "C3"
    for n in OUT: cluster_of[n] = "OUT"
    block_order = ["C1", "C2", "C3", "OUT"]
    return _build_fixture("small20", nodes, edges, cluster_of, block_order)


def random_fixture(name: str, n: int, k: int, seed: int) -> dict:
    """Random fixture: n nodes, k clusters, dense intra + sparse inter."""
    rng = random.Random(seed)
    sizes = [n // k] * k
    for i in range(n - sum(sizes)): sizes[i] += 1
    nodes = list(range(1, n + 1))
    cluster_of = {}
    blocks_nodes = {}
    cur = 0
    for ci in range(k):
        name_c = f"C{ci+1}"
        blocks_nodes[name_c] = []
        for _ in range(sizes[ci]):
            cluster_of[nodes[cur]] = name_c
            blocks_nodes[name_c].append(nodes[cur])
            cur += 1
    edges = set()
    p_intra = 0.30
    p_inter = 0.02
    for i in range(n):
        for j in range(i + 1, n):
            u, v = nodes[i], nodes[j]
            same = cluster_of[u] == cluster_of[v]
            p = p_intra if same else p_inter
            if rng.random() < p:
                edges.add((u, v))
    block_order = sorted(blocks_nodes.keys(), key=lambda c: -len(blocks_nodes[c]))
    return _build_fixture(name, nodes, sorted(edges), cluster_of, block_order)


def _build_fixture(name, nodes, edges, cluster_of, block_order):
    # iid: nodes sorted by (-degree, id) (mirrors compute_node_degree).
    degree = {n: 0 for n in nodes}
    for u, v in edges:
        degree[u] += 1
        degree[v] += 1
    nodes_iid = sorted(nodes, key=lambda n: (-degree[n], n))
    iid_of = {n: i for i, n in enumerate(nodes_iid)}
    # cluster iid: block_order is already size-desc + id-asc on ties.
    cluster_iid = {c: i for i, c in enumerate(block_order)}
    blocks = [cluster_iid[cluster_of[n]] for n in nodes_iid]
    degrees = [degree[n] for n in nodes_iid]
    B = len(block_order)
    e_full = np.zeros((B, B), dtype=np.int64)
    for u, v in edges:
        ru, rv = cluster_iid[cluster_of[u]], cluster_iid[cluster_of[v]]
        # profile_common.compute_edge_count walks every directed (u,v) pair.
        e_full[ru, rv] += 1
        e_full[rv, ru] += 1
    return {
        "name": name,
        "nodes_iid": nodes_iid,    # iid -> node id
        "iid_of": iid_of,          # node id -> iid
        "blocks": blocks,          # iid -> block iid
        "degrees": degrees,        # iid -> degree
        "block_order": block_order,
        "cluster_iid": cluster_iid,
        "edges_input": edges,
        "e_rs": e_full,
        "B": B,
    }


def gt_run(fixture: dict, seed: int):
    """Run gt.generate_sbm and return achieved (e_rs, degrees, edges)."""
    import graph_tool.all as gt
    gt.seed_rng(seed)
    np.random.seed(seed)
    blocks = np.array(fixture["blocks"], dtype="int64")
    degrees = np.array(fixture["degrees"], dtype="int64")
    probs = sp.csr_matrix(fixture["e_rs"])
    g = gt.generate_sbm(
        blocks, probs,
        out_degs=degrees,
        micro_ers=True, micro_degs=True,
        directed=False,
    )
    edges_iid = [(int(s), int(t)) for s, t in g.iter_edges()]
    return _achieved(blocks, degrees, edges_iid, fixture["B"]), edges_iid


def _fixture_payload(fixture, seed):
    triples = []
    e = fixture["e_rs"]
    for r in range(fixture["B"]):
        for s in range(fixture["B"]):
            if e[r, s]: triples.append([int(r), int(s), int(e[r, s])])
    return {
        "blocks": list(fixture["blocks"]),
        "degrees": list(fixture["degrees"]),
        "e_rs": triples,
        "num_blocks": fixture["B"],
        "seed": int(seed),
    }


def cpp_run(fixture: dict, seed: int, binary: Path) -> tuple[dict, list]:
    """Invoke the standalone C++ kernel with the same fixture."""
    proc = subprocess.run(
        [str(binary)], input=json.dumps(_fixture_payload(fixture, seed)),
        capture_output=True, text=True, check=True,
    )
    out = json.loads(proc.stdout)
    edges_iid = [tuple(e) for e in out["edges"]]
    return _achieved(
        np.array(fixture["blocks"]),
        np.array(fixture["degrees"]),
        edges_iid,
        fixture["B"],
    ), out


def js_run(fixture: dict, seed: int, mjs: Path, replay=None) -> dict:
    """Invoke the JS kernel port via node. If `replay` is given, the JS
    sampler bypasses its PRNG and consumes the supplied (i_a, i_b) draws."""
    payload = _fixture_payload(fixture, seed)
    if replay is not None:
        payload["replay"] = replay
    proc = subprocess.run(
        ["node", str(mjs)], input=json.dumps(payload),
        capture_output=True, text=True, check=True,
    )
    out = json.loads(proc.stdout)
    edges_iid = [tuple(e) for e in out["edges"]]
    ach = _achieved(
        np.array(fixture["blocks"]),
        np.array(fixture["degrees"]),
        edges_iid,
        fixture["B"],
    )
    return ach, out


def _achieved(blocks, degrees, edges_iid, B):
    ach_e = np.zeros((B, B), dtype=np.int64)
    ach_deg = np.zeros(len(blocks), dtype=np.int64)
    multi = 0
    loops = 0
    seen = set()
    for u, v in edges_iid:
        ach_deg[u] += 1
        ach_deg[v] += 1
        ru, rv = blocks[u], blocks[v]
        if ru == rv:
            ach_e[ru, ru] += 2
        else:
            ach_e[ru, rv] += 1
            ach_e[rv, ru] += 1
        if u == v:
            loops += 1
        else:
            key = (min(u, v), max(u, v))
            if key in seen: multi += 1
            else: seen.add(key)
    return {
        "e_rs": ach_e,
        "degree": ach_deg,
        "loops": loops,
        "multi": multi,
        "edges": len(edges_iid),
    }


def check_invariants(name: str, fixture: dict, ach: dict) -> bool:
    expected_e = fixture["e_rs"]
    expected_deg = np.array(fixture["degrees"])
    e_match = np.array_equal(expected_e, ach["e_rs"])
    d_match = np.array_equal(expected_deg, ach["degree"])
    edges_match = ach["edges"] == int(expected_e.sum() // 2)
    print(
        f"  {name:>10s}: edges={ach['edges']:>3d} "
        f"loops={ach['loops']:>2d} multi={ach['multi']:>2d}  "
        f"e_rs_exact={e_match}  deg_exact={d_match}  "
        f"edge_total_exact={edges_match}"
    )
    return e_match and d_match and edges_match


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=list(range(1, 11)))
    ap.add_argument("--cpp-binary", default="/tmp/sbm_kernel_check")
    ap.add_argument("--js-mjs", default=str(Path(__file__).with_name("kernel_check.mjs")))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    binary = Path(args.cpp_binary)
    if not binary.exists():
        print(f"FAIL: cpp binary not at {binary}; build first", file=sys.stderr)
        sys.exit(2)

    fixtures = [
        small20_fixture(),
        random_fixture("rand100_5c", n=100, k=5, seed=42),
        random_fixture("rand200_8c", n=200, k=8, seed=43),
    ]

    overall = True
    for fx in fixtures:
        print(f"\n=== fixture: {fx['name']} (N={len(fx['blocks'])}, B={fx['B']}, "
              f"E={int(fx['e_rs'].sum() // 2)}) ===")
        for seed in args.seeds:
            print(f"\nseed={seed}")
            try:
                gt_ach, gt_edges = gt_run(fx, seed)
                gt_ok = check_invariants("gt.gen_sbm", fx, gt_ach)
            except Exception as exc:
                print(f"  gt.gen_sbm: FAIL ({exc})")
                gt_ok = False

            cpp_ach, cpp_out = cpp_run(fx, seed, binary)
            cpp_ok = check_invariants("cpp ref", fx, cpp_ach)

            js_ach, js_out = js_run(fx, seed, Path(args.js_mjs))
            js_ok = check_invariants("js (node)", fx, js_ach)

            # Derandomized cross-check: feed the C++ trace's (i_a, i_b)
            # stream into the JS sampler. JS bypasses its PRNG and
            # replays the C++ draws verbatim; output must equal C++.
            replay_draws = [{"i_a": t["i_a"], "i_b": t["i_b"]} for t in cpp_out["trace"]]
            jsr_ach, jsr_out = js_run(fx, seed, Path(args.js_mjs), replay=replay_draws)
            jsr_ok = check_invariants("js replay", fx, jsr_ach)
            jsr_edge_match = (jsr_out["edges"] == cpp_out["edges"])
            print(f"  js replay edges == cpp:     {jsr_edge_match}")

            # Per-pair mrs comparison (same fixture, all three should agree
            # on the mrs schedule even though specific edges differ).
            cpp_pairs = sorted(((p["r"], p["s"]), p["mrs"]) for p in cpp_out["pairs"])
            expected_mrs = []
            e = fx["e_rs"]
            for r in range(fx["B"]):
                for s in range(r, fx["B"]):
                    if e[r, s] == 0: continue
                    mrs = (e[r, s] // 2) if r == s else int(e[r, s])
                    expected_mrs.append(((r, s), mrs))
            expected_mrs.sort()
            pair_ok = (cpp_pairs == expected_mrs)
            print(f"  cpp pair plan exact:        {pair_ok}")

            if args.verbose and seed == args.seeds[0]:
                print("    first 5 cpp trace entries:")
                for t in cpp_out["trace"][:5]:
                    print(f"      step={t['step']} pair=({t['r']},{t['s']}) "
                          f"urn_r_size={t['urnR']} i_a={t['i_a']} "
                          f"urn_s_size={t['urnS']} i_b={t['i_b']} "
                          f"-> ({t['u']}, {t['v']})")
                print("    first 5 js trace entries:")
                for t in js_out["trace_first5"]:
                    print(f"      step={t['step']} pair=({t['r']},{t['s']}) "
                          f"urn_r_size={t['urnR']} i_a={t['i_a']} "
                          f"urn_s_size={t['urnS']} i_b={t['i_b']} "
                          f"-> ({t['u']}, {t['v']})")

            overall = overall and gt_ok and cpp_ok and js_ok and jsr_ok and jsr_edge_match and pair_ok

    print("\n" + ("=" * 60))
    print("OVERALL: " + ("PASS" if overall else "FAIL"))
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
