"""Instrumented canonical match_degree runner.

Loads ``src/match_degree.py`` after monkey-patching the four module-level
``random`` calls it uses (``shuffle``, ``choices``, ``randrange``,
``random``) to log every draw to a JSON-serializable trace. Logging
mirrors the original PRNG state machine: ``shuffle`` is re-implemented
inline using the same ``_randbelow`` calls so state advances identically
to the unpatched ``random.shuffle``; the other three delegate to the
real implementation and just record the result.

CLI: reads ``{algo, payload, seed}`` on stdin, writes
``{edges, trace, achieved_deg, simple_graph}`` on stdout.

The harness builds the (target_deg, exist_neighbor) pair the JS port also
consumes and feeds both sides the same trace so JS replay byte-equals
canonical edges (modulo set-iteration order; the harness compares sorted
edge lists).
"""
from __future__ import annotations

import json
import random as _random
import sys
from collections import deque
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "src"))

# Import after sys.path manipulation; before monkey-patch so the module
# captures references to the canonical random first.
import match_degree as _md  # noqa: E402


class TraceRecorder:
    def __init__(self):
        self.trace = []
        self._real_choices = _random.choices
        self._real_randrange = _random.randrange
        self._real_random = _random.random
        # Module-level instance used by the bare random.* calls.
        self._inst = _random._inst

    def install(self):
        _random.choices = self._choices
        _random.shuffle = self._shuffle
        _random.randrange = self._randrange
        _random.random = self._random_call

    def restore(self):
        _random.choices = self._real_choices
        _random.shuffle = self._inst.shuffle
        _random.randrange = self._real_randrange
        _random.random = self._real_random

    def _shuffle(self, x):
        # Re-implement Random.shuffle inline so we capture the j_seq while
        # advancing the PRNG state identically to the canonical impl.
        rb = self._inst._randbelow
        n = len(x)
        j_seq = []
        for i in reversed(range(1, n)):
            j = rb(i + 1)
            j_seq.append(int(j))
            x[i], x[j] = x[j], x[i]
        self.trace.append({"site": "shuffle", "n": n, "j_seq": j_seq})

    def _choices(self, population, weights=None, *, cum_weights=None, k=1):
        result = self._real_choices(
            population, weights=weights, cum_weights=cum_weights, k=k,
        )
        # match_degree only ever calls with k=1 and unique-population lists.
        # Record the index so the JS port can pick population[i] without
        # re-running the cumulative weights logic.
        indices = [population.index(r) for r in result]
        self.trace.append({
            "site": "choices",
            "n": len(population),
            "k": k,
            "indices": indices,
        })
        return result

    def _randrange(self, *args, **kwargs):
        val = self._real_randrange(*args, **kwargs)
        self.trace.append({
            "site": "randrange",
            "args": list(args),
            "value": int(val),
        })
        return val

    def _random_call(self):
        val = self._real_random()
        # Float repr is round-trip-safe across JSON in both languages.
        self.trace.append({"site": "random", "value": float(val)})
        return val


ALGO_FN = {
    "greedy": _md.match_missing_degrees_greedy,
    "true_greedy": _md.match_missing_degrees_true_greedy,
    "random_greedy": _md.match_missing_degrees_random_greedy,
}


def _payload_to_state(payload):
    iids = list(payload["iids"])
    target = {int(k): int(v) for k, v in payload["target_deg"].items()}
    exist = {int(k): set(int(x) for x in v)
             for k, v in payload["exist_neighbor"].items()}
    for iid in iids:
        target.setdefault(iid, 0)
        exist.setdefault(iid, set())
    return iids, target, exist


def run_canonical(algo, payload, seed):
    iids, target, exist = _payload_to_state(payload)
    recorder = TraceRecorder()
    _random.seed(seed)
    recorder.install()
    try:
        if algo in ALGO_FN:
            edges = ALGO_FN[algo](dict(target), exist)
        elif algo == "rewire":
            edges, _invalid = _md.match_missing_degrees_rewire(
                dict(target), exist, max_retries=10,
            )
        elif algo == "hybrid":
            edges = _md.match_missing_degrees_hybrid(dict(target), exist)
        else:
            raise SystemExit(f"unknown algo: {algo}")
    finally:
        recorder.restore()
    edge_list = sorted((int(u), int(v)) for u, v in edges)
    achieved = {iid: 0 for iid in iids}
    for u, v in edge_list:
        achieved[u] += 1
        achieved[v] += 1
    seen = set()
    simple = True
    for u, v in edge_list:
        if u == v:
            simple = False
            break
        key = (min(u, v), max(u, v))
        if key in seen:
            simple = False
            break
        seen.add(key)
    return {
        "edges": edge_list,
        "achieved_deg": {str(k): v for k, v in achieved.items()},
        "trace": recorder.trace,
        "simple_graph": simple,
    }


def main():
    src = sys.stdin.read()
    job = json.loads(src)
    out = run_canonical(job["algo"], job["payload"], int(job["seed"]))
    sys.stdout.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
