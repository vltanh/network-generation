# Generator dispatcher configs

Each `*.sh` file in this directory registers one generator with
`run_generator.sh`. The basename (minus `.sh`) is the generator name
passed via `--generator`.

## Adding a new generator

Create `configs/<name>.sh` declaring:

```bash
GEN_PIPELINE="src/<name>/pipeline.sh"   # relative to repo root
GEN_REQUIRED_DIR_VAR=""                 # name of var holding a required external dir, e.g. "abcd_dir"; empty if none
GEN_REQUIRED_DIR_FLAG=""                # human-readable CLI flag name for the error message
GEN_EXTRA_ARGS=(                        # forwarded to pipeline.sh after the common flags
    --seed "${seed}"
    --n-threads "${n_threads}"
)
```

Common flags `--input-edgelist`, `--input-clustering`, `--output-dir` are forwarded
automatically. `GEN_EXTRA_ARGS` is evaluated in the dispatcher's scope, so it can
reference any variable defined in `run_generator.sh` (e.g. `${seed}`, `${n_threads}`,
`${abcd_dir}`).

## Adding a CLI arg for one generator

Edit only that generator's config file. If the new arg corresponds to a new global
variable parsed from the command line, add the flag to the parser in `run_generator.sh`
and reference it from `GEN_EXTRA_ARGS`. Otherwise hardcode the value in the array.

## `GEN_EXTRA_ARGS` vs `GEN_CLI_ARGS`

Two similarly-named arrays live at different layers of the pipeline. They
are not interchangeable:

- **`GEN_EXTRA_ARGS`** lives in `configs/<name>.sh` (this directory). It is
  forwarded by the dispatcher (`run_generator.sh`) to the per-generator
  `pipeline.sh` *after* the common `--input-edgelist / --input-clustering /
  --output-dir` flags. Use it for pipeline-level knobs like `--seed`,
  `--n-threads`, `--package-dir`.

  Naming convention: at the dispatcher layer, generator-specific flags are
  namespaced (`--abcd-dir`, `--lfr-binary`, `--npso-dir`, `--npso-model`); the
  generator config in this directory translates them to short names at the
  `pipeline.sh` layer (`--package-dir` for `abcd` / `abcd+o` / `npso`,
  `--binary` for `lfr`, `--model` for `npso`). Shared flags that already
  carry no ambiguity (`--seed`, `--n-threads`, `--timeout`, `--keep-state`)
  keep the same name across layers.

- **`GEN_CLI_ARGS`** lives in the per-generator wrapper
  `src/<name>/pipeline.sh`. It is forwarded by the shared dispatcher
  (`src/_common/simple_pipeline.sh`) to `gen.py` in stage 2. Use it for
  generator-script flags like `--node-id`, `--degree`, `--edge-counts`
  that `gen.py` needs but `pipeline.sh` wouldn't know about.

Concretely, why `sbm.sh` doesn't list `--input-clustering` in
`GEN_EXTRA_ARGS`: the SBM wrapper adds it to `gen.py` itself via
`GEN_CLI_ARGS` (because SBM's `gen.py` re-reads the clustering to size
its output), while the dispatcher already forwards it to `pipeline.sh`
as a common flag. One is pipeline-scope; the other is gen.py-scope.
