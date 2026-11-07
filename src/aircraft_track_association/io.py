"""Read scenario files, validate observations and drop identical retransmissions.

This is the trust boundary: every observation is checked for required fields and valid
coordinates before it reaches modelling. Retransmissions reuse their observation_id, so
identical rows are collapsed to one, while two different rows sharing an ID are an error.
Labels are loaded separately and are only ever used for evaluation.
"""

import json
from datetime import datetime
from pathlib import Path

REQUIRED = (
    "observation_id",
    "tracklet_id",
    "observed_at",
    "received_at",
    "latitude",
    "longitude",
    "source",
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parse_time(value: str) -> float:
    """ISO 8601 UTC timestamp (with Z) -> POSIX seconds."""
    return datetime.fromisoformat(value).timestamp()


def validate(obs: dict) -> None:
    missing = [k for k in REQUIRED if obs.get(k) is None]
    if missing:
        raise ValueError(f"{obs.get('observation_id')}: missing required fields {missing}")
    if not -90 <= obs["latitude"] <= 90 or not -180 <= obs["longitude"] <= 180:
        raise ValueError(f"{obs['observation_id']}: coordinate out of range")


def dedupe(rows: list[dict]) -> list[dict]:
    """Keep one copy of identical retransmissions; reject different reports sharing an ID."""
    seen: dict[str, dict] = {}
    for obs in rows:
        validate(obs)
        prev = seen.setdefault(obs["observation_id"], obs)
        if prev != obs:
            raise ValueError(f"{obs['observation_id']}: conflicting reports share an ID")
    return list(seen.values())


def load_observations(scenario_dir: Path) -> list[dict]:
    return dedupe(read_jsonl(scenario_dir / "observations.jsonl"))


def load_successors(scenario_dir: Path) -> dict[str, str | None]:
    """Ground truth {tracklet id: successor id or None}. Evaluation only."""
    return {
        r["tracklet_id"]: r["successor_tracklet_id"]
        for r in read_jsonl(scenario_dir / "labels.jsonl")
    }
