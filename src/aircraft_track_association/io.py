"""Read scenario files, validate records and drop identical retransmissions.

This is the trust boundary for both the CLI and the HTTP service: every observation and
supporting record is checked before it reaches modelling, and any violation raises InputError
naming the offending record. Checks: required fields present, IDs non-empty strings, timestamps
ISO 8601 in UTC, coordinates numeric and in range, altitude numeric or null.

Retransmissions reuse their ID, so records whose parsed JSON is equal are collapsed to one,
while two different records sharing an ID are rejected. Output is sorted by ID so the result
does not depend on input order. Labels are loaded separately and only ever used for evaluation.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

ID_FIELDS = ("observation_id", "tracklet_id", "source")
SUPPORT_ID_FIELDS = ("record_id", "observation_id", "source")


class InputError(ValueError):
    """Input that violates the data contract; the message names the offending record."""


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parse_time(value: str) -> float:
    """ISO 8601 UTC timestamp (with Z) -> POSIX seconds."""
    return datetime.fromisoformat(value).timestamp()


def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_utc(rid: str, field: str, value) -> None:
    try:
        ts = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        ts = None
    if ts is None or ts.utcoffset() != timedelta(0):
        raise InputError(f"{rid}: {field} must be an ISO 8601 UTC timestamp, got {value!r}")


def check_ids(rec: dict, fields: tuple[str, ...], rid: str) -> None:
    bad = [k for k in fields if not isinstance(rec.get(k), str) or not rec[k].strip()]
    if bad:
        raise InputError(f"{rid}: missing or empty required fields {bad}")


def validate(obs: dict) -> None:
    if not isinstance(obs, dict):
        raise InputError(f"observation must be a JSON object, got {type(obs).__name__}")
    rid = str(obs.get("observation_id"))
    check_ids(obs, ID_FIELDS, rid)
    for field in ("observed_at", "received_at"):
        check_utc(rid, field, obs.get(field))
    lat, lon = obs.get("latitude"), obs.get("longitude")
    if not is_number(lat) or not is_number(lon):
        raise InputError(f"{rid}: latitude and longitude must be numbers")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise InputError(f"{rid}: coordinate out of range")
    if obs.get("altitude_ft") is not None and not is_number(obs["altitude_ft"]):
        raise InputError(f"{rid}: altitude_ft must be a number or null")
    if obs.get("attributes") is not None and not isinstance(obs["attributes"], dict):
        raise InputError(f"{rid}: attributes must be a JSON object")


def validate_supporting(rec: dict) -> None:
    if not isinstance(rec, dict):
        raise InputError(f"supporting record must be a JSON object, got {type(rec).__name__}")
    rid = str(rec.get("record_id"))
    check_ids(rec, SUPPORT_ID_FIELDS, rid)
    check_utc(rid, "received_at", rec.get("received_at"))
    if not isinstance(rec.get("attributes"), dict):
        raise InputError(f"{rid}: attributes must be a JSON object")


def dedupe_by(rows: list[dict], key: str, check) -> list[dict]:
    """Validate each record; keep one copy of identical retransmissions; reject conflicts.
    Dict equality ignores key order but distinguishes a missing field from an explicit null."""
    seen: dict[str, dict] = {}
    for rec in rows:
        check(rec)
        if seen.setdefault(rec[key], rec) != rec:
            raise InputError(f"{rec[key]}: conflicting records share an ID")
    return [seen[k] for k in sorted(seen)]


def dedupe(rows: list[dict]) -> list[dict]:
    return dedupe_by(rows, "observation_id", validate)


def dedupe_supporting(rows: list[dict]) -> list[dict]:
    return dedupe_by(rows, "record_id", validate_supporting)


def supporting_altitude(records: list[dict]) -> dict[str, float]:
    """{observation_id: altitude in metres} from deduplicated supporting records' `a1` field.

    `a1` is undocumented; it is read as altitude in metres because, on the 10,194 train
    observations that also report altitude_ft, it agrees with altitude_ft * 0.3048 with
    correlation 0.999, median ratio 1.000 and a largest difference of 162 m (normal noise).
    It is null in about 12% of records. The `b4` flag does not change its agreement.
    """
    alt: dict[str, float] = {}
    for rec in records:
        a1 = rec["attributes"].get("a1")
        if not is_number(a1):
            continue
        # Packaged data has one record per observation; refuse to pick between two silently.
        if alt.setdefault(rec["observation_id"], float(a1)) != float(a1):
            raise InputError(f"{rec['observation_id']}: conflicting supporting altitudes")
    return alt


def load_observations(scenario_dir: Path) -> list[dict]:
    return dedupe(read_jsonl(scenario_dir / "observations.jsonl"))


def load_supporting(scenario_dir: Path) -> list[dict]:
    return dedupe_supporting(read_jsonl(scenario_dir / "supporting.jsonl"))


def load_successors(scenario_dir: Path) -> dict[str, str | None]:
    """Ground truth {tracklet id: successor id or None}. Evaluation only."""
    return {
        r["tracklet_id"]: r["successor_tracklet_id"]
        for r in read_jsonl(scenario_dir / "labels.jsonl")
    }
