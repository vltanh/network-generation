# Synthetic Network Generator

Generate a synthetic network that mirrors an empirical input (edge list +
reference clustering), then optionally compute statistics and compare
against the original.

Seven generators: `sbm`, `ec-sbm-v1`, `ec-sbm-v2`, `abcd`, `abcd+o`,
`lfr`, `npso`. See [docs/algorithms.md](docs/algorithms.md) for what
each preserves (degrees, block structure, mixing parameter, clustering
coefficient) and the determinism / cost trade-offs.

## Install

Per-generator; install only what you need. See [INSTALL.md](INSTALL.md).

## Usage

### Custom mode

Caller provides explicit paths.

```bash
./run_generator.sh \
    --generator <gen> --run-id <id> \
    --input-edgelist <path> --input-clustering <path> --output-dir <dir> \
    [OPTIONS]
```

### Macro mode

Auto-resolves paths from the standard `data/` tree.

```bash
./run_generator.sh --macro \
    --generator <gen> --run-id <id> \
    --network <id> --clustering-id <id> \
    [OPTIONS]
```

## Arguments

### Required

| Argument | Custom | Macro | Description |
| --- | :---: | :---: | --- |
| `--generator <gen>` | ✓ | ✓ | One of `sbm`, `ec-sbm-v1`, `ec-sbm-v2`, `abcd`, `abcd+o`, `lfr`, `npso`. |
| `--run-id <id>` | ✓ | ✓ | Numerical run identifier. |
| `--macro` |   | ✓ | Enable macro mode. |
| `--input-edgelist <p>` | ✓ |   | Empirical edge list CSV (header `source,target`). |
| `--input-clustering <p>` | ✓ |   | Reference clustering CSV (header `node_id,cluster_id`). |
| `--output-dir <dir>` | ✓ |   | Root output directory. |
| `--network <id>` |   | ✓ | Network identifier for `data/` lookup. |
| `--clustering-id <id>` |   | ✓ | Reference clustering identifier for `data/` lookup. |

### Optional

#### Output grouping

| Argument | Description |
| --- | --- |
| `--network <id>` | Sub-group outputs under `<generator>/<clustering-id>/<network>/` (custom mode). |
| `--clustering-id <id>` | Sub-group outputs under `<generator>/<clustering-id>/` (custom mode). |

#### Statistics & comparison

| Argument | Description |
| --- | --- |
| `--run-stats` | Compute synthetic network + cluster statistics. |
| `--run-comp` | Compare synthetic vs. empirical. Reference stats are computed from `--input-edgelist` + `--input-clustering` on first use if not provided (see below). |
| `--input-network-stats <p>` | Pre-computed empirical network stats directory. Optional: falls back to auto-compute if omitted. |
| `--input-cluster-stats <p>` | Pre-computed reference cluster stats directory. Optional: falls back to auto-compute if omitted. |

#### Caching

| Argument | Description |
| --- | --- |
| `--keep-state` | Preserve per-stage `.state/` directory (debug / resume). |

#### Runtime

| Argument | Default | Description |
| --- | --- | --- |
| `--seed <n>` | `1` | Seed forwarded to every generator. |
| `--n-threads <n>` | `1` | Threads for parallel backends. |
| `--timeout <dur>` | `3d` | Generation-step timeout. Any `timeout(1)` duration. |

Notes:

- `--seed 0` silently disables byte-reproducibility in graph-tool-backed
  generators (`sbm`, `ec-sbm-v1`, `ec-sbm-v2`).
- `--n-threads > 1` is untested and may break determinism. Leave at `1`
  unless you have a reason to change it. `lfr` is single-threaded and
  ignores this flag.

#### Generator-specific

Each generator accepts its own set of additional flags (path overrides,
model variants, outlier handling, etc.). Both dispatcher-level flags and
the pipeline-layer knobs (reachable via `src/<gen>/pipeline.sh` direct
invocation) are documented on the per-generator page:

- [`sbm`](docs/algorithms/sbm.md)
- [`ec-sbm-v1`](docs/algorithms/ec-sbm-v1.md)
- [`ec-sbm-v2`](docs/algorithms/ec-sbm-v2.md)
- [`abcd`](docs/algorithms/abcd.md) / [`abcd+o`](docs/algorithms/abcd+o.md)
- [`lfr`](docs/algorithms/lfr.md)
- [`npso`](docs/algorithms/npso.md)

See [docs/advanced-usage.md](docs/advanced-usage.md) for the naming
convention across layers and the cross-generator default matrix.

## Input / output path routing

Every run produces synthetic networks under a canonical sub-tree. Stats
land in a parallel tree when `--run-stats` is passed.

### Custom mode

**Inputs** (caller-provided):

- `<input-edgelist>`: reference edge list
- `<input-clustering>`: reference clustering
- `<input-network-stats>` + `<input-cluster-stats>`: optional. Used only with `--run-comp`. If omitted, computed from the reference inputs and cached under `<output-dir>/stats/reference/...` (see Outputs).

