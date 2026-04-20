# Installation

Each generator is independent, so install only the generators you plan to use. Each section below is self-contained: pick a generator and follow its steps. If you want to use `--run-stats` or `--run-comp`, also follow the instructions in [Optional: run-stats and run-comp](#optional-run-stats-and-run-comp) to set up the `network_evaluation/` submodule.

## `sbm`

Python deps: `numpy`, `pandas`, `scipy`, `graph-tool`.

```bash
conda create -n sbm numpy pandas scipy
conda activate sbm
conda install -c conda-forge graph-tool
```

## `ec-sbm-v1`, `ec-sbm-v2`

Python deps: `pandas`, `scipy`, `graph-tool`, `numpy`, `pymincut`.

```bash
conda create -n ecsbm numpy pandas scipy
conda activate ecsbm
conda install -c conda-forge graph-tool
pip install git+https://github.com/vikramr2/python-mincut
```

`pymincut` is built from source. Requires a C++ toolchain, `openmpi`, and **`cmake >= 3.2` and `< 4.0`**.

Both versions share the same env.

## `abcd` / `abcd+o`

Python deps: `pandas`.  
Host deps: `julia` on `PATH`.

```bash
conda create -n abcd pandas
conda activate abcd
git submodule update --init --recursive externals/abcd
julia -e 'using Pkg; Pkg.develop(path="externals/abcd"); Pkg.instantiate()'
```

## `lfr`

Python deps: `pandas`, `numpy`, `powerlaw`.  
Host deps: `make` + a C++ compiler (to build the benchmark).

```bash
conda create -n lfr numpy pandas
conda activate lfr
pip install powerlaw
git submodule update --init --recursive externals/lfr
make -C externals/lfr/unweighted_undirected
```

The build produces the binary at `externals/lfr/unweighted_undirected/benchmark`.

## `npso`

Python deps: `pandas`, `numpy`, `powerlaw`, `networkit`.  
Host deps: `matlab` on `PATH` at run time.

```bash
conda create -n npso numpy pandas
conda activate npso
pip install powerlaw networkit
git submodule update --init --recursive externals/npso
```

The MATLAB wrapper at [src/npso/matlab/run_npso.m](src/npso/matlab/run_npso.m) is tracked in-repo and auto-added to MATLAB's path by [src/npso/gen.py](src/npso/gen.py), so it does not need to be copied into the submodule.

## Optional: Submodule path overrides

After installing a generator's submodule, `run_generator.sh` picks it up automatically via the defaults `--abcd-dir=externals/abcd`, `--lfr-binary=externals/lfr/unweighted_undirected/benchmark`, `--npso-dir=externals/npso`. Override those flags if you want to use a different path.

## Optional: run-stats and run-comp

Only needed if you pass `--run-stats` or `--run-comp` to `run_generator.sh`. These flags invoke the `network_evaluation/` submodule, which has its own deps on top of whatever the chosen generator needs: `graph-tool`, `pymincut`, `scipy`, `sklearn`, `networkit`, `tqdm`, `matplotlib`, `seaborn` (plus `numpy` / `pandas`, already present for every generator).

```bash
git submodule update --init --recursive network_evaluation
conda install -c conda-forge graph-tool # if not already installed for sbm, ec-sbm
conda install scipy scikit-learn tqdm matplotlib seaborn 
pip install networkit # if not already installed for npso
pip install git+https://github.com/vikramr2/python-mincut   # if not already installed for ec-sbm
```