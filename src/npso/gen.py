import hashlib
import json
import logging
import argparse
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import powerlaw
import networkit as nk

from pipeline_common import standard_setup, timed, drop_singleton_clusters


SEARCH_LOG_NAME = "search_log.jsonl"


try:
    import matlab.engine as _matlab_engine
    _ENGINE_IMPORT_ERROR = None
except Exception as _exc:
    _matlab_engine = None
    _ENGINE_IMPORT_ERROR = _exc


def _engine_available():
    return _matlab_engine is not None


def compute_global_ccoeff_from_edgelist(edgelist_path):
    """Compute the exact global clustering coefficient of an undirected edgelist."""
    elr = nk.graphio.EdgeListReader(",", 1, continuous=False, directed=False)
    g = elr.read(str(edgelist_path))
    g.removeMultiEdges()
    g.removeSelfLoops()
    return nk.globals.ClusteringCoefficient.exactGlobal(g)


def _ccoeff_from_edges(edges_df):
    g = nk.graph.Graph(n=0, weighted=False, directed=False)
    nodes = pd.unique(pd.concat([edges_df["source"], edges_df["target"]], ignore_index=True))
    idx = {v: i for i, v in enumerate(nodes)}
    for _ in range(len(nodes)):
        g.addNode()
    for u, v in zip(edges_df["source"].to_numpy(), edges_df["target"].to_numpy()):
        g.addEdge(idx[u], idx[v])
    g.removeMultiEdges()
    g.removeSelfLoops()
    return nk.globals.ClusteringCoefficient.exactGlobal(g)


def _matlab_subprocess_script(n_threads):
    """Bash one-liner: load matlab via lmod if not in PATH, then run the
    inner MATLAB command passed as $1."""
    single_flag = "-singleCompThread " if n_threads == 1 else ""
    return (
        "if ! command -v matlab >/dev/null 2>&1; then "
        "for f in /etc/profile.d/z00_lmod.sh /usr/share/lmod/lmod/init/bash; do "
        '[ -r "$f" ] && . "$f" && break; done; '
        "command -v module >/dev/null 2>&1 && module load matlab 2>/dev/null; fi; "
        f'exec matlab {single_flag}-nodisplay -nosplash -nodesktop -r "$1"'
    )


class SubprocessRunner:
    """Spawns a fresh `matlab` per iteration.

    Backup path. Used when matlab.engine for Python is not installed, or as
    a per-iter fallback when an engine call fails.

    run_iter() writes {prefix}_edge.tsv / {prefix}_com.tsv via the MATLAB
    script, then reads them back into DataFrames. The TSVs live in a
    caller-supplied scratch dir so the main loop doesn't have to clean
    them up individually.
    """

    def __init__(self, n_threads, npso_dir_abs, matlab_wrapper_dir):
        self.n_threads = n_threads
        self.npso_dir_abs = str(npso_dir_abs)
        self.matlab_wrapper_dir = str(matlab_wrapper_dir)
        self.last_error = None

    def run_iter(self, N, m, T, gamma, c, prefix, seed):
        matlab_inner = (
            f"try, maxNumCompThreads({self.n_threads}), "
            f"addpath(genpath('{self.npso_dir_abs}')), "
            f"addpath('{self.matlab_wrapper_dir}'), "
            f"run_npso({N}, {m}, {T}, {gamma}, {c}, '{prefix}', {seed}), "
            f"catch e, fprintf(1, e.message), end, quit"
        )
        bash_script = _matlab_subprocess_script(self.n_threads)
        proc = subprocess.run(
            ["bash", "-c", bash_script, "bash", matlab_inner],
            check=False, capture_output=True, text=True,
        )
        edge_path = Path(f"{prefix}edge.tsv")
        com_path = Path(f"{prefix}com.tsv")
        if not edge_path.exists() or not com_path.exists():
            # No TSVs means MATLAB caught an error via the try/catch above
            # and printed e.message on stdout; preserve it for diagnostics.
            tail = (proc.stdout or "").strip().splitlines()[-5:]
            self.last_error = "\n".join(tail) or "unknown MATLAB failure (no TSVs produced)"
            logging.error(f"MATLAB iter failed (subprocess): {self.last_error}")
            return None
        edge_df = pd.read_csv(edge_path, sep="\t", header=None, names=["source", "target"])
        com_df = pd.read_csv(com_path, sep="\t", header=None, names=["node_id", "cluster_id"])
        # Clean up the scratch TSVs — their data is now in memory.
        _safe_remove(edge_path)
        _safe_remove(com_path)
        return edge_df, com_df

    def close(self):
        pass


