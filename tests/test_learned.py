from pathlib import Path

import pytest

from aircraft_track_association import learned
from aircraft_track_association.learned import select

DATA = Path(__file__).parents[1] / "candidate-packet" / "data"
SCENARIO = DATA / "validation" / "scenario_001"

BANNED_FEATURES = {
    "ratio",
    "plausibility",
    "vel_w2",
    "pos_w2",
    "alt_w2",
    "vel_cos",
    "heading",
    "dt",
}


def test_margin_does_not_fall_through_to_a_worse_candidate():
    scores = {("a", "b"): 0.95, ("a", "c"): 0.90, ("a", "e"): 0.85, ("d", "b"): 0.99}
    got = select(["a", "b", "c", "d", "e"], scores, min_score=0.5, min_margin=0.1, mode="greedy")
    assert got["d"]["successor"] == "b"
    assert got["a"]["successor"] is None
    assert got["a"]["code"] == "insufficient_margin"


def test_next_candidate_is_judged_with_a_fresh_margin():
    # b is taken by d. a's next candidate c has no runner-up, so the margin does not block it.
    scores = {("a", "b"): 0.95, ("a", "c"): 0.70, ("d", "b"): 0.99}
    got = select(["a", "b", "c", "d"], scores, min_score=0.5, min_margin=0.1, mode="greedy")
    assert got["d"]["successor"] == "b"
    assert got["a"]["successor"] == "c"
    successors = [got[src]["successor"] for src in got if got[src]["successor"]]
    assert len(successors) == len(set(successors))


def test_one_to_one_and_cycle_are_refused():
    scores = {("a", "b"): 0.9, ("b", "a"): 0.8}
    got = select(["a", "b"], scores, min_score=0.5, min_margin=0.0, mode="greedy")
    assert got["a"]["successor"] == "b"
    assert got["b"]["successor"] is None
    assert got["b"]["code"] == "cycle"


def test_below_minimum_score_stays_unresolved():
    got = select(["a", "b"], {("a", "b"): 0.04}, min_score=0.05, min_margin=0.0, mode="greedy")
    assert got["a"]["successor"] is None
    assert got["a"]["code"] == "below_threshold"


def test_saved_features_exclude_the_other_approaches():
    names = set(learned.model()["feature_names"])
    assert "ref_match" in names
    assert "miss_over_allowance" in names
    assert names.isdisjoint(BANNED_FEATURES)


def test_save_load_gives_identical_decisions(tmp_path):
    tracklets, _labels, _obs, _sup = learned.prepare(SCENARIO)
    original = learned.decide(tracklets)
    path = tmp_path / "learned.joblib"
    learned.save_model(learned.model(), path)
    reloaded = learned.model(path)
    assert learned.decide(tracklets, reloaded) == original


def test_train_refuses_the_test_partition():
    with pytest.raises(SystemExit, match="test"):
        learned.train(
            [DATA / "train" / "scenario_001"],
            [DATA / "test" / "scenario_001"],
        )
