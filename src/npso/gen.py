import logging
import argparse
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import powerlaw
import networkit as nk

from pipeline_common import standard_setup, timed, drop_singleton_clusters


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


def _matlab_subprocess_script(n_threads, npso_dir_abs, matlab_wrapper_dir):
    """Bash command that locates the matlab binary (lmod-dance) and runs a
    passed-in MATLAB one-liner. Used by SubprocessRunner."""
    single_flag = "-singleCompThread " if n_threads == 1 else ""
    return (
        "if ! command -v matlab >/dev/null 2>&1; then "
        "for f in /etc/profile.d/z00_lmod.sh /usr/share/lmod/lmod/init/bash; do "
        '[ -r "$f" ] && . "$f" && break; done; '
        "command -v module >/dev/null 2>&1 && module load matlab 2>/dev/null; fi; "
        f'exec matlab {single_flag}-nodisplay -nosplash -nodesktop -r "$1"'
    )


class SubprocessRunner:
    """Runs MATLAB once per iteration via a fresh `matlab` subprocess.

    Baseline path. Used when matlab.engine for Python is not installed, or as
    a fallback when the engine raises a non-recoverable error mid-loop.
    """

    def __init__(self, n_threads, npso_dir_abs, matlab_wrapper_dir):
        self.n_threads = n_threads
        self.npso_dir_abs = npso_dir_abs
        self.matlab_wrapper_dir = matlab_wrapper_dir

    def run_iter(self, N, m, T, gamma, c, prefix, seed):
        matlab_inner = (
            f"try, maxNumCompThreads({self.n_threads}), "
            f"addpath(genpath('{self.npso_dir_abs}')), "
            f"addpath('{self.matlab_wrapper_dir}'), "
            f"run_npso({N}, {m}, {T}, {gamma}, {c}, '{prefix}', {seed}), "
            f"catch e, fprintf(1, e.message), end, quit"
        )
        bash_script = _matlab_subprocess_script(
            self.n_threads, self.npso_dir_abs, self.matlab_wrapper_dir
        )
        subprocess.run(
            ["bash", "-c", bash_script, "bash", matlab_inner],
            check=False,
        )

    def close(self):
        pass