class EngineRunner:
    """One persistent MATLAB session shared by every bisection iter.

    run_iter() returns the edges/comm matrices directly from MATLAB — no
    TSV write/read round-trip. On a per-iter MATLAB error logs and returns
    None; the main loop treats that as a failed iter and can retry via
    SubprocessRunner. On engine start/path-setup failure raises so
    make_runner() can downgrade the whole run.
    """

    def __init__(self, n_threads, npso_dir_abs, matlab_wrapper_dir):
        if not _engine_available():
            raise RuntimeError("matlab.engine not importable")
        self.n_threads = n_threads
        self.npso_dir_abs = str(npso_dir_abs)
        self.matlab_wrapper_dir = str(matlab_wrapper_dir)
        self.last_error = None
        logging.info("Starting persistent MATLAB engine session...")
        self._eng = _matlab_engine.start_matlab("-singleCompThread -nodisplay -nosplash -nodesktop")
        self._eng.addpath(self._eng.genpath(self.npso_dir_abs), nargout=0)
        self._eng.addpath(self.matlab_wrapper_dir, nargout=0)
        self._eng.maxNumCompThreads(self.n_threads, nargout=0)

    def run_iter(self, N, m, T, gamma, c, prefix, seed):
        try:
            edges_mat, comm_mat = self._eng.run_npso(
                float(N), float(m), float(T), float(gamma), float(c),
                str(prefix), float(seed), nargout=2,
            )
        except _matlab_engine.MatlabExecutionError as exc:
            self.last_error = str(exc).strip() or "unknown MatlabExecutionError"
            logging.error(f"MATLAB iter failed (engine): {self.last_error}")
            return None

        edges_arr = np.asarray(edges_mat, dtype=np.int64)
        if edges_arr.size == 0:
            edges_arr = edges_arr.reshape(0, 2)
        comm_arr = np.asarray(comm_mat, dtype=np.int64).reshape(-1)

        edge_df = pd.DataFrame({"source": edges_arr[:, 0], "target": edges_arr[:, 1]})
        com_df = pd.DataFrame({
            "node_id": np.arange(1, len(comm_arr) + 1, dtype=np.int64),
            "cluster_id": comm_arr,
        })
        return edge_df, com_df

    def close(self):
        try:
            self._eng.quit()
        except Exception:
            pass


def make_runner(n_threads, npso_dir_abs, matlab_wrapper_dir):
    """Prefer the persistent engine; silently fall back to subprocess if
    matlab.engine isn't installed or the engine fails to start."""
    if not _engine_available():
        logging.info(
            "matlab.engine for Python not available "
            f"({_ENGINE_IMPORT_ERROR}); using subprocess fallback."
        )
        return SubprocessRunner(n_threads, npso_dir_abs, matlab_wrapper_dir)
    try:
        return EngineRunner(n_threads, npso_dir_abs, matlab_wrapper_dir)
    except Exception as exc:
        logging.error(f"MATLAB engine failed to start: {exc}; falling back to subprocess.")
        return SubprocessRunner(n_threads, npso_dir_abs, matlab_wrapper_dir)


