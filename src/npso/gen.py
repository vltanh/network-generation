import hashlib
import json
import logging
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import networkit as nk

from pipeline_common import standard_setup, timed, drop_singleton_clusters, simplify_edges


MODELS = ("nPSO1", "nPSO2", "nPSO3")
DEFAULT_MODEL = "nPSO2"

SEARCH_STRATEGIES = ("bayesian", "secant")
DEFAULT_SEARCH_STRATEGY = "bayesian"
DEFAULT_SEARCH_INITIAL_POINTS = 5
DEFAULT_SEARCH_SAMPLES_PER_T = 1

SEARCH_LOG_NAME = "search_log.json"


try:
    import matlab.engine as _matlab_engine
    import matlab as _matlab
    _ENGINE_IMPORT_ERROR = None
except Exception as _exc:
    _matlab_engine = None
    _matlab = None
    _ENGINE_IMPORT_ERROR = _exc


def _engine_available():
    return _matlab_engine is not None


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
    """Bash one-liner: lmod-load matlab if needed, then run inner MATLAB cmd as $1."""
    single_flag = "-singleCompThread " if n_threads == 1 else ""
    return (
        "if ! command -v matlab >/dev/null 2>&1; then "
        "for f in /etc/profile.d/z00_lmod.sh /usr/share/lmod/lmod/init/bash; do "
        '[ -r "$f" ] && . "$f" && break; done; '
        "command -v module >/dev/null 2>&1 && module load matlab 2>/dev/null; fi; "
        f'exec matlab {single_flag}-nodisplay -nosplash -nodesktop -r "$1"'
    )


def _weights_matlab_literal(weights):
    """Render a weights vector as a MATLAB row-vector literal for subprocess -r."""
    if not weights:
        return "[]"
    return "[" + ",".join(f"{w:.17g}" for w in weights) + "]"


def _validate_model_inputs(model, c, weights):
    if model not in MODELS:
        raise ValueError(f"unknown model: {model!r}; expected one of {MODELS}")
    if model == "nPSO2":
        if len(weights) != c:
            raise ValueError(
                f"nPSO2 requires {c} mixing proportions, derived.json carries {len(weights)}"
            )


