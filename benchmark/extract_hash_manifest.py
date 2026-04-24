"""Post-process bench_gens.sh's results.csv into a compact hash manifest.

For each (gen, seed) we record the edge.csv + com.csv sha256 seen across
all kept runs. Byte-identity is asserted: if any kept run differs from
the others within a (gen, seed), we flag it.

Also emits summary stats (mean / std / min / max) per (gen, seed) and
per gen (aggregated over seeds) for time and peak RSS.

Usage:
  python benchmark/extract_hash_manifest.py \
      --results benchmark/results.csv \
      --out-manifest benchmark/hash_manifest.csv \
      --out-summary benchmark/summary.csv
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--results", required=True)
    p.add_argument("--out-manifest", required=True)
    p.add_argument("--out-summary", required=True)
    return p.parse_args()


def stats(xs):
    if not xs:
        return None, None, None, None
    m = sum(xs) / len(xs)
    s = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0
    return m, s, min(xs), max(xs)


def main():
    args = parse_args()

    # (gen, seed) -> list of kept time_s
    times = defaultdict(list)
    # (gen, seed) -> list of kept peak_rss_kb
    rss = defaultdict(list)
    # (gen, seed) -> set of distinct edge/com hashes across kept runs
    edge_hashes = defaultdict(set)
    com_hashes = defaultdict(set)
    fail_rows = []

    with open(args.results) as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["gen"], row["seed"])
            if row.get("time_s") == "FAIL":
                fail_rows.append(row)
                continue
            if row["phase"] != "kept":
                continue
            times[key].append(float(row["time_s"]))
            if row.get("peak_rss_kb"):
                try:
                    rss[key].append(int(row["peak_rss_kb"]))
                except ValueError:
                    pass
            edge_hashes[key].add(row.get("edge_sha256", ""))
            com_hashes[key].add(row.get("com_sha256", ""))

    # Manifest: one row per (gen, seed) with the canonical hashes.
    with open(args.out_manifest, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gen", "seed", "edge_sha256", "com_sha256",
                    "edge_byte_identical", "com_byte_identical",
                    "n_kept_runs"])
        for key in sorted(edge_hashes.keys(), key=lambda k: (k[0], int(k[1]))):
            gen, seed = key
            eh_set = edge_hashes[key]
            ch_set = com_hashes[key]
            eh = next(iter(eh_set)) if len(eh_set) == 1 else "MIXED"
            ch = next(iter(ch_set)) if len(ch_set) == 1 else "MIXED"
            w.writerow([
                gen, seed, eh, ch,
                "yes" if len(eh_set) == 1 else "NO",
                "yes" if len(ch_set) == 1 else "NO",
                len(times.get(key, [])),
            ])

    # Summary: per (gen, seed) + per-gen aggregate.
    with open(args.out_summary, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "scope", "gen", "seed", "n",
            "time_mean_s", "time_std_s", "time_min_s", "time_max_s",
            "rss_mean_kb", "rss_max_kb",
        ])
        # Per (gen, seed).
        for key in sorted(times.keys(), key=lambda k: (k[0], int(k[1]))):
            gen, seed = key
            tm, ts, tmn, tmx = stats(times[key])
            rmean, _, _, rmax = stats(rss[key]) if rss[key] else (None, None, None, None)
            w.writerow([
                "per-seed", gen, seed, len(times[key]),
                f"{tm:.4f}" if tm is not None else "",
                f"{ts:.4f}" if ts is not None else "",
                f"{tmn:.4f}" if tmn is not None else "",
                f"{tmx:.4f}" if tmx is not None else "",
                f"{rmean:.0f}" if rmean is not None else "",
                f"{rmax:.0f}" if rmax is not None else "",
            ])
        # Per-gen aggregate across all seeds.
        gens = sorted({g for g, _ in times.keys()})
        for gen in gens:
            all_t = [t for (g, _), ts in times.items() if g == gen for t in ts]
            all_r = [r for (g, _), rs in rss.items() if g == gen for r in rs]
            tm, ts, tmn, tmx = stats(all_t)
            rmean, _, _, rmax = stats(all_r) if all_r else (None, None, None, None)
            w.writerow([
                "per-gen", gen, "", len(all_t),
                f"{tm:.4f}" if tm is not None else "",
                f"{ts:.4f}" if ts is not None else "",
                f"{tmn:.4f}" if tmn is not None else "",
                f"{tmx:.4f}" if tmx is not None else "",
                f"{rmean:.0f}" if rmean is not None else "",
                f"{rmax:.0f}" if rmax is not None else "",
            ])

    print(f"Wrote {args.out_manifest} ({sum(1 for _ in open(args.out_manifest)) - 1} rows)")
    print(f"Wrote {args.out_summary}")
    if fail_rows:
        print(f"WARN: {len(fail_rows)} FAILed runs (see results.csv).")


if __name__ == "__main__":
    main()