def run_npso_generation(
    input_edgelist,
    degree_path,
    cluster_sizes_path,
    npso_dir,
    output_dir,
    seed,
    n_threads,
):
    output_dir = standard_setup(output_dir)

    logging.info("Starting nPSO Generation...")
    logging.info(f"Seed: {seed} n_threads: {n_threads}")

    with timed("Input loading + parameter computation"):
        degrees = pd.read_csv(degree_path, header=None)[0].to_numpy()
        cluster_sizes = pd.read_csv(cluster_sizes_path, header=None)[0].to_numpy()

        N = len(degrees)
        m = int(np.round(np.mean(degrees) / 2))
        gamma = float(np.max([
            powerlaw.Fit(degrees, discrete=True, verbose=False).power_law.alpha,
            2.0,
        ]))
        c = int(len(cluster_sizes))

        target_global_ccoeff = compute_global_ccoeff_from_edgelist(input_edgelist)
        logging.info(f"N={N} m={m} gamma={gamma} c={c} target_ccoeff={target_global_ccoeff}")

    min_T, max_T = 0.0, 1.0
    # Signed residuals f(T) = ccoeff(T) - target at the bracket endpoints,
    # measured lazily from the last iter that landed there. Populated as
    # we go; secant step only fires once both are known with opposite signs.
    f_min_T, f_max_T = None, None
    best_T = None
    best_edge_df = None
    best_com_df = None
    best_global_ccoeff = None
    best_diff = None
    prev_global_ccoeff, global_ccoeff = None, None
    max_iters = 100
    npso_dir_abs = Path(npso_dir).resolve()
    matlab_wrapper_dir = (Path(__file__).resolve().parent / "matlab")

    # --- Resumability: replay prior iters from search_log.jsonl ------------
    inputs_sha256 = _input_hash(N, m, gamma, c, target_global_ccoeff, seed)
    search_log_path = output_dir / SEARCH_LOG_NAME
    prior_rows = _load_search_log(search_log_path, inputs_sha256)
    start_iter = 0
    converged_in_replay = False
    for row in prior_rows:
        start_iter = row["iter"] + 1
        T_r = row["T"]
        cc_r = row["global_ccoeff"]
        diff_r = abs(cc_r - target_global_ccoeff)
        step_r = abs(prev_global_ccoeff - cc_r) if prev_global_ccoeff is not None else 2.0
        if best_global_ccoeff is None or diff_r < best_diff:
            best_T = T_r
            best_global_ccoeff = cc_r
            best_diff = diff_r
        f_T = cc_r - target_global_ccoeff
        if f_T < 0:
            max_T = T_r
            f_max_T = f_T
        else:
            min_T = T_r
            f_min_T = f_T
        prev_global_ccoeff = cc_r
        global_ccoeff = cc_r
        # Mirror the in-loop early-exit predicates so resume doesn't
        # redo work the prior run would have skipped.
        if best_diff is not None and best_diff < 0.005:
            converged_in_replay = True
            break
        if step_r < 0.0001:
            converged_in_replay = True
            break
    if prior_rows:
        logging.info(
            f"Resuming from search_log.jsonl: replayed {len(prior_rows)} iter(s); "
            f"start_iter={start_iter} best_T={best_T} best_ccoeff={best_global_ccoeff}"
        )
    # --------------------------------------------------------------------

    runner = make_runner(n_threads, npso_dir_abs, matlab_wrapper_dir)
    fallback = None

    # Scratch dir for SubprocessRunner's {prefix}_{edge,com}.tsv files —
    # EngineRunner returns in memory so nothing lands here in that path.
    with tempfile.TemporaryDirectory(prefix="npso_scratch_", dir=output_dir) as scratch_str:
        scratch_dir = Path(scratch_str)
        try:
            # If we resumed with a best_T but no matrices in memory, re-run
            # it once to restore them. Cheaper than persisting adj/comm to
            # disk in phase B's in-memory model.
            if best_T is not None and best_edge_df is None:
                logging.info(f"Re-running best_T={best_T} to restore matrices post-resume.")
                prefix = scratch_dir / f"resume_{best_T:.5f}_"
                restored = runner.run_iter(N, m, best_T, gamma, c, prefix, seed)
                if restored is None and not isinstance(runner, SubprocessRunner):
                    if fallback is None:
                        fallback = SubprocessRunner(n_threads, npso_dir_abs, matlab_wrapper_dir)
                    restored = fallback.run_iter(N, m, best_T, gamma, c, prefix, seed)
                if restored is not None:
                    best_edge_df, best_com_df = restored
                else:
                    logging.error("Resume re-run at best_T failed; treating log as stale.")
                    search_log_path.unlink(missing_ok=True)
                    start_iter = 0
                    converged_in_replay = False
                    best_T = None
                    best_global_ccoeff = None
                    best_diff = None
                    min_T, max_T = 0.0, 1.0
                    f_min_T, f_max_T = None, None
                    prev_global_ccoeff = None
                    global_ccoeff = None

            if not converged_in_replay:
                for it in range(start_iter, max_iters):
                    T = _next_T(min_T, max_T, f_min_T, f_max_T)
                    if T < 0.0005:
                        break
                    logging.info(f"[iter {it}] T={T}")

                    iter_start = time.monotonic()
                    with timed("Generation"):
                        prefix = scratch_dir / f"{T:.5f}_"
                        result = runner.run_iter(N, m, T, gamma, c, prefix, seed)
                        if result is None and not isinstance(runner, SubprocessRunner):
                            logging.warning("Engine iter failed; retrying via subprocess fallback for this iter.")
                            if fallback is None:
                                fallback = SubprocessRunner(n_threads, npso_dir_abs, matlab_wrapper_dir)
                            result = fallback.run_iter(N, m, T, gamma, c, prefix, seed)
                    elapsed_s = time.monotonic() - iter_start

                    if result is None:
                        logging.error(f"Missing MATLAB outputs at T={T}")
                        global_ccoeff = None
                        edge_df, com_df = None, None
                    else:
                        edge_df, com_df = result
                        prev_global_ccoeff = global_ccoeff
                        global_ccoeff = _ccoeff_from_edges(edge_df)
                        logging.info(f"Global clustering coefficient: {global_ccoeff}")
                        _append_search_log(search_log_path, {
                            "iter": it,
                            "T": T,
                            "inputs_sha256": inputs_sha256,
                            "global_ccoeff": global_ccoeff,
                            "elapsed_s": elapsed_s,
                        })

                    diff = abs(global_ccoeff - target_global_ccoeff) if global_ccoeff is not None else 2.0
                    step = abs(prev_global_ccoeff - global_ccoeff) if prev_global_ccoeff is not None and global_ccoeff is not None else 2.0

                    if result is not None and (best_global_ccoeff is None or diff < best_diff):
                        best_T = T
                        best_edge_df = edge_df
                        best_com_df = com_df
                        best_global_ccoeff = global_ccoeff
                        best_diff = diff

                    logging.info(f"Step: {step}  Best T: {best_T}  Best ccoeff: {best_global_ccoeff}  Best diff: {best_diff}")
                    if best_diff is not None and best_diff < 0.005:
                        break
                    if step < 0.0001:
                        break

                    if global_ccoeff is not None:
                        # ccoeff is decreasing in T. f(T) = ccoeff(T) - target.
                        # f > 0 ⇒ need higher T (raise min_T); f < 0 ⇒ lower T (lower max_T).
                        f_T = global_ccoeff - target_global_ccoeff
                        if f_T < 0:
                            max_T = T
                            f_max_T = f_T
                        else:
                            min_T = T
                            f_min_T = f_T
        finally:
            runner.close()
            if fallback is not None:
                fallback.close()

    if best_T is None or best_edge_df is None:
        # Prefer the fallback's error when present — it's the later attempt.
        last = (
            getattr(fallback, "last_error", None) if fallback is not None else None
        ) or getattr(runner, "last_error", None)
        msg = "nPSO produced no viable output."
        if last:
            msg += f" Last MATLAB error:\n{last}"
        raise RuntimeError(msg)

    # Drop outlier bucket (cluster_id == 1 matches synnet convention), then singletons.
    final_com = drop_singleton_clusters(best_com_df[best_com_df["cluster_id"] > 1])

    best_edge_df.to_csv(output_dir / "edge.csv", index=False)
    final_com.to_csv(output_dir / "com.csv", index=False)

    logging.info("nPSO generation complete.")


