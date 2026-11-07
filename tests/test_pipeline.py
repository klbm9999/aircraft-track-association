import random
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aircraft_track_association import baseline
from aircraft_track_association.decision import links
from aircraft_track_association.io import InputError, dedupe
from aircraft_track_association.pipeline import analyze, read_snapshot
from aircraft_track_association.service import app
from aircraft_track_association.track import build_tracklet

DATA = Path(__file__).parents[1] / "candidate-packet" / "data"
SCENARIO = DATA / "validation" / "scenario_001"


def good(**overrides):
    obs = {
        "observation_id": "o1",
        "tracklet_id": "t1",
        "observed_at": "2026-01-01T00:00:00Z",
        "received_at": "2026-01-01T00:00:05Z",
        "latitude": 35.0,
        "longitude": -108.0,
        "source": "feed-a",
        "altitude_ft": None,
    }
    return {**obs, **overrides}


@pytest.mark.parametrize(
    "bad",
    [
        {"tracklet_id": ""},
        {"observation_id": "  "},
        {"source": None},
        {"observed_at": "2026-01-01T00:00:00"},  # no timezone
        {"observed_at": "2026-01-01T02:00:00+02:00"},  # not UTC
        {"received_at": "yesterday"},
        {"latitude": "35.0"},
        {"longitude": 181},
        {"altitude_ft": "high"},
        {"attributes": [1, 2]},
    ],
)
def test_invalid_observation_rejected(bad):
    with pytest.raises(InputError):
        dedupe([good(**bad)])


def test_missing_altitude_and_explicit_utc_offset_accepted():
    assert dedupe([good(observed_at="2026-01-01T00:00:00+00:00")])[0]["altitude_ft"] is None


def test_missing_field_differs_from_explicit_null():
    a, b = good(), good()
    del b["altitude_ft"]
    with pytest.raises(InputError, match="conflicting"):
        dedupe([a, b])


@pytest.fixture(scope="module")
def snapshot():
    return read_snapshot(SCENARIO)


@pytest.fixture(scope="module")
def result(snapshot):
    return analyze(**snapshot)


def test_reordered_and_duplicated_input_gives_identical_result(snapshot, result):
    rows = snapshot["observations"] + snapshot["observations"][:40]
    sup = snapshot["supporting"] + snapshot["supporting"][:20]
    random.Random(7).shuffle(rows)
    random.Random(8).shuffle(sup)
    again = analyze(rows, sup, snapshot["snapshot_id"], snapshot["receipt_cutoff"])
    assert again["results"] == result["results"]
    assert again["input_sha256"] == result["input_sha256"]


@pytest.mark.parametrize("approach", ["baseline", "gaussian"])
def test_association_constraints(result, approach):
    start = {t["tracklet_id"]: t["start_s"] for t in result["tracklets"]}
    end = {t["tracklet_id"]: t["end_s"] for t in result["tracklets"]}
    linked = links(result["results"][approach])
    assert len(set(linked.values())) == len(linked)  # at most one predecessor
    for src, dst in linked.items():
        assert start[dst] > end[src]  # strictly after, which also rules out cycles
    for r in result["results"][approach]:
        assert (r["status"] == "accepted") == (r["successor"] is not None)
        assert r["reason"]
        scores = [c["score"] for c in r["candidates"]]
        assert scores == sorted(scores)


def test_receipt_cutoff_releases_only_earlier_records():
    s1 = read_snapshot(DATA / "staged_demo" / "snapshot_1")
    s2 = read_snapshot(DATA / "staged_demo" / "snapshot_2")
    # Snapshot 2's records cut at snapshot 1's cutoff are exactly what snapshot 1 saw.
    replay = analyze(s2["observations"], s2["supporting"], receipt_cutoff=s1["receipt_cutoff"])
    assert replay["input_sha256"] == analyze(**s1)["input_sha256"]
    with pytest.raises(InputError, match="cutoff"):
        analyze(s2["observations"], s2["supporting"], receipt_cutoff="2000-01-01T00:00:00Z")


def straight(tid, t0, x0, v=150.0, n=12):
    t = t0 + np.arange(n) * 10.0
    return build_tracklet(
        tid, t, np.column_stack([x0 + v * (t - t0), np.zeros(n), np.full(n, 4000.0)])
    )


def test_baseline_accepts_within_tolerance_only():
    a = straight("a", 0, 0)
    on_track = straight("b", 210, 150 * 210)  # displacement equals projection
    too_far = straight("c", 210, 150 * 210 * 1.2)
    recs = {r["tracklet_id"]: r for r in baseline.decide([a, too_far, on_track])}
    assert recs["a"]["successor"] == "b"
    assert recs["a"]["candidates"][0]["measurements"]["ratio"] == pytest.approx(1.0)
    recs = {r["tracklet_id"]: r for r in baseline.decide([a, too_far])}
    assert recs["a"]["status"] == "unresolved" and "outside" in recs["a"]["reason"]


client = TestClient(app)


def test_health():
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["approaches"] == ["baseline", "gaussian"]


def test_associate_endpoint_matches_pipeline(snapshot, result):
    body = client.post("/tracklets/associate", json=snapshot)
    assert body.status_code == 200
    assert body.json()["results"] == result["results"]


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"observations": [{"observation_id": "o1"}]}, 422, "invalid_input"),
        ({"observations": "nope"}, 422, "invalid_request"),
        ({"observations": []}, 422, "invalid_input"),
    ],
)
def test_associate_errors_are_json(payload, status, code):
    r = client.post("/tracklets/associate", json=payload)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_unknown_scenario_is_404():
    r = client.get("/scenarios/train/does_not_exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_tune_scoring_matches_evaluation():
    from aircraft_track_association.evaluate import link_metrics
    from aircraft_track_association.io import load_successors
    from aircraft_track_association.tune import prepare, score
    from aircraft_track_association.weights import WEIGHTS

    dirs = [DATA / "test" / "scenario_014", SCENARIO]
    s = score(prepare(dirs), WEIGHTS)
    wrong = unresolved = 0
    for d in dirs:
        m = link_metrics(
            links(analyze(**read_snapshot(d))["results"]["gaussian"]), load_successors(d)
        )
        wrong, unresolved = wrong + m["wrong_links"], unresolved + m["unreported_links"]
    assert (s["wrong"], s["unresolved"]) == (wrong, unresolved)


def test_thinning_keeps_every_tracklet_and_is_reproducible(snapshot):
    from collections import Counter

    from aircraft_track_association.stress import MIN_KEEP, thin

    obs, sup = thin(snapshot["observations"], snapshot["supporting"], 0.25, seed=0)
    before = Counter(o["tracklet_id"] for o in dedupe(snapshot["observations"]))
    after = Counter(o["tracklet_id"] for o in obs)
    assert set(after) == set(before)
    assert all(after[t] >= min(MIN_KEEP, before[t]) for t in before)
    assert sum(after.values()) < sum(before.values()) / 2
    assert {r["observation_id"] for r in sup} <= {o["observation_id"] for o in obs}
    rows = list(reversed(snapshot["observations"]))
    assert thin(rows, snapshot["supporting"], 0.25, seed=0)[0] == obs  # order-independent
