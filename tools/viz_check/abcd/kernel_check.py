"""ABCD kernel cross-check.

Two cross-checks per (fixture, seed) cell:

1. **Structural invariants** on the canonical Julia sampler's output
   (deg-exact, sizes-exact, simple, ξ within tol) — the original
   contract from ``memory/gen_abcd.md``.
2. **Faithful-replay byte-equality** between canonical Julia and a
   Node JS replay:
     - ``tools/viz_check/abcd/instrumented/instrumented.jl`` ports
       ``populate_clusters`` + ``config_model`` with logging hooks at
       every PRNG site, runs both canonical + instrumented in one
       process, asserts the two agree, and emits the trace.
     - ``tools/viz_check/abcd/kernel_check.mjs`` consumes the trace
       and reproduces the canonical edge set deterministically.
     - The harness asserts canonical edges == JS edges and that the
       trace cursor was fully consumed.

Drives the Julia ABCD sampler (the same `graph_sampler.jl` that
`src/abcd/gen.py` invokes) on multiple fixtures and seeds, post-processes
the result with the same `simplify_edges` + `drop_singleton_clusters`
helpers `gen.py` uses, then verifies the contract documented in
``memory/gen_abcd.md``:

  * **Exact degree sequence** — every output node's degree equals its
    input degree. (The contract says "exact if no rewiring triggers;
    slightly perturbed otherwise". When the sampler emits a "Very hard
    graph" stderr warning, this can be violated by a small handful of
    edges; we keep the check strict and surface the warning.)
  * **Exact cluster sizes** — every output cluster (post drop_singleton)
    matches an input cluster size.
  * **Global ξ within tolerance** — measured ξ on the output is within
    ±``xi_tol`` of the profile target. The per-node external split is
    randomized so this is not exact; tolerance is auto-loosened on
    tiny fixtures (E < 100) where binomial concentration is weak.
  * **Simple graph** — no self-loops, no parallel edges in `edge.csv`.
    `simplify_edges` already enforces this; we re-check as a regression
    tripwire.

Run:

    conda run -n nwbench python tools/abcd/kernel_check.py
    conda run -n nwbench python tools/abcd/kernel_check.py --seeds 1 2 3

Fixtures:
  * **small20** — 20-node 40-edge graph mirroring `vltanh.github.io/netgen/shared.js`.
  * **rand100_5c** — 100 nodes, 5 planted clusters, intra/inter Bernoulli draws.

PASS for a (fixture, seed) row means: every invariant held on the
post-processed `edge.csv` + `com.csv`. The bottom-line `OVERALL: PASS`
means every row passed; on small fixtures, expect occasional FAILs on
the strict degree check when the Julia sampler reports "Very hard graph"
(see `memory/gen_abcd.md` for the in-expectation contract).
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import random
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
ABCD_DIR = REPO_ROOT / "externals" / "abcd"
ABCD_SAMPLER = ABCD_DIR / "utils" / "graph_sampler.jl"
INSTRUMENTED_JL = Path(__file__).with_name("instrumented") / "instrumented.jl"
JS_REPLAY = Path(__file__).with_name("kernel_check.mjs")
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))


# Sampler stderr substrings worth flagging in the row output.
SAMPLER_FLAGS = [
    ("very_hard", "Very hard graph"),
    ("xi_biased", "Resulting ξ might be slightly biased"),
    ("unresolved", "Unresolved_collisions:"),
    ("outlier_lift", "outlier nodes form a community"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def small20_fixture() -> dict:
    """20-node, 40-edge fixture mirroring vltanh.github.io/netgen/shared.js.

    Cluster sizes 8/6/4 plus 2 singleton outliers (cluster_id strings
    `__outlier_19__`, `__outlier_20__`). The singletons get filtered
    by `drop_singleton_clusters` on the *output*, mirroring the real
    pipeline.
    """
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
    for n in OUT: cluster_of[n] = f"__outlier_{n}__"
    return _build_fixture("small20", nodes, edges, cluster_of)


def random_fixture(name: str, n: int, k: int, seed: int,
                   p_intra: float = 0.30, p_inter: float = 0.02) -> dict:
    """Random fixture: n nodes, k planted clusters, dense intra + sparse inter."""
    rng = random.Random(seed)
    sizes = [n // k] * k
    for i in range(n - sum(sizes)):
        sizes[i] += 1
    nodes = list(range(1, n + 1))
    cluster_of = {}
    cur = 0
    for ci in range(k):
        cname = f"C{ci+1}"
        for _ in range(sizes[ci]):
            cluster_of[nodes[cur]] = cname
            cur += 1
    edges = set()
    for i in range(n):
        for j in range(i + 1, n):
            u, v = nodes[i], nodes[j]
            same = cluster_of[u] == cluster_of[v]
            p = p_intra if same else p_inter
            if rng.random() < p:
                edges.add((u, v))
    return _build_fixture(name, nodes, sorted(edges), cluster_of)


def _build_fixture(name: str, nodes: list, edges: list, cluster_of: dict) -> dict:
    """Compute degree sequence + cluster sizes + global ξ for a fixture.

    Mirrors `src/abcd/profile.py`: descending-degree node order with
    ascending-id tiebreak, descending-size cluster order with
    ascending-id tiebreak, ξ = Σ_out / Σ_total over directed (u, v)
    walk (each undirected edge counted twice).
    """
    degree = {n: 0 for n in nodes}
    for u, v in edges:
        degree[u] += 1
        degree[v] += 1
    nodes_sorted = sorted(nodes, key=lambda n: (-degree[n], n))
    degree_seq = [degree[n] for n in nodes_sorted]
    cluster_counter = Counter(cluster_of[n] for n in nodes)
    clusters_sorted = sorted(
        cluster_counter.items(), key=lambda kv: (-kv[1], str(kv[0])),
    )
    cluster_sizes = [sz for _, sz in clusters_sorted]
    out_count = 0
    total = 0
    for u, v in edges:
        if cluster_of[u] != cluster_of[v]:
            out_count += 2
        total += 2
    xi = out_count / total if total else 0.0
    return {
        "name": name,
        "nodes": nodes_sorted,
        "degree_seq": degree_seq,
        "cluster_of": cluster_of,
        "cluster_sizes": cluster_sizes,
        "xi": xi,
        "edges_input": list(edges),
    }


# ---------------------------------------------------------------------------
# Sampler invocation
# ---------------------------------------------------------------------------


def _run_julia_sampler(deg_path: Path, cs_path: Path, xi: float, seed: int,
                       n_outliers: int, work: Path) -> tuple[Path, Path, str]:
    """Invoke the Julia sampler exactly the way `gen.py` does.

    Returns (edge_tsv_path, com_tsv_path, stderr_text).
    """
    edge_tsv = work / "edge.tsv"
    com_tsv = work / "com.tsv"
    cmd = [
        "julia", str(ABCD_SAMPLER),
        str(edge_tsv), str(com_tsv),
        str(deg_path), str(cs_path),
        "xi", str(xi), "false", "false", str(seed), str(n_outliers),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Julia sampler failed (rc={proc.returncode}):\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return edge_tsv, com_tsv, proc.stderr


def _post_process(edge_tsv: Path, com_tsv: Path, out_dir: Path) -> tuple[Path, Path]:
    """Apply gen.py's simplify_edges + drop_singleton_clusters."""
    from pipeline_common import drop_singleton_clusters, simplify_edges

    out_dir.mkdir(parents=True, exist_ok=True)
    edge_df = pd.read_csv(edge_tsv, sep="\t", header=None,
                          names=["source", "target"])
    com_df = pd.read_csv(com_tsv, sep="\t", header=None,
                         names=["node_id", "cluster_id"])
    # Suppress the helper's INFO-level chatter for the harness.
    logging.disable(logging.CRITICAL)
    try:
        edge_df = simplify_edges(edge_df)
        com_df = drop_singleton_clusters(com_df)
    finally:
        logging.disable(logging.NOTSET)
    edge_csv = out_dir / "edge.csv"
    com_csv = out_dir / "com.csv"
    edge_df.to_csv(edge_csv, index=False)
    com_df.to_csv(com_csv, index=False)
    return edge_csv, com_csv