class EngineRunner:
    """Runs each iteration inside one persistent MATLAB Engine session.

    Removes the ~20–60s per-iter cold-start of the subprocess path. On a
    per-iter MATLAB error, logs and returns without killing the engine;
    caller treats that iter as failed and may invoke a SubprocessRunner
    fallback. On engine-level failures (start, path setup) raises so the
    caller can downgrade to SubprocessRunner for the whole run.
    """

    def __init__(self, n_threads, npso_dir_abs, matlab_wrapper_dir):
        if not _engine_available():
            raise RuntimeError("matlab.engine not importable")
        self.n_threads = n_threads
        self.npso_dir_abs = str(npso_dir_abs)
        self.matlab_wrapper_dir = str(matlab_wrapper_dir)
        logging.info("Starting persistent MATLAB engine session...")
        self._eng = _matlab_engine.start_matlab("-singleCompThread -nodisplay -nosplash -nodesktop")
        self._eng.addpath(self._eng.genpath(self.npso_dir_abs), nargout=0)
        self._eng.addpath(self.matlab_wrapper_dir, nargout=0)
        self._eng.maxNumCompThreads(self.n_threads, nargout=0)

    def run_iter(self, N, m, T, gamma, c, prefix, seed):
        try:
            self._eng.run_npso(
                float(N), float(m), float(T), float(gamma), float(c),
                str(prefix), float(seed), nargout=0,
            )
            return True
        except _matlab_engine.MatlabExecutionError as exc:
            logging.error(f"MATLAB iter failed (engine): {exc}")
            return False

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
    best_T = None
    best_global_ccoeff = None
    best_diff = None
    prev_global_ccoeff, global_ccoeff = None, None
    max_iters = 100
    npso_dir_abs = Path(npso_dir).resolve()
    matlab_wrapper_dir = (Path(__file__).resolve().parent / "matlab")

    runner = make_runner(n_threads, npso_dir_abs, matlab_wrapper_dir)
    fallback = None

    try:
        for it in range(max_iters):
            T = min_T + (max_T - min_T) / 2
            if T < 0.0005:
                break
            logging.info(f"[iter {it}] T={T}")

            with timed("Generation"):
                prefix = output_dir / f"{T:.5f}_"
                ok = runner.run_iter(N, m, T, gamma, c, prefix, seed)
                if ok is False and not isinstance(runner, SubprocessRunner):
                    logging.warning("Engine iter failed; retrying via subprocess fallback for this iter.")
                    if fallback is None:
                        fallback = SubprocessRunner(n_threads, npso_dir_abs, matlab_wrapper_dir)
                    fallback.run_iter(N, m, T, gamma, c, prefix, seed)

            edge_path = output_dir / f"{T:.5f}_edge.tsv"
            com_path = output_dir / f"{T:.5f}_com.tsv"
            if not edge_path.exists() or not com_path.exists():
                logging.error(f"Missing MATLAB outputs at T={T}")
                global_ccoeff = None
            else:
                elr = nk.graphio.EdgeListReader("\t", 0, continuous=False, directed=False)
                graph = elr.read(str(edge_path))
                graph.removeMultiEdges()
                graph.removeSelfLoops()
                prev_global_ccoeff = global_ccoeff
                global_ccoeff = nk.globals.ClusteringCoefficient.exactGlobal(graph)
                logging.info(f"Global clustering coefficient: {global_ccoeff}")

            diff = abs(global_ccoeff - target_global_ccoeff) if global_ccoeff is not None else 2.0
            step = abs(prev_global_ccoeff - global_ccoeff) if prev_global_ccoeff is not None and global_ccoeff is not None else 2.0

            if best_global_ccoeff is None or diff < best_diff:
                if best_T is not None and best_T != T:
                    _safe_remove(output_dir / f"{best_T:.5f}_edge.tsv")
                    _safe_remove(output_dir / f"{best_T:.5f}_com.tsv")
                best_T = T
                best_global_ccoeff = global_ccoeff
                best_diff = diff
            else:
                if best_T is not None and best_T != T:
                    _safe_remove(output_dir / f"{T:.5f}_edge.tsv")
                    _safe_remove(output_dir / f"{T:.5f}_com.tsv")

            logging.info(f"Step: {step}  Best T: {best_T}  Best ccoeff: {best_global_ccoeff}  Best diff: {best_diff}")
            if best_diff is not None and best_diff < 0.005:
                break
            if step < 0.0001:
                break

            if global_ccoeff is not None and global_ccoeff < target_global_ccoeff:
                max_T = T
            else:
                min_T = T
    finally:
        runner.close()
        if fallback is not None:
            fallback.close()

    if best_T is None:
        raise RuntimeError("nPSO produced no viable output.")

    best_edge = output_dir / f"{best_T:.5f}_edge.tsv"
    best_com = output_dir / f"{best_T:.5f}_com.tsv"
    if not best_edge.exists() or not best_com.exists():
        raise RuntimeError(f"Best nPSO output missing at T={best_T}")

    edge_df = pd.read_csv(best_edge, sep="\t", header=None, names=["source", "target"])
    com_df = pd.read_csv(best_com, sep="\t", header=None, names=["node_id", "cluster_id"])
    # Drop outlier bucket (cluster_id == 1 matches synnet convention), then singletons.
    com_df = drop_singleton_clusters(com_df[com_df["cluster_id"] > 1])

    edge_df.to_csv(output_dir / "edge.csv", index=False)
    com_df.to_csv(output_dir / "com.csv", index=False)

    # Cleanup the per-T files.
    for p in output_dir.glob("*_edge.tsv"):
        _safe_remove(p)
    for p in output_dir.glob("*_com.tsv"):
        _safe_remove(p)

    logging.info("nPSO generation complete.")


def _safe_remove(p):
    try:
        Path(p).unlink()
    except FileNotFoundError:
        pass


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
