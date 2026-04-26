# Benchmark guide

Reproducible wall-clock + memory + byte-identity numbers for the seven
generators on a single empirical input, recorded under
`examples/benchmark/`. The harness is deliberately simple: shell scripts
+ `/usr/bin/time -v` + a cgroup memory sampler. Driver lives at
`tools/benchmark/bench_isolated.sh`.

## Quick start

```bash
# from the repo root, plain shell, no env vars required
tools/benchmark/bench_isolated.sh

# verify the toolchain without running the bench
tools/benchmark/bench_isolated.sh --check
```

The driver auto-detects the project's conda env via:

1. `$NW_ENV` if you set it,
2. otherwise the active `$CONDA_PREFIX`,
3. otherwise an env named `$NW_ENV_NAME` (default `nwbench`) found by
   `conda env list`.

Each candidate is validated by importing `graph_tool` from its python
binary; a base conda or unrelated env that lacks `graph_tool` is
skipped, so the right env is picked even if a wrong one is active.

A preflight check runs every requested gen's deps before any benchmark
work starts and aborts with a single clear message if anything is
missing (use `--skip-preflight` to bypass; `--check` runs only the
preflight and exits).

Defaults: 7 gens (`sbm,ec-sbm-v1,ec-sbm-v2,abcd,abcd+o,lfr,npso`), seeds
1..10, 10 kept runs + 2 warmups per (gen, seed), CPU pin to cores 0-3,
16 GiB memory cap, sample interval 1 s. Wall time on a quiet i9-12900HK:
~12-18 minutes.

**Per-gen output isolation.** Each gen writes its own
`per_gen/results_<gen>.csv` and the merged `results.csv` is rebuilt
from those files after each gen finishes. Effects:
- one gen failing or being killed never touches another gen's data,
- `--gens npso` only refreshes `results_npso.csv`,
- the merged file is regenerated after every gen, so the on-disk
  state always reflects the gens that have actually finished.

`per_gen/` is the source of truth on disk; `results.csv` is the merged
view. Edit either; the next run will rebuild the merge.

Outputs land under `examples/benchmark/`:

| File | What it carries |
|---|---|
| `results.csv` | per-run row: `gen,seed,phase,run,time_s,peak_rss_kb,edge_sha256,com_sha256` |
| `host_snapshot.txt` | host + toolchain at run start (CPU model, kernel, conda env, gen versions) |
| `memory_timeline.csv` | per-second cgroup memory.current + peak, tagged with the active gen |
| `memory_peak.txt` | last-observed cgroup memory.peak (whole run, all gens) |
| `memory_peak_per_gen.csv` | per-gen peak from the timeline (max over rows tagged with that gen) |
| `plots/` | wallclock bar, memory timeline (gen-coloured), byte-identity grid |

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--gens <csv>` | `sbm,ec-sbm-v1,ec-sbm-v2,abcd,abcd+o,lfr,npso` | restrict to a subset |
| `--seeds <space-separated>` | `1 2 3 4 5 6 7 8 9 10` | seed list |
| `--runs <N>` | `10` | kept runs per (gen, seed) |
| `--warmup <N>` | `2` | warmup runs per (gen, seed) |
| `--shield` | off | best-effort exclusivity (real-time priority + RT IO class) |

Env overrides: `MEM_CAP`, `CPU_LIST`, `SAMPLE_INTERVAL_S`, `NW_ENV`,
`NW_NPSO_ENV`. See header of `bench_isolated.sh` for full doc.

## CPU exclusivity

The default `taskset -c 0-3` plus `systemd-run -p AllowedCPUs=0-3` pins
the bench to four cores. **It does not stop other userspace tasks from
scheduling on those same cores.** For paper-grade numbers you need to
add real exclusivity.

Three tiers, weakest to strongest:

### Tier 1 — quiet desktop (no extra setup)

Just close the noisy stuff before benchmarking and rely on the pin:

- Close browser, IDE, Slack, file indexer, anything CPU-bound.
- Plug in AC power so the CPU does not throttle.
- Set the CPU governor to `performance`:
  ```bash
  sudo cpupower frequency-set -g performance
  ```
- Optionally disable Turbo Boost so per-core frequency is uniform:
  ```bash
  echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo
  ```
- Pin to physical cores only. On i9-12900HK confirm SMT siblings via
  `lscpu -e`; pick one core per pair.

Reproducibility on a clean run: ~1-3% std on wall time.

### Tier 2 — `--shield` flag (best-effort, no reboot)

`--shield` runs the bench under `chrt --fifo 1 ionice -c 1 -n 0`:

- SCHED_FIFO at priority 1 preempts all SCHED_OTHER user tasks on the
  pinned cores. They run in the gaps.
- ionice RT class preempts IO from other classes.

Both need `RLIMIT_RTPRIO` raised on the user. Default Linux ships with
`ulimit -r 0`, which is why the unconfigured shield call fails with
`chrt: failed to set pid 0's policy: Operation not permitted`. Raise it
once:

