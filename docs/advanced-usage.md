# Advanced usage: per-generator pipeline flags

`run_generator.sh` is the recommended entry point and covers the common
dispatcher-level flags (see the repo [`README.md`](../README.md)). Each
generator's `src/<gen>/pipeline.sh` accepts additional knobs that the
dispatcher does not plumb through. To use them, bypass the dispatcher and
invoke the per-generator pipeline directly:

```bash
./src/<gen>/pipeline.sh --input-edgelist <p> --input-clustering <p> --output-dir <d> [FLAGS]
```

## Naming convention across layers

Generator-specific flags are **namespaced** at the dispatcher
(`run_generator.sh`) and **short** at the per-generator pipeline layer:

| Dispatcher                  | `src/<gen>/pipeline.sh`                |
| --------------------------- | -------------------------------------- |
| `--abcd-dir <p>`            | `--package-dir <p>` (abcd, abcd+o)    |
| `--lfr-binary <p>`          | `--binary <p>` (lfr)                   |
| `--npso-dir <p>`            | `--package-dir <p>` (npso)             |
| `--npso-model <m>`          | `--model <m>` (npso)                   |
| `--ec-sbm-dir <p>`          | `--package-dir <p>` (ec-sbm-v1, ec-sbm-v2) |

Shared flags that already carry no ambiguity (`--seed`, `--n-threads`,
`--timeout`, `--keep-state`, `--outlier-mode`, etc.) keep the same name
at both layers.

## Cross-generator pipeline flags

| Argument | Applies to | Description |
| --- | --- | --- |
| `--outlier-mode <m>` | all | Stage-1 handling of singleton clusters: `excluded` \| `singleton` \| `combined`. `ec-sbm-v1` only accepts `excluded`. |
| `--drop-outlier-outlier-edges` / `--keep-outlier-outlier-edges` | all | Drop or keep outlier-outlier edges in the profile. |
| `--match-degree` / `--no-match-degree` | `sbm`, `abcd`, `abcd+o`, `lfr`, `npso` | Toggle Stage-4 degree-matching rewire. (Always on for `ec-sbm-v1` / `ec-sbm-v2`.) |
| `--match-degree-algorithm <a>` | `sbm`, `abcd`, `abcd+o`, `lfr`, `npso`, `ec-sbm-v2` | `greedy` \| `true_greedy` \| `random_greedy` \| `rewire` \| `hybrid`. |
| `--remap` / `--no-remap` | same as above | Rank-pair synthetic nodes to reference by descending degree before matching. |

## Per-generator defaults

| Generator | `--outlier-mode` | OO edges | match-degree | algorithm | remap |
| --- | --- | --- | --- | --- | --- |
| `sbm` | `combined` | keep | off | `true_greedy` | off |
| `ec-sbm-v1` | `excluded` (fixed) | keep | on (fixed) | `greedy` (fixed) | n/a |
| `ec-sbm-v2` | `excluded` | keep | on (fixed) | `true_greedy` (dispatcher) | n/a |
| `abcd` | `singleton` | keep | off | `true_greedy` | on |
| `abcd+o` | `singleton` | **drop** | off | `true_greedy` | on |
| `lfr` | `singleton` | keep | off | `true_greedy` | on |
| `npso` | `singleton` | keep | off | `true_greedy` | on |

## Generator-unique flags

Flags that only one generator accepts (e.g. `--gen-outlier-mode` /
`--edge-correction` for `ec-sbm-v2`, `--model` for `npso`, plus the
standalone `gen.py` scalar CLI for `npso`) are documented on the
per-generator page under [algorithms/](algorithms/).