def _safe_remove(p):
    try:
        Path(p).unlink()
    except FileNotFoundError:
        pass


def _input_hash(N, m, gamma, c, target_ccoeff, seed):
    """Stable digest of the derived search inputs — if any of these change
    between runs, the log must be discarded."""
    payload = json.dumps(
        {"N": int(N), "m": int(m), "gamma": float(gamma),
         "c": int(c), "target": float(target_ccoeff), "seed": int(seed)},
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _load_search_log(log_path, expected_hash):
    """Parse existing search_log.jsonl. Return the list of (iter, T, ccoeff)
    records if every row matches `expected_hash`; else truncate and return []."""
    if not log_path.exists():
        return []
    records = []
    try:
        with log_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("inputs_sha256") != expected_hash:
                    raise ValueError("inputs_sha256 mismatch")
                records.append(row)
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logging.warning(f"search_log.jsonl incompatible ({exc}); starting fresh.")
        log_path.unlink()
        return []
    records.sort(key=lambda r: r["iter"])
    return records


def _append_search_log(log_path, row):
    with log_path.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
        f.flush()


def _next_T(min_T, max_T, f_min_T, f_max_T):
    """Pick the next T to evaluate.

    Secant when both endpoints have signed residuals and they straddle
    zero; otherwise midpoint. Clamp near-boundary secant picks back to
    the midpoint so we don't collapse the bracket.
    """
    mid = min_T + (max_T - min_T) / 2
    if f_min_T is None or f_max_T is None:
        return mid
    if f_min_T * f_max_T > 0:
        # Same sign — bracket invalid (shouldn't happen given how we
        # update bounds, but stay safe). Fall back to midpoint.
        return mid
    denom = f_max_T - f_min_T
    if denom == 0:
        return mid
    T_sec = min_T - f_min_T * (max_T - min_T) / denom
    # Guard against degenerate secant steps that hug the edge.
    margin = 0.05 * (max_T - min_T)
    if T_sec <= min_T + margin or T_sec >= max_T - margin:
        return mid
    return T_sec


def parse_args():
    parser = argparse.ArgumentParser(description="nPSO Graph Generator")
    parser.add_argument("--input-edgelist", type=str, required=True,
                        help="Original empirical edgelist (used to measure target global clustering coefficient)")
    parser.add_argument("--degree", type=str, required=True)
    parser.add_argument("--cluster-sizes", type=str, required=True)
    parser.add_argument("--npso-dir", type=str, required=True,
                        help="Path to the nPSO_model checkout (containing nPSO_model.m)")
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n-threads", type=int, default=1)
    return parser.parse_args()


def main():
    args = parse_args()
    run_npso_generation(
        args.input_edgelist,
        args.degree,
        args.cluster_sizes,
        args.npso_dir,
        args.output_folder,
        args.seed,
        args.n_threads,
    )


if __name__ == "__main__":
    main()