```bash
sudo tee /etc/security/limits.d/99-rtbench.conf <<'EOF'
@nwbench  -  rtprio  99
@nwbench  -  nice    -19
EOF
sudo groupadd -f nwbench
sudo usermod -aG nwbench $USER
# log out + back in (or `newgrp nwbench` for the current shell)
ulimit -r 99    # confirm
```

Then:

```bash
NW_ENV=$CONDA_PREFIX/bin tools/benchmark/bench_isolated.sh --shield
```

The shield still does not move other userspace tasks off the pinned
cores; it just out-prioritises them. Reproducibility on a busy desktop:
~0.5-1.5% std.

### Tier 3 — true exclusivity

Two paths, both require root and at least one reboot.

**Boot-time isolation** (gold standard):

```
# /etc/default/grub
GRUB_CMDLINE_LINUX="... isolcpus=0-3 nohz_full=0-3 rcu_nocbs=0-3"
sudo update-grub
sudo reboot
```

Cores 0-3 are removed from the scheduler pool entirely. No task lands
on them unless it explicitly pins via `taskset` or
`sched_setaffinity`. Tickless kernel + RCU callbacks off too. Run the
bench normally; pinned bench is the only userspace work on those cores.

**`cset shield`** (runtime, no reboot):

```bash
sudo apt install cpuset
sudo cset shield --cpu=0-3 --kthread=on
sudo cset shield --exec -- env NW_ENV=$CONDA_PREFIX/bin \
    tools/benchmark/bench_isolated.sh
sudo cset shield --reset
```

Moves all userspace tasks off 0-3 into the unshielded set;
`--kthread=on` migrates movable kernel threads. Closest you get to
isolcpus without rebooting.

Reproducibility under either: <0.5% std.

## What the harness records vs interprets

`results.csv` is the source of truth. Per (gen, seed), the harness
records 10 kept runs (after 2 discarded warmups) and reports
`mean / std / min / max`. Edge and com sha256 are byte-identity checks:
all 10 runs at a given (gen, seed) should produce identical output if
the seed pin works (every gen passes today).

`memory_peak_per_gen.csv` is a max over the cgroup `memory.current`
samples tagged with that gen. The cgroup includes the gen's process
tree plus any persistent helpers (graph-tool import, MATLAB engine).

`/usr/bin/time -v` peak RSS in `results.csv` is per-`run_generator.sh`
process tree. It and the cgroup peak agree to within a few MB on quiet
runs.

## Re-running with different inputs

The bench is hardcoded to `dnc + sbm-flat-best+cc + run-id 0` (the
shipped example). To bench a different empirical network, edit
`INPUT_EDGELIST` and `INPUT_CLUSTERING` near the top of
`tools/benchmark/bench_gens.sh`.
