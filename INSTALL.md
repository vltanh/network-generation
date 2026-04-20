# Installation

Each generator is independent — install only the generators you plan to use. Each section below is self-contained: pick a generator, follow its steps, and nothing else needs to be set up.

`graph-tool` is not pip-installable, so `sbm` and the two `ec-sbm` variants need conda. The ABCD / LFR / nPSO generators are pip-only on the Python side but depend on an `externals/` submodule and a host prerequisite (`julia`, a C++ toolchain, or `matlab`).

The examples use `conda` + `pip`, but any equivalent environment manager works.

## `sbm`

Python deps: `numpy`, `pandas`, `scipy`, `graph-tool`.

```bash
conda create -n sbm python=3.11 numpy pandas scipy -c conda-forge
conda activate sbm
conda install -c conda-forge graph-tool
```

No submodule init needed.

## `ec-sbm-v1`, `ec-sbm-v2`

Python deps: `pandas`, `scipy`, `graph-tool`, `numpy`, `pymincut`.

```bash
conda create -n ecsbm python=3.11 numpy pandas scipy -c conda-forge
conda activate ecsbm
conda install -c conda-forge graph-tool
pip install pymincut
```

No submodule init needed. Both versions share the same env.

## `abcd` / `abcd+o`

Python deps: `pandas`.  
Host deps: `julia` on `PATH`.

```bash
conda create -n abcd python=3.11 pandas -c conda-forge
conda activate abcd
git submodule update --init --recursive externals/abcd
julia -e 'using Pkg; Pkg.develop(path="externals/abcd"); Pkg.instantiate()'
```

## `lfr`

Python deps: `pandas`, `numpy`, `powerlaw`.  
Host deps: `make` + a C++ compiler (to build the benchmark).

```bash
conda create -n lfr python=3.11 numpy pandas -c conda-forge
conda activate lfr
pip install powerlaw
git submodule update --init --recursive externals/lfr
make -C externals/lfr/unweighted_undirected
```

The build produces the binary at `externals/lfr/unweighted_undirected/benchmark`.

## `npso`

Python deps: `pandas`, `numpy`, `powerlaw`, `networkit`.  
Host deps: `matlab` on `PATH` at run time (on the campus cluster: `module load matlab`).

```bash
conda create -n npso python=3.11 numpy pandas -c conda-forge
conda activate npso
pip install powerlaw networkit
git submodule update --init --recursive externals/npso
```

No build step — the MATLAB wrapper at [src/npso/matlab/run_npso.m](src/npso/matlab/run_npso.m) is tracked in-repo and auto-added to MATLAB's path by [src/npso/gen.py](src/npso/gen.py), so it does not need to be copied into the submodule.

## Optional: `--run-stats` / `--run-comp`

Only needed if you pass `--run-stats` or `--run-comp` to `run_generator.sh`. These flags invoke the `network_evaluation/` submodule, which has its own deps on top of whatever the chosen generator needs: `graph-tool`, `pymincut`, `scipy`, `sklearn`, `networkit`, `tqdm`, `matplotlib`, `seaborn` (plus `numpy` / `pandas`, already present for every generator).

```bash
git submodule update --init --recursive network_evaluation
conda install -c conda-forge graph-tool scikit-learn networkit tqdm matplotlib seaborn
pip install pymincut   # if not already installed for ec-sbm
```

If your generator env already has `graph-tool` (`sbm`, `ec-sbm-*`) or `networkit` (`npso`), you only need to add the missing pieces.

## Submodule path defaults

After installing a generator's submodule, `run_generator.sh` picks it up automatically via the defaults `--abcd-dir=externals/abcd`, `--lfr-binary=externals/lfr/unweighted_undirected/benchmark`, `--npso-dir=externals/npso`. Override those flags to point at a different checkout.
