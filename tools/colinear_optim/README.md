# colinear_optim

Place 2-D points so that no triple is visibly colinear.

## Why

The shared 20-node netgen fixture in `vltanh.github.io/netgen/shared.js` has
no edge data baked into its layout — every page draws a different
random graph on top of the same coordinates. That means an edge
between two of the nodes can pass through the visible disc of a third
unrelated node when the three sit close to a straight line, and the
viz reads as "node C is on edge AB" even though the underlying graph
has no such relation.

This is a 2-D point-placement problem: minimize the number (and the
worst case) of colinear triples while keeping every pair far enough
apart that the per-node spokes don't overlap.

## Metric

For a triple `(A, B, C)` we measure the perpendicular distance from
`C` to the line `AB`, but only count the triple when `C`'s projection
onto `AB` lies strictly between `A` and `B` (parameter `t` in
`(0.05, 0.95)`). Triples where `C` projects off the segment never
read as "edge through node C" in the viz.

A triple is "colinear" if its perpendicular distance is below
`target` px. `target` is what the viz can tolerate before the third
node's disc starts overlapping the line — defaults to 22 px (node
radius 13 + a small safety margin).

## Constraints

- `minPair` (default 58 px): minimum allowed pairwise distance. Set
  this to `2 * (node_radius + spoke_length)` so two nodes' spokes
  cannot touch even when both fully extended.
- `maxDisplace` (default 70 px): per-node cap on `|new − original|`,
  so the optimizer can't drift a node out of its semantic cluster.

## Run

C++ build (~50-100x faster than the JS reference):

```
g++ -O3 -std=c++17 optim_positions.cpp -o optim_positions
./optim_positions input.json > output.json
```

The C++ build needs `nlohmann/json` as a single header (`json.hpp`)
in the same directory; fetch from
https://github.com/nlohmann/json/releases.

JS fallback (no compile, easy to tweak):

```
node optim_positions.mjs input.json > output.json
```

`input.json`:

```json
{
  "positions": { "1": {"x": -126, "y": -180}, "2": {"x": -169, "y": -242}, ... },
  "target": 22,
  "minPair": 58,
  "maxDisplace": 70,
  "restarts": 60,
  "iters": 200000,
  "clusters": [["1","2","3","4","5","6","7","8"], ["9","10","11","12","13","14"]],
  "clusterMaxDiameter": 320
}
```

`clusters` (optional) groups node ids into clusters; the maximum
pairwise distance inside each group is capped at
`clusterMaxDiameter` so members read as a coherent cluster. C++ build
only — the JS port doesn't yet implement this constraint.

Output prints the best config found plus `score.worst` (the worst
near-colinear distance) and `score.minPair` (the minimum pairwise
distance achieved). Non-zero exit code if no feasible configuration
was found within the budget.

## Algorithm

Simulated annealing with random restarts. Each restart seeds from a
random perturbation of the supplied positions, then a local nudge
chain (one to three nodes per step, ±`nudgePx` px each) anneals
against a cubic-deficit penalty (each colinear triple contributes
`(target − distance)³` to the penalty). The best seen config across
all restarts wins, ranked by `(worst-case distance descending, total
penalty ascending)`.

This is best-effort. With `N` nodes packed into a fixed canvas there
are `N · C(N − 1, 2)` triples to satisfy and the math doesn't allow
a perfect solution past a certain density. The optimizer pushes the
worst triple's distance up but won't get it past what the geometry
allows.
