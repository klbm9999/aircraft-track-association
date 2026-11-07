"""Stress test: thin each tracklet's reports, then rerun association without retuning.

Thinning keeps a random fraction of each tracklet's unique reports (at least MIN_KEEP, so every
tracklet is still defined and gets a velocity), and drops supporting records whose observation
was removed. Tracklet identities and labels are unchanged, so the same evaluation applies.
The random choice is seeded per tracklet from the seed and the tracklet ID, so it does not
depend on file order and is reproducible.
"""

import hashlib
from collections import defaultdict

import numpy as np

from .io import dedupe

MIN_KEEP = 3


def thin(observations: list, supporting: list, keep: float, seed: int) -> tuple[list, list]:
    by = defaultdict(list)
    for o in dedupe(observations):
        by[o["tracklet_id"]].append(o)
    kept = []
    for tid in sorted(by):
        rows = by[tid]  # dedupe returns rows sorted by observation_id
        digest = hashlib.sha256(f"{seed}:{tid}".encode()).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
        n = min(len(rows), max(MIN_KEEP, round(keep * len(rows))))
        kept += [rows[i] for i in sorted(rng.choice(len(rows), n, replace=False))]
    ids = {o["observation_id"] for o in kept}
    return kept, [r for r in supporting if r.get("observation_id") in ids]
