"""Baseline: a constant-velocity projection rule with greedy one-to-one selection.

For each ordered pair (source, target) where the target starts after the source ends:
- displacement: horizontal distance from the source's last report to the target's first report,
- projected: the source's last horizontal speed times the time gap,
- ratio = displacement / projected; score = |ratio - 1| (0 is a perfect fit, lower is better).

A pair is acceptable when its score is within TOLERANCE (5%). Acceptable pairs are taken
greedily in score order, skipping any whose source already has a successor or whose target
already has a predecessor. Because targets always start after sources end, links cannot form
cycles. The rule checks speed only, not direction, so it cannot tell apart two aircraft that
are the same distance away.

"Last speed" is measured from the source's last report back to the latest report at least
MIN_BASELINE_S earlier: consecutive reports can be under a second apart, and with ~200 m of
position noise the raw last step gives speeds off by hundreds of m/s.
"""

import numpy as np

from .decision import candidate, record, short
from .track import MIN_BASELINE_S, Tracklet

TOLERANCE = 0.05


def last_velocity(tr: Tracklet) -> np.ndarray:
    """Horizontal velocity over the source's final >= MIN_BASELINE_S (or its whole length)."""
    j = max(int(np.searchsorted(tr.t, tr.t[-1] - MIN_BASELINE_S, side="right")) - 1, 0)
    return (tr.p[-1, :2] - tr.p[j, :2]) / (tr.t[-1] - tr.t[j])


def pair_measurements(src: Tracklet, dst: Tracklet) -> dict | None:
    dt = dst.t[0] - src.t[-1]
    if dt <= 0:  # same-aircraft tracklets never overlap
        return None
    displacement = float(np.linalg.norm(dst.p[0, :2] - src.p[-1, :2]))
    projected = float(np.linalg.norm(last_velocity(src)) * dt)
    return {
        "dt_s": dt,
        "displacement_m": displacement,
        "projected_m": projected,
        "ratio": displacement / projected,
    }


def decide(tracklets: list[Tracklet], tolerance: float = TOLERANCE) -> list[dict]:
    ranked = {}  # source id -> candidates, best first
    for src in tracklets:
        cands = [
            candidate(dst.id, abs(m["ratio"] - 1), m)
            for dst in tracklets
            if dst is not src and (m := pair_measurements(src, dst)) is not None
        ]
        ranked[src.id] = sorted(cands, key=lambda c: (c["score"], c["tracklet_id"]))

    # Greedy: best-fitting acceptable pairs first; ids break exact ties deterministically.
    pairs = sorted(
        (c["score"], s, c["tracklet_id"])
        for s, cands in ranked.items()
        for c in cands
        if c["score"] <= tolerance
    )
    successor, predecessor = {}, {}
    for _, s, d in pairs:
        if s not in successor and d not in predecessor:
            successor[s], predecessor[d] = d, s

    out = []
    for tr in tracklets:
        cands, succ = ranked[tr.id], successor.get(tr.id)
        if succ:
            m = next(c for c in cands if c["tracklet_id"] == succ)["measurements"]
            reason = (
                f"displacement {m['displacement_m']:.0f} m vs projected {m['projected_m']:.0f} m "
                f"over {m['dt_s']:.0f} s (ratio {m['ratio']:.3f}, within +/-{tolerance:.0%})"
            )
        elif not cands:
            reason = "no tracklet starts after this one ends"
        elif cands[0]["score"] > tolerance:
            reason = (
                f"closest candidate {short(cands[0]['tracklet_id'])} has ratio "
                f"{cands[0]['measurements']['ratio']:.3f}, outside +/-{tolerance:.0%}"
            )
        else:
            taken = next(c for c in cands if c["score"] <= tolerance)["tracklet_id"]
            reason = (
                f"every candidate within +/-{tolerance:.0%} (best {short(taken)}) was already "
                f"accepted by a source with a closer ratio ({short(predecessor[taken])})"
            )
        out.append(record(tr.id, cands, succ, reason))
    return out