def _profile_inputs_for_abcd(fx: dict, work_dir: Path) -> tuple[Path, Path, float]:
    """Write the three stage-1 artifacts the Julia sampler consumes.

    Returns (deg_tsv_path, cs_tsv_path, xi).
    """
    deg_path = work_dir / "deg.tsv"
    cs_path = work_dir / "cs.tsv"
    pd.DataFrame(fx["degree_seq"]).to_csv(deg_path, sep="\t",
                                          header=False, index=False)
    pd.DataFrame(fx["cluster_sizes"]).to_csv(cs_path, sep="\t",
                                             header=False, index=False)
    return deg_path, cs_path, fx["xi"]


# ---------------------------------------------------------------------------
# Invariant checks
# ---------------------------------------------------------------------------


def _xi_tol_for(fx: dict, base_tol: float) -> float:
    """Loosen the ξ tolerance on small fixtures.

    The per-node external split d_i^ext = round(ξ · d_i) is rounded
    independently per-node, so realised ξ has stdev ~ O(1/sqrt(E))
    around the target. base_tol (default 0.05) is appropriate for E
    in the 100s+; on a 40-edge fixture we let it slip to 0.15.
    """
    n_edges = len(fx["edges_input"])
    if n_edges < 100:
        return max(base_tol, 0.15)
    return base_tol


