# Installation

Each generator is independent. Install only the generators you plan to use; each section below is self-contained. If you want `--run-stats` or `--run-comp`, also follow [Optional: run-stats and run-comp](#optional-run-stats-and-run-comp).

## `sbm`

Python deps: `numpy`, `pandas`, `scipy`, `graph-tool`.

```bash
conda create -n sbm python=3.11 numpy pandas scipy -y
conda activate sbm
conda install -c conda-forge graph-tool -y
```

## `ec-sbm-v1`, `ec-sbm-v2`

Initialize the submodule, then follow its
[`INSTALL.md`](externals/ec-sbm/INSTALL.md) for the conda recipe + the
CMake 4.0+ workaround.

```bash
git submodule update --init --recursive externals/ec-sbm
```

## `abcd` / `abcd+o`

Python deps: `pandas`.  
Host deps: `julia` on `PATH`. Recommended installer: [juliaup](https://github.com/JuliaLang/juliaup).

```bash
conda create -n abcd python=3.11 pandas -y
conda activate abcd
curl -fsSL https://install.julialang.org | sh -s -- -y
export PATH="$HOME/.juliaup/bin:$PATH"   # add to your shell rc for future shells
git submodule update --init --recursive externals/abcd
julia -e 'using Pkg; Pkg.develop(path="externals/abcd"); Pkg.instantiate()'
```

## `lfr`

Python deps: `numpy`, `pandas`, `powerlaw`.  
Build deps: `make` + a C++ compiler.

```bash
conda create -n lfr python=3.11 numpy pandas -y
conda activate lfr
pip install powerlaw
git submodule update --init --recursive externals/lfr
make -C externals/lfr/unweighted_undirected
```

The build produces the binary at `externals/lfr/unweighted_undirected/benchmark`.

## `npso`

Python deps: `numpy`, `pandas`, `powerlaw`, `networkit`.  
Host deps: MATLAB R2024a on `PATH` at run time. R2024a's Statistics and Machine Learning Toolbox is required.

```bash
conda create -n npso python=3.11 numpy pandas -y
conda activate npso
pip install powerlaw networkit
git submodule update --init --recursive externals/npso
```

The MATLAB wrapper at [src/npso/matlab/run_npso.m](src/npso/matlab/run_npso.m) is auto-added to MATLAB's path by [src/npso/gen.py](src/npso/gen.py).

**Optional: MATLAB Engine for Python (recommended for large speedup)**

With the engine, the nPSO pipeline starts one persistent MATLAB session per invocation instead of spawning `matlab` per bisection iter. If the import fails at runtime, gen.py falls back to the per-iter subprocess path.

The engine package for R2024a (24.1) is officially Python 3.9-3.11. Install from PyPI; setting `LD_LIBRARY_PATH` first makes the build find your MATLAB install:

```bash
LD_LIBRARY_PATH=$(dirname $(dirname $(readlink -f $(which matlab))))/bin/glnxa64:$LD_LIBRARY_PATH \
  pip install matlabengine==24.1.4
```

No `LD_LIBRARY_PATH` is needed at run time (matlabengine configures its own loader path at import).

## Optional: Submodule path overrides

After installing a generator's submodule, `run_generator.sh` picks it up automatically via the defaults `--abcd-dir=externals/abcd`, `--lfr-binary=externals/lfr/unweighted_undirected/benchmark`, `--npso-dir=externals/npso`, `--ec-sbm-dir=externals/ec-sbm`. Override those flags if you want to use a different path.

## Optional: Tests

Activate the conda env that has the generator deps on `PATH` before running pipeline-level tests. If `pytest`'s subprocess shell needs additional paths (e.g. your conda env or juliaup install isn't on the inherited `PATH`), set `NW_TEST_PATH_PREFIX` to a colon-separated prefix that will be prepended to `PATH` inside spawned `run_generator.sh` calls:

```bash
NW_TEST_PATH_PREFIX="$CONDA_PREFIX/bin:$HOME/.juliaup/bin" python -m pytest tests/generators -m slow
```

## Optional: run-stats and run-comp

Only needed if you pass `--run-stats` or `--run-comp` to
`run_generator.sh`. Those flags invoke the `network_evaluation/`
submodule; initialize it and install its dependencies per
[`network_evaluation/INSTALL.md`](network_evaluation/INSTALL.md).

```bash
git submodule update --init --recursive network_evaluation
```
