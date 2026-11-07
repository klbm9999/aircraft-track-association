from pathlib import Path

import numpy as np
import pytest

from aircraft_track_association.associate import match, wasserstein2
from aircraft_track_association.evaluate import link_metrics
from aircraft_track_association.io import dedupe, read_jsonl
from aircraft_track_association.track import build_tracklet, build_tracklets

PACKET = Path(__file__).parents[1] / "candidate-packet"
FIXTURES = PACKET / "fixtures"


def test_identical_duplicate_kept_once():
    assert len(dedupe(read_jsonl(FIXTURES / "identical_duplicate.jsonl"))) == 1


@pytest.mark.parametrize("name", ["conflicting_observation_id", "invalid_coordinate"])
def test_bad_fixture_rejected(name):
    with pytest.raises(ValueError):
        dedupe(read_jsonl(FIXTURES / f"{name}.jsonl"))


def obs(i, tracklet, t, lat, lon, alt=10000.0):
    return {
        "observation_id": f"o{tracklet}{i}",
        "tracklet_id": tracklet,
        "observed_at": f"2026-01-01T00:{t // 60:02d}:{t % 60:02d}Z",
        "received_at": "2026-01-01T01:00:00Z",
        "latitude": lat,
        "longitude": lon,
        "source": "feed-a",
        "altitude_ft": alt,
    }


def test_projection_metric_and_altitude_mean_fill():
    # 0.01 deg of latitude is ~1110 m; second tracklet point lacks altitude
    rows = [
        obs(0, "a", 0, 35.0, -108.0),
        obs(1, "a", 10, 35.01, -108.0, alt=None),
        obs(2, "a", 20, 35.02, -108.0, alt=20000.0),
    ]
    (t,) = build_tracklets(rows)
    assert np.linalg.norm(t.p[1, :2] - t.p[0, :2]) == pytest.approx(1109, rel=0.01)
    assert t.p[1, 2] == pytest.approx(15000 * 0.3048)
    assert np.linalg.norm(t.vel_mean[:2]) == pytest.approx(110.9, rel=0.01)


def test_supporting_altitude_fills_gap_before_tracklet_mean():
    rows = [
        obs(0, "a", 0, 35.0, -108.0),
        obs(1, "a", 10, 35.01, -108.0, alt=None),
        obs(2, "a", 20, 35.02, -108.0, alt=None),
    ]
    (t,) = build_tracklets(rows, {"oa1": 5000.0})
    assert t.p[1, 2] == pytest.approx(5000.0)
    # oa2 has no supporting altitude: mean of the reported and supporting values
    assert t.p[2, 2] == pytest.approx((10000 * 0.3048 + 5000.0) / 2)


def test_near_duplicate_dropped():
    rows = [
        obs(0, "a", 0, 35.0, -108.0),
        obs(1, "a", 0, 35.0001, -108.0),
        obs(2, "a", 10, 35.01, -108.0),
    ]
    (t,) = build_tracklets(rows)
    assert len(t.t) == 2


def test_wasserstein_zero_for_identical_and_mean_shift():
    m, s = np.zeros(3), np.diag([1.0, 2.0, 3.0])
    assert wasserstein2(m, s, m, s) == pytest.approx(0, abs=1e-6)
    assert wasserstein2(m, s, m + [3, 4, 0], s) == pytest.approx(5)


def straight(tid, t0, x0, n=12, v=150.0):
    t = t0 + np.arange(n) * 10.0
    return build_tracklet(
        tid, t, np.column_stack([x0 + v * (t - t0), np.zeros(n), np.full(n, 4000.0)])
    )


def test_match_links_in_order_and_leaves_end_unmatched():
    # one aircraft flying east in three fragments with 100 s gaps
    a = straight("a", 0, 0)
    b = straight("b", 210, 150 * 210)
    c = straight("c", 420, 150 * 420)
    assert match([c, a, b]) == {"a": "b", "b": "c"}


def test_no_match_when_target_is_implausible():
    a = straight("a", 0, 0)
    far = build_tracklet(
        "far",
        210 + np.arange(12) * 10.0,
        np.column_stack([np.zeros(12), -150 * np.arange(12) * 10.0 - 5e5, np.full(12, 4000.0)]),
    )
    near = straight("b", 210, 150 * 210)
    assert match([a, far, near])["a"] == "b"
    assert match([a, far], no_match_cost=0.5) == {}


def test_link_metrics():
    m = link_metrics({"a": "b", "b": "x"}, {"a": "b", "b": "c", "c": None})
    assert (m["precision"], m["recall"], m["correct_no_successor"]) == (0.5, 0.5, 1)
    assert (m["wrong_links"], m["unreported_links"]) == (1, 0)
    m = link_metrics({"a": "b"}, {"a": "b", "b": "c", "c": None})
    assert (m["wrong_links"], m["unreported_links"]) == (0, 1)


def test_scenario_001_regression():
    from aircraft_track_association.io import load_successors
    from aircraft_track_association.track import load_tracklets

    d = PACKET / "data/train/scenario_001"
    m = link_metrics(match(load_tracklets(d)), load_successors(d))
    assert m["f1"] == 1.0