class SubprocessRunner:
    """Spawns a fresh `matlab` per iter. Fallback when matlab.engine is absent.

    Writes {prefix}_edge.tsv / {prefix}_com.tsv and reads them back.
    """

    def __init__(self, n_threads, npso_dir_abs, matlab_wrapper_dir):
        self.n_threads = n_threads
        self.npso_dir_abs = str(npso_dir_abs)
        self.matlab_wrapper_dir = str(matlab_wrapper_dir)
        self.last_error = None

    def run_iter(self, N, m, T, gamma, c, model, weights, prefix, seed):
        weights_literal = _weights_matlab_literal(weights)
        matlab_inner = (
            f"try, maxNumCompThreads({self.n_threads}), "
            f"addpath(genpath('{self.npso_dir_abs}')), "
            f"addpath('{self.matlab_wrapper_dir}'), "
            f"run_npso({N}, {m}, {T}, {gamma}, {c}, '{model}', "
            f"{weights_literal}, '{prefix}', {seed}), "
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
            tail = (proc.stdout or "").strip().splitlines()[-5:]
            self.last_error = "\n".join(tail) or "unknown MATLAB failure (no TSVs produced)"
            logging.error(f"MATLAB iter failed (subprocess): {self.last_error}")
            return None
        edge_df = pd.read_csv(edge_path, sep="\t", header=None, names=["source", "target"])
        com_df = pd.read_csv(com_path, sep="\t", header=None, names=["node_id", "cluster_id"])
        _safe_remove(edge_path)
        _safe_remove(com_path)
        return edge_df, com_df

    def close(self):
        pass


class EngineRunner:
    """Persistent MATLAB session; returns edges/comm matrices directly (no TSV).

    Per-iter MATLAB error returns None (main loop may retry via subprocess).
    Engine-start failure raises so make_runner() can downgrade the whole run.
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

    def run_iter(self, N, m, T, gamma, c, model, weights, prefix, seed):
        weights_matlab = _matlab.double(list(weights) if weights else [])
        try:
            edges_mat, comm_mat = self._eng.run_npso(
                float(N), float(m), float(T), float(gamma), float(c),
                str(model), weights_matlab,
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
    """Engine if available, else subprocess fallback."""
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


def _eval_T_with_samples(
    runner, fallback_factory, scratch_dir, T, N, m, gamma, c, model,
    mixing_proportions, base_seed, samples_per_T, iter_idx,
):
    """Run MATLAB ``samples_per_T`` times at temperature ``T`` with
    distinct per-realisation seeds; return ``(mean_cc, rep_edge_df,
    rep_com_df, sample_ccs, fallback_ref)`` where ``rep_*`` is the
    realisation whose ccoeff is closest to the empirical mean.

    Per-realisation seed = ``base_seed * 1_000_003 + iter_idx * 31337
    + s`` so re-runs are deterministic and different probes / samples
    get distinct draws. ``fallback_ref`` is the (lazily created)
    SubprocessRunner used when the engine errs on a sample, so the
    caller can keep reusing it.
    """
    sample_ccs = []
    edges_list = []
    coms_list = []
    fallback = fallback_factory()
    for s in range(samples_per_T):
        seed_iter = (int(base_seed) * 1_000_003 + int(iter_idx) * 31337 + s) % (2**31 - 1)
        prefix = scratch_dir / f"{T:.5f}_s{s}_"
        result = runner.run_iter(
            N, m, T, gamma, c, model, mixing_proportions, prefix, seed_iter,
        )
        if result is None and not isinstance(runner, SubprocessRunner):
            if fallback is None:
                fallback = fallback_factory(force=True)
            result = fallback.run_iter(
                N, m, T, gamma, c, model, mixing_proportions, prefix, seed_iter,
            )
        if result is None:
            return None, None, None, sample_ccs, fallback
        edge_df, com_df = result
        cc = _ccoeff_from_edges(edge_df)
        sample_ccs.append(cc)
        edges_list.append(edge_df)
        coms_list.append(com_df)
    mean_cc = float(np.mean(sample_ccs))
    if samples_per_T == 1:
        return mean_cc, edges_list[0], coms_list[0], sample_ccs, fallback
    diffs = [abs(c - mean_cc) for c in sample_ccs]
    rep_idx = int(np.argmin(diffs))
    return mean_cc, edges_list[rep_idx], coms_list[rep_idx], sample_ccs, fallback


def run_npso_generation(
    N,
    m,
    gamma,
    c,
    target_global_ccoeff,
    mixing_proportions,
    npso_dir,
    output_dir,
    seed,
    n_threads,
    model=DEFAULT_MODEL,
    search_strategy=DEFAULT_SEARCH_STRATEGY,
    search_initial_points=DEFAULT_SEARCH_INITIAL_POINTS,
    search_samples_per_T=DEFAULT_SEARCH_SAMPLES_PER_T,
    search_max_iters=100,
    search_diff_tol=0.005,
    search_step_tol=0.0001,
    search_t_min=0.0005,
):
    output_dir = standard_setup(output_dir)

    logging.info("Starting nPSO Generation...")
    logging.info(f"Seed: {seed} n_threads: {n_threads} model: {model}")

    N = int(N)
    m = int(m)
    gamma = float(gamma)
    c = int(c)
    target_global_ccoeff = float(target_global_ccoeff)
    mixing_proportions = [float(x) for x in (mixing_proportions or [])]
    _validate_model_inputs(model, c, mixing_proportions)
    logging.info(
        f"N={N} m={m} gamma={gamma} c={c} "
        f"target_ccoeff={target_global_ccoeff} "
        f"mixing_proportions={mixing_proportions}"
    )

    min_T, max_T = 0.0, 1.0
    # Signed residuals f(T) = ccoeff(T) - target at bracket endpoints.
    # Secant fires only once both are known with opposite signs.
    f_min_T, f_max_T = None, None
    best_T = None
    best_edge_df = None
    best_com_df = None
    best_global_ccoeff = None
    best_diff = None
    prev_global_ccoeff, global_ccoeff = None, None
    max_iters = search_max_iters
    npso_dir_abs = Path(npso_dir).resolve()
    matlab_wrapper_dir = (Path(__file__).resolve().parent / "matlab")

    # Resume: replay prior iters from search_log.json
    inputs_sha256 = _input_hash(
        N, m, gamma, c, target_global_ccoeff, seed, model, mixing_proportions
    )
    search_log_path = output_dir / SEARCH_LOG_NAME
    iter_records = _load_search_log(search_log_path, inputs_sha256)
    start_iter = 0
    converged_in_replay = False
    for idx, row in enumerate(iter_records):
        start_iter = idx + 1
        T_r = row["T"]
        cc_r = row["ccoeff"]
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
        if best_diff is not None and best_diff < search_diff_tol:
            converged_in_replay = True
            break
        if step_r < search_step_tol:
            converged_in_replay = True
            break
    if iter_records:
        logging.info(
            f"Resuming from search_log.json: replayed {len(iter_records)} iter(s); "
            f"start_iter={start_iter} best_T={best_T} best_ccoeff={best_global_ccoeff}"
        )

    runner = make_runner(n_threads, npso_dir_abs, matlab_wrapper_dir)
    fallback_holder = {"runner": None}

    def fallback_factory(force=False):
        if force and fallback_holder["runner"] is None:
            fallback_holder["runner"] = SubprocessRunner(
                n_threads, npso_dir_abs, matlab_wrapper_dir,
            )
        return fallback_holder["runner"]

    with tempfile.TemporaryDirectory(prefix="npso_scratch_", dir=output_dir) as scratch_str:
        scratch_dir = Path(scratch_str)
        try:
            # Resume case: replay recovered best_T but not the matrices — re-run once.
            if best_T is not None and best_edge_df is None:
                logging.info(f"Re-running best_T={best_T} to restore matrices post-resume.")
                prefix = scratch_dir / f"resume_{best_T:.5f}_"
                restored = runner.run_iter(
                    N, m, best_T, gamma, c, model, mixing_proportions, prefix, seed,
                )
                if restored is None and not isinstance(runner, SubprocessRunner):
                    fallback_holder["runner"] = SubprocessRunner(
                        n_threads, npso_dir_abs, matlab_wrapper_dir,
                    )
                    restored = fallback_holder["runner"].run_iter(
                        N, m, best_T, gamma, c, model, mixing_proportions, prefix, seed,
                    )
                if restored is not None:
                    best_edge_df, best_com_df = restored
                else:
                    logging.error("Resume re-run at best_T failed; treating log as stale.")
                    search_log_path.unlink(missing_ok=True)
                    iter_records = []
                    start_iter = 0
                    converged_in_replay = False
                    best_T = None
                    best_global_ccoeff = None
                    best_diff = None
                    min_T, max_T = 0.0, 1.0
                    f_min_T, f_max_T = None, None
                    prev_global_ccoeff = None
                    global_ccoeff = None

            if converged_in_replay:
                pass
            elif search_strategy == "bayesian":
                from skopt import Optimizer
                from skopt.space import Real

                opt = Optimizer(
                    dimensions=[Real(max(search_t_min, 1e-4), 1.0, name="T")],
                    base_estimator="GP",
                    acq_func="EI",
                    n_initial_points=min(search_initial_points, max_iters),
                    initial_point_generator="lhs",
                    random_state=int(seed) % (2**32 - 1),
                )
                # Replay prior iters into the GP so resume keeps its memory.
                for r in iter_records:
                    opt.tell([float(r["T"])], abs(float(r["ccoeff"]) - target_global_ccoeff))

                for it in range(start_iter, max_iters):
                    suggestion = opt.ask()
                    T = float(suggestion[0])
                    if T < search_t_min:
                        break
                    logging.info(f"[iter {it}] (bayesian) T={T}")
                    with timed("Generation"):
                        mean_cc, edge_df, com_df, samples, _ = _eval_T_with_samples(
                            runner, fallback_factory, scratch_dir, T,
                            N, m, gamma, c, model, mixing_proportions,
                            seed, search_samples_per_T, it,
                        )
                    if mean_cc is None:
                        logging.error(f"Missing MATLAB outputs at T={T}")
                        opt.tell(suggestion, 2.0)  # penalise so BO doesn't re-pick
                        continue
                    global_ccoeff = mean_cc
                    rec = {"T": T, "ccoeff": global_ccoeff}
                    if search_samples_per_T > 1:
                        rec["samples"] = samples
                    iter_records.append(rec)
                    _write_search_log(search_log_path, inputs_sha256, iter_records)
                    diff = abs(global_ccoeff - target_global_ccoeff)
                    opt.tell(suggestion, diff)
                    if best_global_ccoeff is None or diff < best_diff:
                        best_T = T
                        best_edge_df = edge_df
                        best_com_df = com_df
                        best_global_ccoeff = global_ccoeff
                        best_diff = diff
                    logging.info(
                        f"mean ccoeff={global_ccoeff:.4f} diff={diff:.4f} "
                        f"best T={best_T} best cc={best_global_ccoeff:.4f}"
                    )
                    if best_diff < search_diff_tol:
                        break
            else:
                # secant strategy
                for it in range(start_iter, max_iters):
                    T = _next_T(min_T, max_T, f_min_T, f_max_T)
                    if T < search_t_min:
                        break
                    logging.info(f"[iter {it}] (secant) T={T}")
                    with timed("Generation"):
                        mean_cc, edge_df, com_df, samples, _ = _eval_T_with_samples(
                            runner, fallback_factory, scratch_dir, T,
                            N, m, gamma, c, model, mixing_proportions,
                            seed, search_samples_per_T, it,
                        )
                    if mean_cc is None:
                        logging.error(f"Missing MATLAB outputs at T={T}")
                        global_ccoeff = None
                    else:
                        prev_global_ccoeff = global_ccoeff
                        global_ccoeff = mean_cc
                        logging.info(f"Global clustering coefficient: {global_ccoeff}")
                        rec = {"T": T, "ccoeff": global_ccoeff}
                        if search_samples_per_T > 1:
                            rec["samples"] = samples
                        iter_records.append(rec)
                        _write_search_log(search_log_path, inputs_sha256, iter_records)
                    diff = abs(global_ccoeff - target_global_ccoeff) if global_ccoeff is not None else 2.0
                    step = abs(prev_global_ccoeff - global_ccoeff) if prev_global_ccoeff is not None and global_ccoeff is not None else 2.0
                    if mean_cc is not None and (best_global_ccoeff is None or diff < best_diff):
                        best_T = T
                        best_edge_df = edge_df
                        best_com_df = com_df
                        best_global_ccoeff = global_ccoeff
                        best_diff = diff
                    logging.info(f"Step: {step}  Best T: {best_T}  Best ccoeff: {best_global_ccoeff}  Best diff: {best_diff}")
                    if best_diff is not None and best_diff < search_diff_tol:
                        break
                    if step < search_step_tol:
                        break
                    if global_ccoeff is not None:
                        f_T = global_ccoeff - target_global_ccoeff
                        if f_T < 0:
                            max_T = T
                            f_max_T = f_T
                        else:
                            min_T = T
                            f_min_T = f_T
        finally:
            runner.close()
            if fallback_holder["runner"] is not None:
                fallback_holder["runner"].close()

    if best_T is None or best_edge_df is None:
        last = (
            getattr(fallback_holder["runner"], "last_error", None)
            if fallback_holder["runner"] is not None else None
        ) or getattr(runner, "last_error", None)
        msg = "nPSO produced no viable output."
        if last:
            msg += f" Last MATLAB error:\n{last}"
        raise RuntimeError(msg)

    final_com = drop_singleton_clusters(best_com_df)
    final_edge = simplify_edges(best_edge_df)

    final_edge.to_csv(output_dir / "edge.csv", index=False)
    final_com.to_csv(output_dir / "com.csv", index=False)

    logging.info("nPSO generation complete.")


def _safe_remove(p):
    try:
        Path(p).unlink()
    except FileNotFoundError:
        pass


def _input_hash(N, m, gamma, c, target_ccoeff, seed, model, mixing_proportions):
    """Digest of search inputs; mismatch invalidates the log."""
    payload = json.dumps(
        {"N": int(N), "m": int(m), "gamma": float(gamma),
         "c": int(c), "target": float(target_ccoeff), "seed": int(seed),
         "model": str(model),
         "mixing_proportions": [float(x) for x in (mixing_proportions or [])]},
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _load_search_log(log_path, expected_hash):
    """Return replayable iter records, or [] if missing/incompatible (deletes stale)."""
    if not log_path.exists():
        return []
    try:
        with log_path.open() as f:
            doc = json.load(f)
        if doc.get("inputs_sha256") != expected_hash:
            raise ValueError("inputs_sha256 mismatch")
        iters = doc.get("iters", [])
        if not isinstance(iters, list):
            raise ValueError("iters field must be a list")
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logging.warning(f"search_log.json incompatible ({exc}); starting fresh.")
        log_path.unlink()
        return []
    return iters


def _write_search_log(log_path, inputs_sha256, iters):
    """Atomic write via sibling tempfile + os.replace (same-fs rename)."""
    doc = {"inputs_sha256": inputs_sha256, "iters": iters}
    tmp = log_path.with_suffix(log_path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(doc, f, sort_keys=True)
    os.replace(tmp, log_path)


def _next_T(min_T, max_T, f_min_T, f_max_T):
    """Secant when both endpoint residuals are known with opposite signs; else midpoint."""
    mid = min_T + (max_T - min_T) / 2
    if f_min_T is None or f_max_T is None:
        return mid
    if f_min_T * f_max_T > 0:
        return mid
    denom = f_max_T - f_min_T
    if denom == 0:
        return mid
    T_sec = min_T - f_min_T * (max_T - min_T) / denom
    margin = 0.05 * (max_T - min_T)
    if T_sec <= min_T + margin or T_sec >= max_T - margin:
        return mid
    return T_sec


def _parse_mixing_proportions(text):
    """CSV of floats → list. Empty string → []. Whitespace tolerated."""
    if not text:
        return []
    return [float(x) for x in text.split(",") if x.strip()]


def parse_args():
    parser = argparse.ArgumentParser(
        description="nPSO Graph Generator. All nPSO inputs are explicit flags "
                    "so the script can run standalone without a profile step."
    )
    parser.add_argument("--N", type=int, required=True,
                        help="Number of nodes")
    parser.add_argument("--m", type=int, required=True,
                        help="Half of the mean degree (approximately)")
    parser.add_argument("--gamma", type=float, required=True,
                        help="Power-law exponent of the degree distribution (>= 2)")
    parser.add_argument("--c", type=int, required=True,
                        help="Number of communities (GMM components)")
    parser.add_argument("--target-ccoeff", type=float, required=True,
                        help="Target global clustering coefficient")
    parser.add_argument("--mixing-proportions", type=str, default="",
                        help="Comma-separated rho_k values for nPSO2 "
                             "(must have c entries). Ignored for nPSO1 / nPSO3.")
    parser.add_argument("--npso-dir", type=str, required=True,
                        help="Path to the nPSO_model checkout (containing nPSO_model.m)")
    parser.add_argument("--output-folder", type=str, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n-threads", type=int, default=1)
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL,
                        help="nPSO angular-distribution variant")
    parser.add_argument("--search-strategy", choices=SEARCH_STRATEGIES,
                        default=DEFAULT_SEARCH_STRATEGY,
                        help="T-search strategy. 'bayesian' uses skopt's GP "
                             "+ EI (handles ccoeff sampling noise across MATLAB "
                             "realisations); 'secant' uses bisection + secant.")
    parser.add_argument("--search-initial-points", type=int,
                        default=DEFAULT_SEARCH_INITIAL_POINTS,
                        help="BO-only: number of LHS warm-up evaluations "
                             "before the GP takes over.")
    parser.add_argument("--search-samples-per-T", type=int,
                        default=DEFAULT_SEARCH_SAMPLES_PER_T,
                        help="MATLAB realisations to average per T probe. "
                             "Distinct seeds per realisation; ccoeff is the "
                             "empirical mean. Default 1.")
    parser.add_argument("--search-max-iters", type=int, default=100,
                        help="Max search iterations on T (counts T-probes, "
                             "not realisations).")
    parser.add_argument("--search-diff-tol", type=float, default=0.005,
                        help="Converge when |ccoeff - target| falls below this.")
    parser.add_argument("--search-step-tol", type=float, default=0.0001,
                        help="Converge when successive ccoeff steps fall below this.")
    parser.add_argument("--search-t-min", type=float, default=0.0005,
                        help="Give up the search once T falls below this.")
    return parser.parse_args()


def main():
    args = parse_args()
    run_npso_generation(
        args.N,
        args.m,
        args.gamma,
        args.c,
        args.target_ccoeff,
        _parse_mixing_proportions(args.mixing_proportions),
        args.npso_dir,
        args.output_folder,
        args.seed,
        args.n_threads,
        model=args.model,
        search_strategy=args.search_strategy,
        search_initial_points=args.search_initial_points,
        search_samples_per_T=args.search_samples_per_T,
        search_max_iters=args.search_max_iters,
        search_diff_tol=args.search_diff_tol,
        search_step_tol=args.search_step_tol,
        search_t_min=args.search_t_min,
    )


if __name__ == "__main__":
    main()
