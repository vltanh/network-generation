# Installation

Each generator is independent — only install what you plan to use. Every generator needs a working Python environment with a generator-specific set of packages. Four of them (`abcd`, `abcd+o`, `lfr`, `npso`) additionally depend on a vendored external tool under `externals/`.

Recommended: create a conda env (graph-tool is not pip-installable). The examples below use `conda`, but any equivalent works.

## Shared Python dependencies (all generators)

Every generator runs `src/profile.py` to derive empirical bounds from the input edgelist and clustering. That step needs:

- `numpy`
- `pandas`
- `scipy`
- `pymincut` (see https://github.com/illinois-or-research-analytics/pymincut)

```bash
conda create -n netgen python=3.11 numpy pandas scipy -c conda-forge
conda activate netgen
pip install pymincut
```

Add the generator-specific packages below on top of this base env.

## Optional: statistics and comparison (`--run-stats`, `--run-comp`)

Needed only if you pass `--run-stats` or `--run-comp` to `run_generator.sh`. These flags invoke scripts under the `network_evaluation/` submodule.

```bash
git submodule update --init --recursive network_evaluation
conda install -c conda-forge graph-tool
```

`network_evaluation` additionally uses `scipy.sparse.linalg` (already installed above) and `pymincut` (already installed above).

## `sbm`, `ec-sbm-v1`, `ec-sbm-v2`

Python-only generators, but they require `graph-tool`, which is **not** available via pip.

```bash
conda install -c conda-forge graph-tool
```

No submodule init needed.

## `abcd` / `abcd+o`

Requires `julia` on `PATH`, plus the shared Python deps above.

```bash
git submodule update --init --recursive externals/abcd
julia -e 'using Pkg; Pkg.develop(path="externals/abcd"); Pkg.instantiate()'
```

## `lfr`

Requires `make` and a C++ compiler for the benchmark build, plus Python `powerlaw` on top of the shared deps.

```bash
git submodule update --init --recursive externals/lfr
make -C externals/lfr/unweighted_undirected
pip install powerlaw
```

The build produces the binary at `externals/lfr/unweighted_undirected/benchmark`.

## `npso`

Requires `matlab` on `PATH` at run time (on the campus cluster: `module load matlab`), plus Python `powerlaw` and `networkit` on top of the shared deps.

```bash
git submodule update --init --recursive externals/npso
pip install powerlaw networkit
```

No build step — the MATLAB wrapper [src/npso/matlab/run_npso.m](src/npso/matlab/run_npso.m) is tracked in-repo and auto-added to MATLAB's path by [src/npso/gen.py](src/npso/gen.py), so it does not need to be copied into the submodule.

## Submodule path defaults

After installing a generator's submodule, `run_generator.sh` picks it up automatically via the defaults `--abcd-dir=externals/abcd`, `--lfr-binary=externals/lfr/unweighted_undirected/benchmark`, `--npso-dir=externals/npso`. Override those flags if you want to point at a different checkout.
