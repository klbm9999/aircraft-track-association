"""Link each tracklet to at most one successor with a cost matrix and Hungarian assignment.

For every ordered pair (source, target) where the target starts after the source ends, six
cost terms are computed, each divided by a fixed scale and weighted (both set in weights.py).
Pairs that overlap or run backwards in time get infinite cost. A Hungarian solve over an
augmented matrix then picks a one-to-one set of links: each source either takes one target
or a "no match" option, so every tracklet ends up with at most one successor and predecessor.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment

from .track import Tracklet
from .weights import NO_MATCH_COST, SCALES, WEIGHTS


def psd_sqrt(s: np.ndarray) -> np.ndarray:
    """Square root of a symmetric PSD matrix; stable when it is singular (e.g. flat altitude)."""
    w, v = np.linalg.eigh(s)
    return (v * np.sqrt(np.clip(w, 0, None))) @ v.T


def wasserstein2(m1, s1, m2, s2) -> float:
    """Closed-form W2 distance between two Gaussians:
    W2^2 = |m1 - m2|^2 + tr(S1 + S2 - 2 (S2^1/2 S1 S2^1/2)^1/2)."""
    r2 = psd_sqrt(s2)
    cross = psd_sqrt(r2 @ s1 @ r2)
    w2_sq = np.sum((m1 - m2) ** 2) + np.trace(s1 + s2 - 2 * cross)
    return float(np.sqrt(max(w2_sq, 0.0)))  # clip tiny negative rounding error


def one_minus_cos(a: np.ndarray, b: np.ndarray) -> float:
    """0 when a and b point the same way, 2 when opposite."""
    return 1.0 - float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def pair_terms(src: Tracklet, dst: Tracklet) -> dict[str, float] | None:
    """Raw cost terms for linking src -> dst, or None if dst does not start after src ends."""
    dt = dst.t[0] - src.t[-1]
    if dt <= 0:  # same-aircraft tracklets never overlap
        return None
    # Plausibility: how far the source would fly at its end speed over the gap, versus how far
    # the target's start actually is from the source's end. A ratio of 1 is a perfect fit.
    projected = np.linalg.norm(src.end_vel) * dt
    actual = np.linalg.norm(dst.start_pos - src.end_pos)
    return {
        "plausibility": abs(np.log(actual / projected)),
        # Is the target ahead of the source along its direction of travel?
        "heading": one_minus_cos(src.vel_mean, dst.pos_mean - src.pos_mean),
        # Are both tracklets flying the same way?
        "vel_cos": one_minus_cos(src.vel_mean, dst.vel_mean),
        "vel_w2": wasserstein2(src.vel_mean, src.vel_cov, dst.vel_mean, dst.vel_cov),
        "pos_w2": wasserstein2(src.pos_mean, src.pos_cov, dst.pos_mean, dst.pos_cov),
        "dt": dt,
    }


def cost_matrix(tracklets: list[Tracklet], weights=WEIGHTS) -> tuple[np.ndarray, dict]:
    """Weighted cost per (source, target) index pair; inf where the pair is gated out.
    Also returns the raw terms per pair, for diagnostics."""
    n = len(tracklets)
    terms = {
        (i, j): pt
        for i, a in enumerate(tracklets)
        for j, b in enumerate(tracklets)
        if i != j and (pt := pair_terms(a, b)) is not None
    }
    cost = np.full((n, n), np.inf)
    for (i, j), pt in terms.items():
        cost[i, j] = sum(w * pt[k] / SCALES[k] for k, w in weights.items())
    return cost, terms


def match(
    tracklets: list[Tracklet], no_match_cost: float = NO_MATCH_COST, weights=WEIGHTS
) -> dict[str, str]:
    """Return {source tracklet id: successor tracklet id} for every linked source.

    The augmented 2n x 2n matrix has four blocks:
        [ real links (n x n)          | source unmatched: diag no_match_cost ]
        [ target unmatched: 0 (n x n) | dummy-dummy filler: 0                ]
    so a source is linked only when its link costs less than leaving it unmatched, and any
    target may go without a predecessor at no cost.
    """
    n = len(tracklets)
    cost, _ = cost_matrix(tracklets, weights)
    big = np.full((2 * n, 2 * n), np.inf)
    big[:n, :n] = cost
    big[:n, n:][np.diag_indices(n)] = no_match_cost
    big[n:, :n] = 0.0
    big[n:, n:] = 0.0
    rows, cols = linear_sum_assignment(big)
    return {tracklets[i].id: tracklets[j].id for i, j in zip(rows, cols) if i < n and j < n}
