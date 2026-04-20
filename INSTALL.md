# Installation

The `abcd`, `abcd+o`, `lfr`, and `npso` generators depend on external tools vendored as submodules under `externals/`. Run the installer once after cloning:

```bash
./install.sh
```

This will:

1. Initialize the submodules (`externals/abcd`, `externals/lfr`, `externals/npso`).
2. Register ABCD with Julia via `Pkg.develop(path="externals/abcd")` (requires `julia` on `PATH`).
3. Build the LFR benchmark binary in `externals/lfr/unweighted_undirected/` (requires `make` and a C++ compiler).

## Host prerequisites per generator

| Generator | Needs |
| --- | --- |
| `abcd`, `abcd+o` | `julia` |
| `lfr` | `make` + C++ compiler; Python `powerlaw` |
| `npso` | `matlab` on PATH at run time (on campus cluster: `module load matlab`); Python `powerlaw` |
| `sbm`, `ec-sbm-v1`, `ec-sbm-v2` | Python env only (no external tool) |

The nPSO MATLAB wrapper `run_npso.m` lives under `src/npso/matlab/` (tracked in-repo) and is auto-added to MATLAB's path by `src/npso/gen.py`, so it does not need to be copied into the `externals/npso` submodule.

After installation, `run_generator.sh` defaults `--abcd-dir`, `--lfr-binary`, and `--npso-dir` to the submodule paths, so you can omit those flags unless you want to point at a different checkout.
