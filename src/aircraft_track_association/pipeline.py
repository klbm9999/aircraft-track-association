"""One analysis run: validate inputs, build tracklets, and run every approach on them.

Both the CLI and the HTTP service go through analyze(), so they produce identical results.
With a receipt cutoff, records received after it are not yet available and are left out
before anything else, so a snapshot only ever sees evidence released by its cutoff.
Each result identifies its input (a hash over the deduplicated, ID-sorted records, so
reordering or adding identical duplicates gives the same hash), the code version, and the
full decision configuration with its own hash.
"""

import hashlib
import json
from importlib.metadata import version
from pathlib import Path

from . import associate, baseline, learned, track, weights
from .io import (
    InputError,
    check_utc,
    dedupe,
    dedupe_supporting,
    parse_time,
    read_jsonl,
    supporting_altitude,
)

MODEL_VERSION = version("aircraft-track-association")
APPROACHES = {
    "baseline": baseline.decide,
    "gaussian": associate.decide,
    "learned": learned.decide,
}


def config() -> dict:
    """Every setting that affects a decision, so a saved result can be traced and reproduced."""
    return {
        "preprocessing": {
            "projection": "local azimuthal equidistant, centred on the scenario mean position",
            "altitude": "altitude_ft * 0.3048, else supporting a1 (metres), else tracklet mean",
            "near_duplicate_dt_s": track.DUP_DT_S,
            "near_duplicate_dist_m": track.DUP_DIST_M,
            "min_velocity_window_s": track.MIN_BASELINE_S,
            "endpoint_fit_points": track.ENDPOINT_POINTS,
        },
        "baseline": {"tolerance": baseline.TOLERANCE},
        "gaussian": {
            "weight_set": weights.WEIGHT_SET,
            "weights": weights.WEIGHTS,
            "scales": weights.SCALES,
            "no_match_cost": weights.NO_MATCH_COST,
        },
        "learned": _learned_config(),
    }


def _learned_config() -> dict:
    spec = learned.model()
    operating = spec["decision"]["operating"]
    return {
        "artifact": learned.ARTIFACT_NAME,
        "model_version": spec["model_version"],
        "feature_set": spec["feature_set"],
        "feature_names": spec["feature_names"],
        "min_score": operating["min_score"],
        "min_margin": operating["min_margin"],
        "mode": operating["mode"],
        "seed": spec["seed"],
    }


def digest(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def tracklet_summary(tracklets: list[track.Tracklet], observations: list[dict]) -> list[dict]:
    meta = {}
    for o in observations:
        meta.setdefault(o["tracklet_id"], {"source": o["source"], "observations": 0})
        meta[o["tracklet_id"]]["observations"] += 1
    return [
        {
            "tracklet_id": tr.id,
            "source": meta[tr.id]["source"],
            "observation_count": meta[tr.id]["observations"],
            "start_s": float(tr.t[0]),
            "end_s": float(tr.t[-1]),
        }
        for tr in tracklets
    ]


def available(records: list[dict], cutoff_s: float | None) -> list[dict]:
    if cutoff_s is None:
        return records
    return [r for r in records if parse_time(r["received_at"]) <= cutoff_s]


def analyze(
    observations: list,
    supporting: list,
    snapshot_id: str | None = None,
    receipt_cutoff: str | None = None,
) -> dict:
    """Run every approach on one snapshot. Raises io.InputError on invalid input."""
    if receipt_cutoff is not None:
        check_utc("request", "receipt_cutoff", receipt_cutoff)
    cutoff_s = parse_time(receipt_cutoff) if receipt_cutoff else None
    # Validate everything first, then drop what the cutoff has not released yet.
    obs = available(dedupe(observations), cutoff_s)
    sup = available(dedupe_supporting(supporting), cutoff_s)
    if not obs:
        where = " at the receipt cutoff" if cutoff_s is not None else ""
        raise InputError(f"no observations to analyze{where}")
    tracklets = track.build_tracklets(obs, supporting_altitude(sup))
    cfg = config()
    return {
        "snapshot_id": snapshot_id,
        "receipt_cutoff": receipt_cutoff,
        "input_sha256": digest({"observations": obs, "supporting": sup}),
        "model_version": MODEL_VERSION,
        "config_sha256": digest(cfg),
        "config": cfg,
        "counts": {
            "observation_rows": len(observations),
            "unique_observations": len(obs),
            "supporting_rows": len(supporting),
            "unique_supporting": len(sup),
            "tracklets": len(tracklets),
        },
        "tracklets": tracklet_summary(tracklets, obs),
        "results": {name: decide(tracklets) for name, decide in APPROACHES.items()},
    }


def read_snapshot(scenario_dir: Path) -> dict:
    """Raw inputs of a packaged scenario directory, as the service would receive them."""
    meta = json.loads((scenario_dir / "snapshot.json").read_text())
    return {
        "snapshot_id": meta["snapshot_id"],
        "receipt_cutoff": meta.get("receipt_cutoff"),
        "observations": read_jsonl(scenario_dir / "observations.jsonl"),
        "supporting": read_jsonl(scenario_dir / "supporting.jsonl"),
    }


def analyze_dir(scenario_dir: Path) -> dict:
    snap = read_snapshot(scenario_dir)
    return analyze(**snap)
