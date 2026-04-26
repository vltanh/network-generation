"""Plot benchmark artifacts from results.csv + memory_timeline.csv.

Outputs into ``examples/benchmark/plots/``:

- ``wallclock.png``: per-gen mean wall-clock over kept runs, with std
  error bars. Warmup runs excluded. Bars ordered by mean ascending.
- ``memory_timeline.png``: cgroup RSS sampled every second over the
  whole run, one line per gen band if the bench was single-threaded
  (our default), else one line for the run.
- ``byte_identity.png``: per-(gen, seed) heatmap. Green = all kept
  runs' edge.csv agree; red = they disagreed; grey = gen failed.

Run from the benchmark output dir or pass ``--bench-dir <p>``.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def load_results(path: Path):
    """Return (kept_times, edge_hashes, any_failure) per (gen, seed).

    kept_times: {(gen, seed): [float, ...]}
    edge_hashes: {(gen, seed): set[str]}
    any_failure: {(gen, seed): bool}
    """
    kept_times: dict = defaultdict(list)
    edge_hashes: dict = defaultdict(set)
    failed: dict = defaultdict(bool)
    with open(path) as f:
        for row in csv.DictReader(f):
            key = (row["gen"], row["seed"])
            if row["time_s"] == "FAIL":
                failed[key] = True
                continue
            if row["phase"] == "kept":
                kept_times[key].append(float(row["time_s"]))
                if row["edge_sha256"]:
                    edge_hashes[key].add(row["edge_sha256"])
    return kept_times, edge_hashes, failed


def plot_wallclock(kept_times, out_path: Path):
    per_gen: dict = defaultdict(list)
    for (gen, _seed), ts in kept_times.items():
        per_gen[gen].extend(ts)
    if not per_gen:
        return
    means = {g: float(np.mean(ts)) for g, ts in per_gen.items()}
    stds = {g: float(np.std(ts, ddof=1)) if len(ts) > 1 else 0.0 for g, ts in per_gen.items()}
    gens = sorted(per_gen.keys(), key=lambda g: means[g])
    xs = np.arange(len(gens))
    ys = [means[g] for g in gens]
    errs = [stds[g] for g in gens]

    fig, ax = plt.subplots(figsize=(max(4.8, 0.7 * len(gens) + 1.5), 3.2))
    ax.bar(xs, ys, yerr=errs, capsize=3, color="#4c72b0", edgecolor="black", linewidth=0.5)
    for i, (m, s) in enumerate(zip(ys, errs)):
        ax.text(i, m + s + max(ys) * 0.01, f"{m:.2f}s", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(xs)
    ax.set_xticklabels(gens, rotation=25, ha="right")
    ax.set_ylabel("wall-clock (s)")
    ax.set_title("Per-generator wall-clock (kept runs, mean ± std)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_memory_timeline(mem_path: Path, out_path: Path):
    """Plot the cgroup memory timeline. If the timeline carries a `gen`
    column, colour-code each segment by gen and add a per-gen legend."""
    if not mem_path.is_file():
        return
    ts, rss, peak, gens = [], [], [], []
    with open(mem_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts.append(float(row["ts_s"]))
                rss.append(int(row["rss_bytes"]))
                peak.append(int(row["peak_bytes"]))
                gens.append(row.get("gen", "_") or "_")
            except (KeyError, ValueError):
                continue
    if not ts:
        return

    ts_arr = np.array(ts)
    rss_mb = np.array(rss) / (1024 ** 2)
    peak_mb = np.array(peak) / (1024 ** 2)

    fig, ax = plt.subplots(figsize=(7.6, 3.4))
    unique_gens = [g for g in dict.fromkeys(gens) if g not in ("", "_")]
    if unique_gens:
        palette = plt.colormaps.get_cmap("tab10")
        ax.plot(ts_arr, rss_mb, color="#bbbbbb", linewidth=0.8, alpha=0.8)
        for i, g in enumerate(unique_gens):
            mask = np.array([gg == g for gg in gens])
            if mask.any():
                ax.plot(ts_arr[mask], rss_mb[mask], color=palette(i % 10),
                        linewidth=1.6, label=g)
        ax.legend(frameon=False, loc="upper left", ncol=2, fontsize=8)
    else:
        ax.plot(ts_arr, rss_mb, label="memory.current", color="#4c72b0", linewidth=1.2)
        ax.plot(ts_arr, peak_mb, label="memory.peak", color="#dd8452", linewidth=1.0, linestyle="--")
        ax.legend(frameon=False, loc="upper left")
    ax.set_xlabel("elapsed (s)")
    ax.set_ylabel("RSS (MiB)")
    ax.set_title("cgroup memory over run, segmented by gen")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_byte_identity(edge_hashes, failed, all_keys, out_path: Path):
    if not all_keys:
        return
    gens = sorted({k[0] for k in all_keys})
    seeds = sorted({k[1] for k in all_keys}, key=lambda s: int(s) if s.isdigit() else s)
    grid = np.zeros((len(gens), len(seeds)), dtype=int)  # 0=fail/grey, 1=green, 2=red
    for i, g in enumerate(gens):
        for j, s in enumerate(seeds):
            key = (g, s)
            if failed.get(key):
                grid[i, j] = 0
            elif len(edge_hashes.get(key, set())) == 1:
                grid[i, j] = 1
            elif len(edge_hashes.get(key, set())) > 1:
                grid[i, j] = 2
            else:
                grid[i, j] = 0

    cmap = matplotlib.colors.ListedColormap(["#bbbbbb", "#55a868", "#c44e52"])
    fig, ax = plt.subplots(figsize=(max(4.8, 0.5 * len(seeds) + 2), 0.5 * len(gens) + 1.5))
    ax.imshow(grid, cmap=cmap, vmin=0, vmax=2, aspect="auto")
    ax.set_xticks(np.arange(len(seeds)))
    ax.set_xticklabels(seeds)
    ax.set_yticks(np.arange(len(gens)))
    ax.set_yticklabels(gens)
    ax.set_xlabel("seed")
    ax.set_title("Byte-identity of kept-run edge.csv (green=match, red=mismatch, grey=fail)")
    ax.spines[["top", "right", "bottom", "left"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-dir", type=Path,
                        default=Path(__file__).resolve().parents[2] / "examples" / "benchmark")
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--memory-timeline", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    bench_dir: Path = args.bench_dir
    results = args.results or (bench_dir / "results.csv")
    memory_timeline = args.memory_timeline or (bench_dir / "memory_timeline.csv")
    out_dir = args.out or (bench_dir / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)

    kept, hashes, failed = load_results(results)
    all_keys = set(kept) | set(hashes) | set(failed)
    plot_wallclock(kept, out_dir / "wallclock.png")
    plot_memory_timeline(memory_timeline, out_dir / "memory_timeline.png")
    plot_byte_identity(hashes, failed, all_keys, out_dir / "byte_identity.png")
    print(f"plots written to {out_dir}")


if __name__ == "__main__":
    main()
