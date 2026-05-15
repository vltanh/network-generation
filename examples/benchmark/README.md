# Generator Benchmark Artifacts

Generated with:

```bash
NW_ENV=/home/vltanh/miniconda3/envs/nwbench/bin tools/benchmark/bench_isolated.sh
```

Run date: 2026-05-15.

This directory records the full generator benchmark for `dnc` +
`sbm-flat-best+cc`, using seeds 1 through 10, 2 warmup runs, and 10 kept
runs per seed.

Key files:

- `summary.csv`: aggregate wall-clock summary by generator.
- `per_gen/results_<gen>.csv`: raw warmup and kept run rows for each generator.
- `host_snapshot.txt`: host and toolchain snapshot captured at run start.
- `memory_timeline.csv`: sampled cgroup memory timeline.
- `memory_peak.txt`: maximum `peak_bytes` observed in `memory_timeline.csv`.
- `memory_peak_per_gen.csv`: maximum sampled RSS by generator.
- `plots/`: rendered wall-clock, memory, and byte-identity figures.