**Outputs**: under `<output-dir>/networks/<generator>[/<clustering-id>][/<network>]/<run-id>/`

Bracketed segments `[/<clustering-id>]` and `[/<network>]` are inserted
only when the corresponding optional flag is passed; otherwise they drop
out of the path entirely.

- `edge.csv`, `com.csv`: synthetic edge list + clustering
- `params.txt`, `done`, `run.log`: state hash, invocation params, log
- `source.json` (optional): provenance
- `.state/` (optional, kept only with `--keep-state`): per-stage intermediates

Stats under `<output-dir>/stats/<generator>[/<clustering-id>][/<network>]/<run-id>/` (same bracket semantics):

- `cluster/`, `network/`: per-subsystem metrics
- `comparison.csv`: present only when `--run-comp` was passed

Auto-computed reference stats (only when `--run-comp` is passed without
`--input-network-stats` / `--input-cluster-stats`) land at:

- `<output-dir>/stats/reference/network[/<network>]/`
- `<output-dir>/stats/reference/cluster[/<clustering-id>][/<network>]/`

They are populated on first use from the reference inputs and reused on subsequent runs.

### Macro mode

**Inputs** (auto-resolved under `data/`):

- `data/empirical_networks/networks/<network>/<network>.csv`
- `data/reference_clusterings/clusterings/<clustering-id>/<network>/com.csv`
- `data/empirical_networks/stats/<network>/` and
  `data/reference_clusterings/stats/<clustering-id>/<network>/` (for `--run-comp`; computed and cached at these paths on first use if absent)

**Outputs** (auto-routed under `data/`):

- `data/synthetic_networks/networks/<generator>/<clustering-id>/<network>/<run-id>/`: same file set as custom mode
- `data/synthetic_networks/stats/<generator>/<clustering-id>/<network>/<run-id>/`: same layout as custom mode

## Examples

Full pipeline on the committed example (`ec-sbm-v2` on `dnc` +
`sbm-flat-best+cc`), preserving per-stage `.state/`:

```bash
./run_generator.sh \
    --generator ec-sbm-v2 --run-id 0 --seed 1 \
    --network dnc --clustering-id sbm-flat-best+cc \
    --input-edgelist      examples/input/empirical_networks/networks/dnc/dnc.csv \
    --input-clustering    examples/input/reference_clusterings/clusterings/sbm-flat-best+cc/dnc/com.csv \
    --input-network-stats examples/input/empirical_networks/stats/dnc \
    --input-cluster-stats examples/input/reference_clusterings/stats/sbm-flat-best+cc/dnc \
    --output-dir          examples/output/synthetic_networks \
    --run-stats --run-comp --keep-state
```

Macro-mode equivalent (reads / writes the `data/` tree):

```bash
./run_generator.sh --macro \
    --generator ec-sbm-v2 --run-id 0 --seed 1 \
    --network dnc --clustering-id sbm-flat-best+cc \
    --run-stats --run-comp --keep-state
```

## Further reading

- [docs/algorithms.md](docs/algorithms.md): what each generator
  preserves; runtime and determinism guarantees.
- [docs/advanced-usage.md](docs/advanced-usage.md): per-generator
  pipeline flags, naming convention, default matrix.

## Tests

Test suite under `tests/`, split by subsystem (`common`, `profile_py`,
`dispatcher`, `wrappers`, `generators`). Fast tests have no external
tooling; end-to-end tests (`tests/generators/`, gated by `-m slow`)
skip generators whose externals are not installed. See
[INSTALL.md](INSTALL.md) for setup.

## Benchmarking

[`scripts/benchmark/bench_gens.sh`](scripts/benchmark/bench_gens.sh)
measures end-to-end wall-clock and byte-reproducibility across the seven
generators (default: 2 warmup + 10 kept runs per seed, seeds 1-10,
single-threaded, on the shipped `dnc + sbm-flat-best+cc` example). See
the Runtime section of [docs/algorithms.md](docs/algorithms.md) for
reference numbers.

## Acknowledgements

- **`sbm`**: [graph-tool](https://graph-tool.skewed.de/).
- **`ec-sbm-v1`**: [illinois-or-research-analytics/ec-sbm](https://github.com/illinois-or-research-analytics/ec-sbm); uses [python-mincut](https://github.com/vikramr2/python-mincut).
- **`ec-sbm-v2`**: extended from `ec-sbm-v1`.
- **`abcd` / `abcd+o`**: [ABCDGraphGenerator.jl](https://github.com/bkamins/ABCDGraphGenerator.jl).
- **`lfr`**: [LFR benchmark](https://www.santofortunato.net/resources).
- **`npso`**: [nPSO_model](https://github.com/biomedical-cybernetics/nPSO_model).

Portions of the code, documentation, and tests were written with the
help of [Claude](https://www.anthropic.com/claude) via Claude Code.
