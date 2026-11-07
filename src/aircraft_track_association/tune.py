"""Random search for Gaussian-model weights on a labelled partition (validation by default).

Cost terms do not depend on the weights, so each scenario's raw terms are computed once and
every trial only re-weights them and re-solves the assignment. The search:
- samples each weight log-uniformly in [0.01, 10], keeping plausibility > vel_w2 > pos_w2,
- keeps the scales and the no-match cost fixed (scaling every weight is the same as moving the
  no-match cost, so searching both would be redundant),
- minimises 2 * wrong links + unresolved links, because a wrong link is worse than a withheld
  one, breaking ties by the larger mean margin between each true link's cost and its best
  competitor (relative to the no-match cost), so the chosen set is the least fragile one.
Trial 0 is the current weights, so the result is never worse on the searched partition.
"""

from pathlib import Path

import numpy as np

from .associate import assign, cost_matrix
from .io import load_successors
from .track import load_tracklets
from .weights import NO_MATCH_COST, SCALES, WEIGHTS

TERMS = list(SCALES)


def prepare(scenario_dirs: list[Path]) -> list[tuple[np.ndarray, list[tuple[int, int]], int]]:
    """Per scenario: scaled raw terms (k, n, n) with inf where gated, true links, link count."""
    out = []
    for d in scenario_dirs:
        tracklets = load_tracklets(d)
        _, terms = cost_matrix(tracklets)
        n = len(tracklets)
        raw = np.full((len(TERMS), n, n), np.inf)
        for (i, j), pt in terms.items():
            raw[:, i, j] = [pt[k] / SCALES[k] for k in TERMS]
        index = {t.id: i for i, t in enumerate(tracklets)}
        truth = [(index[s], index[t]) for s, t in load_successors(d).items() if t]
        out.append((raw, truth, len(truth)))
    return out


def score(data, weights: dict) -> dict:
    w = np.array([weights[k] for k in TERMS])
    wrong = unresolved = 0
    margins = []
    for raw, truth, _ in data:
        cost = np.tensordot(w, np.where(np.isinf(raw), 0, raw), axes=1)
        cost[np.isinf(raw[0])] = np.inf
        links = assign(cost, NO_MATCH_COST)
        wrong += sum(links[i] != j for i, j in truth if i in links)
        unresolved += sum(i not in links for i, _ in truth)
        wrong += sum(1 for i in links if all(i != s for s, _ in truth))  # link from a last tracklet
        for i, j in truth:
            others = np.delete(cost[i], j)
            margins.append((others.min() - cost[i, j]) / NO_MATCH_COST if others.size else 0.0)
    margins = np.clip(margins, -10, 10)  # inf margins (no competitor) count as large
    return {
        "objective": 2 * wrong + unresolved,
        "wrong": wrong,
        "unresolved": unresolved,
        "margin": float(np.mean(margins)),
    }


def sample(rng: np.random.Generator) -> dict:
    while True:
        w = {k: float(10 ** rng.uniform(-2, 1)) for k in TERMS}
        if w["plausibility"] > w["vel_w2"] > w["pos_w2"]:
            return w


def search(scenario_dirs: list[Path], trials: int = 2000, seed: int = 0) -> list[dict]:
    """All trials, best first."""
    data = prepare(scenario_dirs)
    rng = np.random.default_rng(seed)
    results = []
    for t in range(trials):
        w = dict(WEIGHTS) if t == 0 else sample(rng)
        results.append({"trial": t, "weights": w, **score(data, w)})
    return sorted(results, key=lambda r: (r["objective"], -r["margin"], r["trial"]))
