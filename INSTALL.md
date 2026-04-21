# Installation

Each generator is independent. Install only the generators you plan to use — each section below is self-contained. If you want `--run-stats` or `--run-comp`, also follow [Optional: run-stats and run-comp](#optional-run-stats-and-run-comp).

Dependency policy: prefer `conda` main channel or `pip` over `conda-forge`. `graph-tool` is the one unavoidable conda-forge dep (not packaged on PyPI, not on conda main).

## `sbm`

Python deps: `numpy`, `pandas`, `scipy`, `graph-tool`.

```bash
conda create -n sbm python=3.11 numpy pandas scipy -y
conda activate sbm
conda install -c conda-forge graph-tool -y
```

## `ec-sbm-v1`, `ec-sbm-v2`

Python deps: `numpy`, `pandas`, `scipy`, `graph-tool`, `pymincut`.  
Build deps: C++ toolchain, `openmpi`, `cmake >= 3.2` and `< 4.0`.

```bash
conda create -n ecsbm python=3.11 numpy pandas scipy setuptools wheel pybind11 -y
conda activate ecsbm
conda install -c conda-forge graph-tool -y
pip install 'cmake<4' && pip install --no-build-isolation git+https://github.com/vikramr2/python-mincut
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

After installing a generator's submodule, `run_generator.sh` picks it up automatically via the defaults `--abcd-dir=externals/abcd`, `--lfr-binary=externals/lfr/unweighted_undirected/benchmark`, `--npso-dir=externals/npso`. Override those flags if you want to use a different path.

## Tests

Activate the conda env that has the generator deps on `PATH` before running pipeline-level tests:

```bash
conda activate <your-env>
python -m pytest tests/common tests/profile_py tests/dispatcher    # unit tests only (~seconds)
python -m pytest tests/wrappers tests/simple_gens tests/ec_sbm     # end-to-end (minutes; skips gens missing externals)
python -m pytest -m slow tests/simple_gens tests/ec_sbm            # slow gens (full end-to-end, real samplers)
```

If `pytest`'s subprocess shell needs additional paths (e.g. your conda env or juliaup install isn't on the inherited `PATH`), set `NW_TEST_PATH_PREFIX` to a colon-separated prefix that will be prepended to `PATH` inside spawned `run_generator.sh` calls:

```bash
NW_TEST_PATH_PREFIX="$CONDA_PREFIX/bin:$HOME/.juliaup/bin" python -m pytest tests/simple_gens
```

## Optional: run-stats and run-comp

Only needed if you pass `--run-stats` or `--run-comp` to `run_generator.sh`. These flags invoke the `network_evaluation/` submodule, which has its own deps on top of whatever the chosen generator needs: `graph-tool`, `pymincut`, `scipy`, `sklearn`, `networkit`, `tqdm`, `matplotlib`, `seaborn` (plus `numpy` / `pandas`, already present for every generator).

```bash
git submodule update --init --recursive network_evaluation
conda install -c conda-forge graph-tool   # if not already installed for sbm, ec-sbm
conda install scipy scikit-learn tqdm matplotlib seaborn -y
pip install networkit                     # if not already installed for npso
pip install 'cmake<4' && pip install --no-build-isolation git+https://github.com/vikramr2/python-mincut   # if not already installed for ec-sbm
```
