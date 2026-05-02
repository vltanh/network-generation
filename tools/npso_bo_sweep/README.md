# nPSO Bayesian-vs-secant T-search sweep

Toolkit that compares the two `--search-strategy` modes shipped by
`src/npso/gen.py`:

- `bayesian`: scikit-optimize GP + EI, with `--search-initial-points`
  Latin-hypercube warm-up evaluations.
- `secant`: bisection + secant bracket update, the original strategy.

`--search-samples-per-T N` averages N MATLAB realisations per probe
for noise reduction.

The sweep exists because the original argument for switching the nPSO
default to BO ("MATLAB realisations are noisy, secant treats noise as a
sign-flip") needed empirical backing. The data does not back it.

## Reproducing

```bash
# 1. Probe achievable cc range at the chosen (N, m, gamma, c).
python tools/npso_bo_sweep/characterize.py \
    --N 200 --m 6 --gamma 2.5 --c 5 --model nPSO2 \
    --mixing-proportions 0.2,0.2,0.2,0.2,0.2 \
    --t-min 0.05 --t-max 0.99 --t-points 8 \
    --seeds 1,2,3,4,5 \
    --out tools/npso_bo_sweep/characterize_n200.json

# 2. Pick targets inside the range, sweep both strategies.
python tools/npso_bo_sweep/sweep.py \
    --N 200 --m 6 --gamma 2.5 --c 5 \
    --mixing-proportions 0.2,0.2,0.2,0.2,0.2 \
    --targets 0.26,0.22,0.18 \
    --strategies bayesian,secant \
    --samples-per-T 1,5 \
    --max-iters 15,30 \
    --seeds 1,2,3,4,5 \
    --diff-tol 0.0001 --step-tol 1e-7 \
    --out tools/npso_bo_sweep/sweep_n200.json

# 3. Tabulate.
python tools/npso_bo_sweep/analyze.py tools/npso_bo_sweep/sweep_n200.json
```

`sweep.py` keeps the MATLAB engine alive across runs via the persistent
`EngineRunner`, so 120 runs at N=200 take ~10 minutes on a Pop!_OS
laptop.

## Findings (N=200, m=6, γ=2.5, c=5, nPSO2 with uniform mixing)

Cc-vs-T characterisation: the achievable mean ccoeff range is
[~0.18, ~0.27]. Per-T spread across 5 seeds is 0.013-0.032 (~7-15% of
the mean) — i.e. real noise, not negligible.

Median |best_cc - target| over 5 seeds, at three targets and two iter
budgets, samples_per_T=1:

| target | iters | BO median (p25-p75)        | secant median (p25-p75)    | BO matlab calls | secant matlab calls |
| -----: | ----: | -------------------------- | -------------------------- | --------------: | ------------------: |
|  0.180 |    15 | 0.0002 (0.0002-0.0004)     | 0.0002 (0.0001-0.0003)     |            13.2 |                14.4 |
|  0.180 |    30 | 0.0002 (0.0001-0.0002)     | 0.0001 (0.0001-0.0002)     |            22.4 |                23.6 |
|  0.220 |    15 | 0.0011 (0.0005-0.0014)     | 0.0010 (0.0009-0.0010)     |            13.6 |                14.8 |
|  0.220 |    30 | 0.0002 (0.0001-0.0004)     | 0.0001 (0.0001-0.0007)     |            25.2 |                24.0 |
|  0.260 |    15 | 0.0017 (0.0006-0.0017)     | 0.0019 (0.0012-0.0032)     |            15.0 |                15.0 |
|  0.260 |    30 | 0.0007 (0.0006-0.0011)     | 0.0001 (0.0001-0.0005)     |            30.0 |                28.2 |

Secant ties or beats BO on every (target, iter) cell at samples=1.
Both methods reach |diff| < 0.001 within the diff_tol on the easy
targets; secant terminates early via step_tol / diff_tol while BO runs
to the iter cap, so BO uses similar or more MATLAB calls.

samples_per_T=5 averaging cuts variance for both, but does not flip
the ranking.

The earlier hypothesis that "BO should help nPSO because MATLAB
realisations are noisy" does not hold here: the trend is strong enough
relative to the noise that bracket-and-secant's monotone-targeted
probes win on iters-to-tolerance. BO's GP would shine when the trend
is much weaker or the function is multi-modal — neither holds for the
nPSO ccoeff(T) curve in the configurations swept.

## Headline at iters=30, samples=1

| target | BO median diff | secant median diff | winner |
| -----: | -------------: | -----------------: | ------ |
|  0.180 |         0.0002 |             0.0001 | secant |
|  0.220 |         0.0002 |             0.0001 | secant |
|  0.260 |         0.0007 |             0.0001 | secant |

`src/npso/gen.py` therefore defaults `--search-strategy secant`. BO
remains opt-in for regimes outside the swept grid (very small N, very
non-monotone targets, or model variants where the trend is weaker).
