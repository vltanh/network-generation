# Generator registry

Each `*.sh` file in this directory registers one generator with `run_generator.sh`.
The basename (minus `.sh`) is the generator name passed via `--generator`.

## Adding a new generator

Create `generators/<name>.sh` declaring:

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
