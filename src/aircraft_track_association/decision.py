"""Per-tracklet decision records shared by every approach.

Each approach returns one record per tracklet, in tracklet (start time, id) order:

    {
      "tracklet_id": str,
      "status": "accepted" | "unresolved",
      "successor": str | None,       # accepted successor, None when unresolved
      "score": float | None,         # score of the accepted link
      "reason": str,                 # short human-readable justification
      "candidates": [                # best candidates first, at most MAX_CANDIDATES
        {"tracklet_id": str, "score": float, "measurements": {name: float}}
      ],
    }

Each approach documents its own scale. Baseline and Gaussian scores are costs: lower is
better, and 0 is a perfect fit. The learned score is a predicted chance that the candidate
is the immediate next tracklet: higher is better. None of the scores are calibrated.
"""

MAX_CANDIDATES = 5


def short(tracklet_id: str | None) -> str:
    """First UUID block, enough to tell tracklets apart in a sentence."""
    return tracklet_id.removeprefix("tracklet-")[:8] if tracklet_id else "-"


def candidate(tracklet_id: str, score: float, measurements: dict) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "score": float(score),
        "measurements": {k: float(v) for k, v in measurements.items()},
    }


def record(tracklet_id, candidates, successor, reason) -> dict:
    """Build one decision record; candidates must already be sorted best first."""
    score = next((c["score"] for c in candidates if c["tracklet_id"] == successor), None)
    return {
        "tracklet_id": tracklet_id,
        "status": "accepted" if successor else "unresolved",
        "successor": successor,
        "score": score,
        "reason": reason,
        "candidates": candidates[:MAX_CANDIDATES],
    }


def links(records: list[dict]) -> dict[str, str]:
    """{source id: successor id} for accepted records, the form evaluation expects."""
    return {r["tracklet_id"]: r["successor"] for r in records if r["successor"]}