def _check_invariants(fx: dict, edge_csv: Path, com_csv: Path,
                      xi_tol: float) -> tuple[bool, dict]:
    edge_df = pd.read_csv(edge_csv)
    com_df = pd.read_csv(com_csv)

    # 1. Simple-graph postcondition (re-check after simplify_edges).
    self_loops = int((edge_df["source"] == edge_df["target"]).sum())
    pair_keys = list(zip(
        edge_df[["source", "target"]].min(axis=1),
        edge_df[["source", "target"]].max(axis=1),
    ))
    parallels = len(pair_keys) - len(set(pair_keys))
    simple_ok = (self_loops == 0) and (parallels == 0)

    # 2. Exact per-node degree. Julia outputs labels 1..N corresponding
    #    to the i-th row of deg.tsv.
    out_deg: Counter = Counter()
    for u, v in zip(edge_df["source"], edge_df["target"]):
        out_deg[int(u)] += 1
        out_deg[int(v)] += 1
    expected_deg = {i + 1: d for i, d in enumerate(fx["degree_seq"])}
    deg_mismatches = []
    for nid, exp in expected_deg.items():
        got = out_deg.get(nid, 0)
        if got != exp:
            deg_mismatches.append((nid, exp, got))
    deg_ok = len(deg_mismatches) == 0

    # 3. Exact cluster size multiset (post drop_singleton on both sides).
    expected_sizes_nonsing = sorted(
        [s for s in fx["cluster_sizes"] if s > 1], reverse=True,
    )
    got_sizes = sorted(Counter(com_df["cluster_id"]).values(), reverse=True)
    sizes_ok = got_sizes == expected_sizes_nonsing

    # 4. Global ξ within tolerance. Unclustered endpoints are treated as
    #    cross-cluster (matches the sampler's outlier convention).
    com_map = dict(zip(com_df["node_id"].astype(int), com_df["cluster_id"]))
    out_sum = 0
    total = 0
    for u, v in zip(edge_df["source"], edge_df["target"]):
        cu = com_map.get(int(u))
        cv = com_map.get(int(v))
        if cu is None or cv is None or cu != cv:
            out_sum += 2
        total += 2
    measured_xi = out_sum / total if total else 0.0
    xi_delta = abs(measured_xi - fx["xi"])
    xi_ok = xi_delta <= xi_tol

    return (simple_ok and deg_ok and sizes_ok and xi_ok), {
        "edges": len(edge_df),
        "self_loops": self_loops,
        "parallels": parallels,
        "simple_ok": simple_ok,
        "deg_ok": deg_ok,
        "deg_mismatches_first5": deg_mismatches[:5],
        "n_deg_mismatches": len(deg_mismatches),
        "sizes_ok": sizes_ok,
        "got_sizes_first5": got_sizes[:5],
        "expected_sizes_first5": expected_sizes_nonsing[:5],
        "measured_xi": measured_xi,
        "target_xi": fx["xi"],
        "xi_delta": xi_delta,
        "xi_ok": xi_ok,
    }


def _summarise_stderr(stderr: str) -> str:
    flags = []
    for tag, needle in SAMPLER_FLAGS:
        if needle in stderr:
            flags.append(tag)
    return ",".join(flags) if flags else "-"


# ---------------------------------------------------------------------------
# Faithful-replay cross-check (instrumented Julia + JS replay)
# ---------------------------------------------------------------------------


def _run_instrumented(deg_path: Path, cs_path: Path, xi: float, seed: int,
                      n_outliers: int) -> dict:
    """Drive the instrumented Julia. Returns {canonical_edges, instr_edges,
    match_canonical, trace} or {_error}."""
    job = {
        "deg_file": str(deg_path),
        "cs_file": str(cs_path),
        "xi": xi,
        "seed": int(seed),
        "n_outliers": int(n_outliers),
    }
    proc = subprocess.run(
        ["julia", str(INSTRUMENTED_JL)],
        input=json.dumps(job), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {"_error": (proc.stderr or proc.stdout)[:600]}
    # Find the JSON line (Julia may emit warnings on stdout/stderr first).
    payload_line = None
    for line in proc.stdout.splitlines()[::-1]:
        if line.startswith("{"):
            payload_line = line
            break
    if payload_line is None:
        return {"_error": "no JSON line in instrumented stdout"}
    try:
        return json.loads(payload_line)
    except json.JSONDecodeError as exc:
        return {"_error": f"json decode: {exc}"}


def _run_js_replay(w: list[int], s: list[int], xi: float, n_outliers: int,
                   trace: list) -> dict:
    payload = {
        "w": list(map(int, w)),
        "s": list(map(int, s)),
        "xi": xi,
        "n_outliers": int(n_outliers),
        "trace": trace,
    }
    proc = subprocess.run(
        ["node", str(JS_REPLAY)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {"_error": (proc.stderr or proc.stdout)[:600]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"_error": f"json decode: {exc}; stdout={proc.stdout[:300]}"}


def _replay_check(fx: dict, deg_path: Path, cs_path: Path, xi: float,
                  seed: int, n_outliers: int) -> dict:
    """Returns {ok, msg} after instrumented Julia + JS replay."""
    inst = _run_instrumented(deg_path, cs_path, xi, seed, n_outliers)
    if "_error" in inst:
        return {"ok": False, "msg": f"instrumented: {inst['_error']}"}
    if not inst.get("match_canonical", False):
        return {"ok": False, "msg": "instrumented edges != canonical edges"}
    s_with_outliers = list(map(int, fx["cluster_sizes"]))
    if n_outliers > 0:
        s_with_outliers = [n_outliers] + s_with_outliers
    js = _run_js_replay(fx["degree_seq"], s_with_outliers, xi, n_outliers,
                        inst.get("trace", []))
    if "_error" in js:
        return {"ok": False, "msg": f"js: {js['_error']}"}
    canon_edges = sorted(tuple(e) for e in inst["canonical_edges"])
    js_edges = sorted(tuple(e) for e in js["edges"])
    if canon_edges != js_edges:
        only_c = sorted(set(canon_edges) - set(js_edges))[:3]
        only_j = sorted(set(js_edges) - set(canon_edges))[:3]
        return {"ok": False, "msg": (
            f"edges differ: canon={len(canon_edges)} js={len(js_edges)} "
            f"only_canon[:3]={only_c} only_js[:3]={only_j}"
        )}
    if js["trace_consumed"] != js["trace_length"]:
        return {"ok": False, "msg": (
            f"trace not fully consumed: {js['trace_consumed']}/{js['trace_length']}"
        )}
    return {"ok": True, "msg": (
        f"edges={len(canon_edges)} trace={js['trace_consumed']}"
    )}


# ---------------------------------------------------------------------------
# Per-row driver
# ---------------------------------------------------------------------------


def _run_one(fx: dict, seed: int, base_xi_tol: float, verbose: bool,
             do_replay: bool) -> bool:
    xi_tol = _xi_tol_for(fx, base_xi_tol)
    print(f"\n  fixture={fx['name']:<12s} seed={seed:>3d}  "
          f"N={len(fx['nodes'])} E={len(fx['edges_input'])} "
          f"xi_target={fx['xi']:.4f}  xi_tol={xi_tol}")
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        deg_path, cs_path, xi = _profile_inputs_for_abcd(fx, work)
        edge_tsv, com_tsv, stderr = _run_julia_sampler(
            deg_path, cs_path, xi, seed, n_outliers=0, work=work,
        )
        edge_csv, com_csv = _post_process(edge_tsv, com_tsv, work / "out")
        ok, info = _check_invariants(fx, edge_csv, com_csv, xi_tol=xi_tol)
        flags = _summarise_stderr(stderr)
        replay = None
        if do_replay:
            replay = _replay_check(fx, deg_path, cs_path, xi, seed, n_outliers=0)
            if not replay["ok"]:
                ok = False
        status = "PASS" if ok else "FAIL"
        print(f"    {status}: edges={info['edges']:>4d} "
              f"self_loops={info['self_loops']} parallels={info['parallels']}  "
              f"simple={info['simple_ok']} deg_exact={info['deg_ok']} "
              f"sizes_exact={info['sizes_ok']} "
              f"xi_measured={info['measured_xi']:.4f} "
              f"xi_delta={info['xi_delta']:.4f} -> xi_ok={info['xi_ok']}  "
              f"sampler_flags={flags}")
        if replay is not None:
            print(f"      replay: ok={replay['ok']} ({replay['msg']})")
        if not ok:
            if not info["deg_ok"]:
                print(f"      degree mismatches: {info['n_deg_mismatches']} node(s); "
                      f"first 5 (node, expected, got): "
                      f"{info['deg_mismatches_first5']}")
            if not info["sizes_ok"]:
                print(f"      sizes mismatch:")
                print(f"        expected (top 5, desc): {info['expected_sizes_first5']}")
                print(f"        got      (top 5, desc): {info['got_sizes_first5']}")
            if not info["xi_ok"]:
                print(f"      xi out of tolerance: |Δ|={info['xi_delta']:.4f} > {xi_tol}")
        if verbose and stderr:
            for line in stderr.splitlines():
                print(f"      [julia stderr] {line}")
        return ok


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, nargs="*", default=list(range(1, 6)),
                    help="Seeds to run per fixture (default: 1..5).")
    ap.add_argument("--xi-tol", type=float, default=0.05,
                    help="Tolerance on |measured xi - target xi| for "
                         "fixtures with E>=100 (default: 0.05). E<100 uses 0.15.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print Julia stderr for every row.")
    ap.add_argument("--no-replay", action="store_true",
                    help="Skip the faithful-replay byte-equality check (instrumented Julia + JS).")
    args = ap.parse_args()

    if not ABCD_SAMPLER.exists():
        print(f"FAIL: ABCD sampler not found at {ABCD_SAMPLER}", file=sys.stderr)
        sys.exit(2)

    fixtures = [
        small20_fixture(),
        random_fixture("rand100_5c", n=100, k=5, seed=42),
    ]

    print("=" * 72)
    print("ABCD kernel cross-check")
    print("=" * 72)
    print(f"  Sampler:   {ABCD_SAMPLER}")
    print(f"  Seeds:     {args.seeds}")
    print(f"  Fixtures:  {[fx['name'] for fx in fixtures]}")
    print(f"  xi tol:    {args.xi_tol} (auto-loosened to 0.15 for fixtures with E<100)")
    print()
    print("PASS criteria per (fixture, seed):")
    print("  * simple graph  -> no self-loops, no parallel edges in edge.csv")
    print("  * deg_exact     -> per-node degree in output == input degree")
    print("  * sizes_exact   -> output cluster size multiset == input (size>=2 only)")
    print("  * xi_ok         -> |measured xi - target xi| <= xi_tol")
    print()
    print("sampler_flags reports stderr findings worth knowing about:")
    for tag, needle in SAMPLER_FLAGS:
        print(f"  {tag:<12s} -> matches '{needle}'")

    do_replay = (not args.no_replay) and INSTRUMENTED_JL.exists() and JS_REPLAY.exists()
    print()
    print(f"  replay check: {'ON' if do_replay else 'OFF'}  "
          f"(instrumented={INSTRUMENTED_JL.name}, js={JS_REPLAY.name})")

    overall = True
    n_pass = 0
    n_total = 0
    for fx in fixtures:
        print(f"\n=== fixture: {fx['name']} ===")
        for seed in args.seeds:
            ok = _run_one(fx, seed, args.xi_tol, args.verbose, do_replay)
            n_total += 1
            n_pass += int(ok)
            overall = overall and ok

    print()
    print("=" * 72)
    print(f"OVERALL: {'PASS' if overall else 'FAIL'}  ({n_pass}/{n_total} rows passed)")
    print("=" * 72)
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
