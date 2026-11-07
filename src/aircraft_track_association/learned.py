"""Learned tracklet association.

The learned approach was produced by an AI coding agent as a port of an AI-built
reference solution. It was not designed by the author.

A HistGradientBoostingClassifier scores each candidate pair with the predicted
chance that the candidate is the immediate next tracklet. It is fit on train
candidate pairs only. Greedy best-first selection then keeps a link when the
score and its margin over the runner-up both clear thresholds chosen on
validation. The score is not a calibrated probability, and higher is better.

Candidates are the same pairs the other approaches consider: the target starts
strictly after the source ends. Field conventions are derived from train only
and stored with the tree weights.
"""

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from .decision import candidate, links, record, short
from .evaluate import link_metrics
from .io import (
    check_utc,
    dedupe,
    dedupe_supporting,
    is_number,
    load_successors,
    parse_time,
    read_jsonl,
    supporting_altitude,
)
from .track import FT_TO_M, build_tracklets, projector

MODEL_VERSION = "learned-1.0.0"
ARTIFACT_NAME = "artifacts/learned.joblib"
SEED = 42
FEATURE_SET = "kinematic_altitude_reported"
# Declared columns. A column that does not vary on train pairs is dropped before the fit
# and omitted from the saved feature list. altitude_missing_count is the usual drop:
# preparation always leaves a tracklet altitude.
FEATURES = [
    "gap_s",
    "straight_km",
    "miss_km",
    "along_miss_km",
    "cross_miss_km",
    "miss_over_allowance",
    "speed_a_mps",
    "speed_b_mps",
    "speed_abs_diff_mps",
    "heading_delta_deg",
    "straight_speed_mps",
    "n_earlier_candidates",
    "n_earlier_plausible",
    "altitude_abs_diff_ft",
    "altitude_missing_count",
    "reported_speed_abs_diff_mps",
    "reported_heading_delta_deg",
    "reported_vs_kinematic_mps",
    "ref_match",
    "low_quality_fraction",
]
HYPERPARAMETERS = {
    "learning_rate": 0.08,
    "max_iter": 200,
    "max_depth": 4,
    "min_samples_leaf": 20,
    "l2_regularization": 0.1,
}
PLAUSIBLE_FACTOR = 1.25
PRECISION_TARGET = 0.95
PERMISSIVE_TARGET = 0.85
CONSERVATIVE_TARGET = 0.98
SCORE_GRID = [round(float(v), 4) for v in np.linspace(0.05, 0.95, 19)]
MARGINS = [0.0, 0.02, 0.05, 0.08, 0.12, 0.2, 0.3]
ENDPOINT_WINDOW = 8
HEADING_SPEED_FLOOR_MPS = 5.0
# Outlier cut for the endpoint line, matching a 3 km / 0.2 km rule in metres.
OUTLIER_RESIDUAL_M = 3000.0
OUTLIER_MEDIAN_FLOOR_M = 200.0

_SPEED_TO_MPS = {"knots": 0.514444, "metres_per_second": 1.0, "kilometres_per_hour": 1.0 / 3.6}
# Source notes state feed-a speed is knots. A check that picks a different unit is rejected.
_DOCUMENTED_SPEED = {"feed-a": "knots"}
_SKIP_NUMERIC = {"r7"}
_SKIP_STRING = {"node", "q", "b4"}
_REF_PATTERN = re.compile(r"^[0-9A-F]{6}$")
_METRES_TO_FEET = 1.0 / FT_TO_M

_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_PATH = _ROOT / ARTIFACT_NAME
HEATMAP_PATH = _ROOT / "results" / "feature_correlation.png"
_cached = None


@dataclass
class Summary:
    tracklet_id: str
    start_s: float
    end_s: float
    obs_start_xy: np.ndarray
    obs_end_xy: np.ndarray
    fit_start_xy: np.ndarray
    fit_end_xy: np.ndarray
    vel_start: np.ndarray
    vel_end: np.ndarray
    known_start: bool
    known_end: bool
    speed_start: float
    speed_end: float
    heading_start: float
    heading_end: float
    altitude_ft: float
    reported_speed: float
    reported_heading: float
    reference: str | None
    low_quality_fraction: float


def model(path: Path | None = None) -> dict:
    """Load the committed artifact. The default path is cached for the service process."""
    global _cached
    path = ARTIFACT_PATH if path is None else Path(path)
    if path == ARTIFACT_PATH and _cached is not None:
        return _cached
    if not path.is_file():
        raise FileNotFoundError(
            f"Learned model not found at {path}. Fit it with the train command."
        )
    loaded = joblib.load(path)
    if path == ARTIFACT_PATH:
        _cached = loaded
    return loaded


def save_model(spec: dict, path: Path | None = None) -> Path:
    path = ARTIFACT_PATH if path is None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(spec, path)
    sidecar = {key: value for key, value in spec.items() if key != "estimator"}
    path.with_suffix(".json").write_text(json.dumps(_jsonable(sidecar), indent=2) + "\n")
    global _cached
    if path.resolve() == ARTIFACT_PATH.resolve():
        _cached = spec
    return path


def decide(tracklets, spec: dict | None = None) -> list[dict]:
    """Score candidates and select links. `spec` defaults to the committed artifact."""
    spec = model() if spec is None else spec
    if not tracklets:
        return []
    summaries = summarize(tracklets, spec["conventions"])
    measurements = candidate_measurements(
        summaries, spec["allowance"], spec["plausible_allowance_factor"]
    )
    names = spec["feature_names"]
    score_of = _score(measurements, spec["estimator"], names)
    operating = spec["decision"]["operating"]
    choices = select(
        list(summaries),
        score_of,
        min_score=float(operating["min_score"]),
        min_margin=float(operating["min_margin"]),
        mode=operating["mode"],
    )
    by_src = defaultdict(list)
    for item in measurements:
        key = (item["predecessor_id"], item["successor_id"])
        measurements_out = {
            name: float(item[name]) for name in names if name in item and _finite(item[name])
        }
        by_src[item["predecessor_id"]].append(
            candidate(item["successor_id"], score_of[key], measurements_out)
        )
    out = []
    for tr in tracklets:
        ranked = sorted(by_src.get(tr.id, []), key=lambda row: (-row["score"], row["tracklet_id"]))
        choice = choices[tr.id]
        out.append(record(tr.id, ranked, choice["successor"], _reason(choice, operating)))
    return out


def train(train_dirs, validation_dirs, output: Path | None = None) -> dict:
    """Fit on train, choose the operating point on validation, and save the artifact.

    Does not read the test partition.
    """
    train_dirs = _sorted(train_dirs)
    validation_dirs = _sorted(validation_dirs)
    _refuse_test([*train_dirs, *validation_dirs])
    print("Deriving field conventions from train. Test is not read.", flush=True)
    conventions = derive_conventions(train_dirs)
    print(json.dumps(_jsonable(conventions), indent=2), flush=True)
    print("Building train and validation candidates.", flush=True)
    train_bundles = _bundles(train_dirs, conventions)
    validation_bundles = _bundles(validation_dirs, conventions)
    gaps, misses = _true_misses(train_bundles)
    if not gaps:
        raise SystemExit("No positive-gap true train links were available to fit the allowance.")
    allowance = fit_allowance(gaps, misses)
    for bundle in [*train_bundles, *validation_bundles]:
        bundle["measurements"] = candidate_measurements(
            bundle["summaries"], allowance, PLAUSIBLE_FACTOR
        )
    train_recall = _candidate_recall(train_bundles)
    validation_recall = _candidate_recall(validation_bundles)
    print(f"Candidate recall train={train_recall} validation={validation_recall}", flush=True)
    if train_recall["recall"] is None or train_recall["recall"] < 0.98:
        raise SystemExit("Train candidate recall is below 0.98. Test was not read.")

    rows, labels = _training_rows(train_bundles)
    declared = list(FEATURES)
    kept = columns_with_signal(feature_matrix(rows, declared), declared)
    dropped = [name for name in declared if name not in kept]
    if not kept:
        raise SystemExit("No training feature varies. Test was not read.")
    print(f"Training pairs={len(rows)} positives={sum(labels)} dropped={dropped}", flush=True)
    estimator = fit_estimator(feature_matrix(rows, kept), labels)
    for bundle in validation_bundles:
        bundle["scores"] = _score(bundle["measurements"], estimator, kept)
    print("Choosing thresholds on validation.", flush=True)
    grid = []
    for mode, margins in (("greedy", MARGINS), ("mutual", [0.0])):
        for min_score in SCORE_GRID:
            for min_margin in margins:
                grid.append(_evaluate_setting(validation_bundles, mode, min_score, min_margin))
    operating, operating_met = _pick(grid, "greedy", None, PRECISION_TARGET)
    permissive, permissive_met = _pick(grid, "greedy", 0.0, PERMISSIVE_TARGET)
    conservative, conservative_met = _pick(grid, "mutual", 0.0, CONSERVATIVE_TARGET)
    for label, row, met in (
        ("operating", operating, operating_met),
        ("permissive", permissive, permissive_met),
        ("conservative", conservative, conservative_met),
    ):
        print(f"{label:12} floor_met={met} {_format_row(row)}", flush=True)
    spec = {
        "estimator": estimator,
        "model_version": MODEL_VERSION,
        "feature_set": FEATURE_SET,
        "feature_names": kept,
        "dropped_features": dropped,
        "seed": SEED,
        "hyperparameters": HYPERPARAMETERS,
        "conventions": conventions,
        "allowance": allowance,
        "plausible_allowance_factor": PLAUSIBLE_FACTOR,
        "train_pairs": len(rows),
        "train_positives": int(sum(labels)),
        "train_candidate_recall": train_recall,
        "validation_candidate_recall": validation_recall,
        "decision": {
            "operating": _setting(operating),
            "permissive": _setting(permissive),
            "conservative": _setting(conservative),
            "precision_target_met": {
                "operating": operating_met,
                "permissive": permissive_met,
                "conservative": conservative_met,
            },
            "validation_metrics": {
                "operating": _metrics_only(operating),
                "permissive": _metrics_only(permissive),
                "conservative": _metrics_only(conservative),
            },
            "rule": (
                "Operating point maximises validation recall among greedy settings "
                "with precision >= 0.95. Ties prefer a larger margin, then a higher "
                "minimum score. Permissive is greedy with margin 0 and precision >= 0.85. "
                "Conservative is mutual-best with precision >= 0.98."
            ),
        },
        "protocol": (
            "Fit on train candidate pairs. Thresholds chosen on validation. "
            "Test labels were not read by this command."
        ),
    }
    path = save_model(spec, output)
    # Reload and check one validation scenario so a bad dump fails before the file is trusted.
    reloaded = joblib.load(path)
    check = validation_bundles[0]
    again = _score(check["measurements"], reloaded["estimator"], reloaded["feature_names"])
    if again != check["scores"]:
        raise SystemExit(f"Reloaded model scores differ from the fit. Left {path} for inspection.")
    print(f"Saved {path}", flush=True)
    return spec


def correlate(train_dirs, output: Path | None = None) -> Path:
    """Pearson correlation of the saved training features over train candidate pairs."""
    train_dirs = _sorted(train_dirs)
    _refuse_test(train_dirs)
    spec = model()
    rows = []
    for scenario_dir in train_dirs:
        tracklets, _labels, _obs, _sup = prepare(scenario_dir)
        summaries = summarize(tracklets, spec["conventions"])
        measurements = candidate_measurements(
            summaries, spec["allowance"], spec["plausible_allowance_factor"]
        )
        names = spec["feature_names"]
        for item in measurements:
            rows.append([item.get(name, math.nan) for name in names])
    if len(rows) < 3:
        raise SystemExit("Not enough train candidate pairs to correlate.")
    matrix = np.asarray(rows, dtype=float)
    path = HEATMAP_PATH if output is None else Path(output)
    _write_heatmap(matrix, spec["feature_names"], path)
    print(f"Wrote {path} from {len(rows)} train candidate pairs", flush=True)
    return path


def stress(scenario_dirs) -> dict:
    """Re-score the saved model after dropping every second report. Does not retrain."""
    spec = model()
    full_pred, full_truth = {}, {}
    thin_pred, thin_truth = {}, {}
    before = after = 0
    for scenario_dir in _sorted(scenario_dirs):
        tracklets, labels, obs, sup = prepare(scenario_dir)
        prefix = scenario_dir.name
        _accumulate(full_pred, full_truth, prefix, decide(tracklets, spec), labels)
        thinned = thin_observations(obs)
        kept = {row["observation_id"] for row in thinned}
        thin_tracklets = build_tracklets(
            thinned, supporting_altitude([row for row in sup if row["observation_id"] in kept])
        )
        _accumulate(thin_pred, thin_truth, prefix, decide(thin_tracklets, spec), labels)
        before += len(obs)
        after += len(thinned)
    return {
        "retrained": False,
        "observations": {"before": before, "after": after},
        "full": _pack(link_metrics(full_pred, full_truth)),
        "thinned": _pack(link_metrics(thin_pred, thin_truth)),
    }


def thin_observations(observations: list[dict]) -> list[dict]:
    """Keep every second report in each tracklet, and at least two when the tracklet has two."""
    groups = defaultdict(list)
    for row in observations:
        groups[row["tracklet_id"]].append(row)
    kept = []
    for rows in groups.values():
        rows = sorted(rows, key=lambda row: (row["observed_at"], row["observation_id"]))
        unique = []
        seen = set()
        for row in rows:
            if row["observation_id"] in seen:
                continue
            seen.add(row["observation_id"])
            unique.append(row)
        chosen = unique[::2]
        if len(chosen) < 2 and len(unique) >= 2:
            chosen = [unique[0], unique[-1]]
        if len(unique) >= 2 and len(chosen) < 2:
            raise RuntimeError("thinning left a tracklet with fewer than two reports")
        kept.extend(chosen)
    return kept


def derive_conventions(train_dirs) -> dict:
    """Speed units, heading fields, reference codes and low-quality codes from train only."""
    segments_by_source = defaultdict(list)
    string_values = defaultdict(lambda: defaultdict(list))
    scenarios = []
    for scenario_dir in _sorted(train_dirs):
        obs, sup = _scenario_records(scenario_dir)
        scenarios.append((obs, sup))
        for segment in _segments(obs):
            segments_by_source[segment["source"]].append(segment)
        for row in obs:
            attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
            for key, value in attrs.items():
                if isinstance(value, str):
                    string_values[row["source"]][key].append(value)

    speed_units, heading_fields, low_quality = _speed_and_heading(segments_by_source)
    reference_fields, ignored = _references(scenarios, string_values)
    altitude_field = _telemetry_altitude(scenarios)
    return {
        "speed_units": speed_units,
        "heading_fields": heading_fields,
        "reference_fields": reference_fields,
        "ignored_references": ignored,
        "low_quality_values": low_quality,
        "telemetry_altitude_field": altitude_field,
        "telemetry_metres_to_feet": _METRES_TO_FEET if altitude_field else None,
        "ignored_rule": (
            "A reference value is ignored when it accounts for at least 5% of "
            "reference-bearing train reports."
        ),
    }


def _speed_and_heading(segments_by_source):
    speed_units = {}
    heading_fields = {}
    errors_by_quality = defaultdict(lambda: defaultdict(list))
    for source, segments in sorted(segments_by_source.items()):
        numeric_keys = sorted(
            {
                key
                for segment in segments
                for key, value in segment["attributes"].items()
                if is_number(value) and key not in _SKIP_NUMERIC
            }
        )
        accepted_by_field = defaultdict(list)
        for key in numeric_keys:
            pairs = [
                (float(segment["attributes"][key]), segment["derived_mps"])
                for segment in segments
                if is_number(segment["attributes"].get(key))
            ]
            for unit, scale in _SPEED_TO_MPS.items():
                summary = _ratio_summary(pairs, scale)
                if _accept_speed(summary):
                    accepted_by_field[key].append(
                        {"field": key, "unit": unit, "to_mps": scale, "summary": summary}
                    )
        usable = [group[0] for group in accepted_by_field.values() if len(group) == 1]
        best = (
            min(usable, key=lambda trial: abs(trial["summary"]["median"] - 1.0)) if usable else None
        )
        if best is not None:
            documented = _DOCUMENTED_SPEED.get(source)
            if documented and documented != best["unit"]:
                status = "conflict_excluded"
            elif documented == best["unit"]:
                status = "documented_and_confirmed"
            else:
                status = "inferred"
            if status != "conflict_excluded":
                speed_units[source] = {
                    "field": best["field"],
                    "unit": best["unit"],
                    "to_mps": best["to_mps"],
                    "status": status,
                }

        best_heading = None
        for key in numeric_keys:
            reported, derived, in_range, total = [], [], 0, 0
            for segment in segments:
                value = segment["attributes"].get(key)
                if not is_number(value):
                    continue
                total += 1
                if 0.0 <= float(value) <= 360.0:
                    in_range += 1
                reported.append(float(value))
                derived.append(segment["derived_heading"])
            errors = _heading_errors(reported, derived)
            if not errors or not total or in_range / total < 0.95:
                continue
            summary = {
                "n": len(errors),
                "median": _pct(errors, 50),
                "p90": _pct(errors, 90),
            }
            if (
                summary["median"] <= 8.0
                and summary["p90"] <= 25.0
                and (best_heading is None or summary["median"] < best_heading[0])
            ):
                best_heading = (summary["median"], key)
        if best_heading is not None:
            heading_fields[source] = best_heading[1]
            for segment in segments:
                value = segment["attributes"].get(best_heading[1])
                if (
                    is_number(value)
                    and segment["derived_heading"] is not None
                    and segment["quality"] is not None
                ):
                    error = _heading_errors([float(value)], [segment["derived_heading"]])
                    if error:
                        errors_by_quality[source][str(segment["quality"])].append(error[0])

    low_quality = {}
    for source, groups in sorted(errors_by_quality.items()):
        usable = []
        for quality, errors in groups.items():
            if len(errors) >= 30:
                usable.append((quality, _pct(errors, 50)))
        if len(usable) < 2:
            continue
        worst = max(usable, key=lambda item: item[1])
        typical = float(np.median([item[1] for item in usable if item[0] != worst[0]]))
        bad = [
            quality
            for quality, median in usable
            if median >= 15.0 and median >= 3.0 * max(typical, 1.0)
        ]
        if bad:
            low_quality[source] = sorted(bad)
    return speed_units, heading_fields, low_quality


def _references(scenarios, string_values):
    reference_fields = {}
    for source, keys in sorted(string_values.items()):
        best_key, best_count = None, 0
        for key, values in keys.items():
            if key in _SKIP_STRING:
                continue
            matches = sum(1 for value in values if _REF_PATTERN.fullmatch(value))
            if matches > best_count:
                best_key, best_count = key, matches
        if best_key and best_count >= 100:
            reference_fields[source] = best_key

    ref_counter = Counter()
    per_tracklet = []
    for obs, _sup in scenarios:
        groups = defaultdict(list)
        for row in obs:
            groups[row["tracklet_id"]].append(row)
        for records in groups.values():
            values = []
            for row in records:
                field_name = reference_fields.get(row["source"])
                attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
                value = attrs.get(field_name) if field_name else None
                if isinstance(value, str) and value.strip():
                    values.append(value)
                    ref_counter[value] += 1
            per_tracklet.append(values)
    total = sum(ref_counter.values()) or 1
    ignored = sorted(value for value, count in ref_counter.items() if count / total >= 0.05)
    ignored_set = set(ignored)
    stable = unstable = 0
    for values in per_tracklet:
        useful = [value for value in values if value not in ignored_set]
        if len(useful) < 2:
            continue
        if len(set(useful)) == 1:
            stable += 1
        else:
            unstable += 1
    if stable + unstable and unstable / (stable + unstable) > 0.05:
        return {}, []
    return reference_fields, ignored


def _telemetry_altitude(scenarios):
    pairs = defaultdict(list)
    for obs, sup in scenarios:
        by_id = {row["observation_id"]: row for row in obs}
        for row in sup:
            observation = by_id.get(row["observation_id"])
            if observation is None or not is_number(observation.get("altitude_ft")):
                continue
            feet = float(observation["altitude_ft"])
            for key, value in row["attributes"].items():
                if is_number(value) and float(value) != 0.0:
                    pairs[key].append((float(value), feet))
    chosen, best_n = None, 0
    for key, rows in pairs.items():
        if len(rows) < 500:
            continue
        ratios = [feet / (metres * _METRES_TO_FEET) for metres, feet in rows]
        median, p10, p90 = _pct(ratios, 50), _pct(ratios, 10), _pct(ratios, 90)
        if 0.98 <= median <= 1.02 and p10 >= 0.95 and p90 <= 1.05 and len(rows) > best_n:
            chosen, best_n = key, len(rows)
    return chosen


def _segments(observations: list[dict]) -> list[dict]:
    if not observations:
        return []
    to_m = projector(observations)
    groups = defaultdict(list)
    for row in observations:
        groups[row["tracklet_id"]].append(row)
    segments = []
    for rows in groups.values():
        rows = sorted(
            rows, key=lambda item: (parse_time(item["observed_at"]), item["observation_id"])
        )
        xs, ys = to_m.transform(
            [item["longitude"] for item in rows], [item["latitude"] for item in rows]
        )
        times = [parse_time(item["observed_at"]) for item in rows]
        for index in range(1, len(rows)):
            dt = times[index] - times[index - 1]
            if dt <= 0:
                continue
            dx = float(xs[index] - xs[index - 1])
            dy = float(ys[index] - ys[index - 1])
            dist = math.hypot(dx, dy)
            attrs = rows[index].get("attributes")
            attrs = attrs if isinstance(attrs, dict) else {}
            segments.append(
                {
                    "source": rows[index]["source"],
                    "derived_mps": dist / dt,
                    "derived_heading": math.degrees(math.atan2(dx, dy)) % 360.0
                    if dist > 50.0
                    else None,
                    "attributes": attrs,
                    "quality": attrs.get("q"),
                }
            )
    return segments


def _ratio_summary(pairs, scale):
    ratios = [(reported * scale) / derived for reported, derived in pairs if derived > 40.0]
    if len(ratios) < 50:
        return None
    return {
        "n": len(ratios),
        "median": _pct(ratios, 50),
        "p10": _pct(ratios, 10),
        "p90": _pct(ratios, 90),
    }


def _accept_speed(summary) -> bool:
    # Wrong units land near 0.5 or 1.8. The right unit has a median ratio near 1.
    return bool(summary) and summary["n"] >= 50 and 0.95 <= summary["median"] <= 1.05


def _heading_errors(reported, derived):
    errors = []
    for left, right in zip(reported, derived):
        if right is None:
            continue
        delta = abs(left - right) % 360.0
        errors.append(min(delta, 360.0 - delta))
    return errors


def _pct(values, q):
    return float(np.percentile(np.asarray(values, dtype=float), q))


def summarize(tracklets, conventions: dict) -> dict[str, Summary]:
    return {tr.id: _summarize_one(tr, conventions) for tr in tracklets}


def _summarize_one(tr, conventions: dict) -> Summary:
    if tr.reports and len(tr.reports) != len(tr.t):
        raise ValueError(f"{tr.id}: reports are not aligned with the cleaned points")
    flags = [_is_low(report, conventions) for report in tr.reports] or [False] * len(tr.t)
    fit_end, vel_end, known_end = _endpoint(tr, "end", flags)
    fit_start, vel_start, known_start = _endpoint(tr, "start", flags)
    speed_end, heading_end = _speed_heading(vel_end, known_end)
    speed_start, heading_start = _speed_heading(vel_start, known_start)
    speeds, headings, refs = [], [], []
    for report in tr.reports:
        speed = _reported_speed(report, conventions)
        if speed is not None:
            speeds.append(speed)
        heading = _reported_heading(report, conventions)
        if heading is not None:
            headings.append(heading)
        ref = _reference_value(report, conventions)
        if ref is not None:
            refs.append(ref)
    reference = None
    if refs:
        counts = Counter(refs)
        best = max(counts.values())
        winners = sorted(value for value, count in counts.items() if count == best)
        if len(winners) == 1:
            reference = winners[0]
    fraction = (sum(flags) / len(flags)) if tr.reports else 0.0
    return Summary(
        tracklet_id=tr.id,
        start_s=float(tr.t[0]),
        end_s=float(tr.t[-1]),
        obs_start_xy=np.asarray(tr.p[0, :2], dtype=float),
        obs_end_xy=np.asarray(tr.p[-1, :2], dtype=float),
        fit_start_xy=fit_start,
        fit_end_xy=fit_end,
        vel_start=vel_start,
        vel_end=vel_end,
        known_start=known_start,
        known_end=known_end,
        speed_start=speed_start,
        speed_end=speed_end,
        heading_start=heading_start,
        heading_end=heading_end,
        altitude_ft=float(np.median(tr.p[:, 2])) / FT_TO_M,
        reported_speed=_median(speeds),
        reported_heading=_circular_mean(headings),
        reference=reference,
        low_quality_fraction=float(fraction),
    )


def _endpoint(tr, which: str, low: list[bool]):
    count = len(tr.t)
    window = (
        range(count - min(ENDPOINT_WINDOW, count), count)
        if which == "end"
        else range(min(ENDPOINT_WINDOW, count))
    )
    idx = [i for i in window if not low[i]]
    if len(idx) < 2:
        idx = list(window)
    times = np.asarray(tr.t[idx], dtype=float)
    xs = np.asarray(tr.p[idx, 0], dtype=float)
    ys = np.asarray(tr.p[idx, 1], dtype=float)

    def fit(mask):
        slope_x, intercept_x = _linreg(times[mask], xs[mask])
        slope_y, intercept_y = _linreg(times[mask], ys[mask])
        residual = np.hypot(
            xs[mask] - (intercept_x + slope_x * times[mask]),
            ys[mask] - (intercept_y + slope_y * times[mask]),
        )
        return slope_x, intercept_x, slope_y, intercept_y, residual

    mask = np.ones(len(idx), dtype=bool)
    slope_x, intercept_x, slope_y, intercept_y, residual = fit(mask)
    if len(idx) >= 4:
        worst = int(np.argmax(residual))
        median = float(np.median(residual))
        if residual[worst] > OUTLIER_RESIDUAL_M and residual[worst] > 3.0 * max(
            median, OUTLIER_MEDIAN_FLOOR_M
        ):
            mask = mask.copy()
            mask[worst] = False
            slope_x, intercept_x, slope_y, intercept_y, _residual = fit(mask)
    eval_t = float(tr.t[-1] if which == "end" else tr.t[0])
    position = np.array(
        [intercept_x + slope_x * eval_t, intercept_y + slope_y * eval_t], dtype=float
    )
    known = float(times[-1] - times[0]) > 1.0
    if not known:
        return position, np.zeros(2), False
    return position, np.array([slope_x, slope_y], dtype=float), True


def _linreg(times: np.ndarray, values: np.ndarray) -> tuple[float, float]:
    mean_t = float(times.mean())
    mean_v = float(values.mean())
    variance = float(np.sum((times - mean_t) ** 2))
    if variance < 1e-9:
        return 0.0, mean_v
    slope = float(np.sum((times - mean_t) * (values - mean_v)) / variance)
    return slope, mean_v - slope * mean_t


def _speed_heading(velocity: np.ndarray, known: bool) -> tuple[float, float]:
    if not known:
        return math.nan, math.nan
    speed = float(np.hypot(velocity[0], velocity[1]))
    if speed < HEADING_SPEED_FLOOR_MPS:
        return speed, math.nan
    return speed, math.degrees(math.atan2(velocity[0], velocity[1])) % 360.0


def _is_low(record: dict, conventions: dict) -> bool:
    bad = set(conventions.get("low_quality_values", {}).get(record["source"], []))
    if not bad:
        return False
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    quality = attrs.get("q")
    if quality is None:
        return False
    return str(quality) in bad


def _reported_speed(record: dict, conventions: dict):
    spec = conventions.get("speed_units", {}).get(record["source"])
    if not spec:
        return None
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    value = attrs.get(spec["field"])
    if not is_number(value):
        return None
    return float(value) * float(spec["to_mps"])


def _reported_heading(record: dict, conventions: dict):
    field_name = conventions.get("heading_fields", {}).get(record["source"])
    if not field_name:
        return None
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    value = attrs.get(field_name)
    if not is_number(value):
        return None
    return float(value) % 360.0


def _reference_value(record: dict, conventions: dict):
    field_name = conventions.get("reference_fields", {}).get(record["source"])
    if not field_name:
        return None
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    value = attrs.get(field_name)
    if not isinstance(value, str) or not value.strip():
        return None
    if value in set(conventions.get("ignored_references", [])):
        return None
    return value


def _median(values: list[float]) -> float:
    if not values:
        return math.nan
    return float(statistics.median(values))


def _circular_mean(degrees: list[float]) -> float:
    if not degrees:
        return math.nan
    east = sum(math.sin(math.radians(value)) for value in degrees)
    north = sum(math.cos(math.radians(value)) for value in degrees)
    if math.hypot(east, north) < 1e-9:
        return math.nan
    return math.degrees(math.atan2(east, north)) % 360.0


def candidate_measurements(summaries: dict, allowance: dict, plausible_factor: float) -> list[dict]:
    grouped = defaultdict(list)
    for src_id, src in summaries.items():
        for dst_id, dst in summaries.items():
            if dst.start_s <= src.end_s:
                continue
            item = measure(src, dst, allowance, plausible_factor)
            item["predecessor_id"] = src_id
            item["successor_id"] = dst_id
            item["successor_start"] = dst.start_s
            grouped[src_id].append(item)
    measurements = []
    for src_id in summaries:
        rows = grouped.get(src_id, [])
        _earlier_counts(rows)
        measurements.extend(rows)
    return measurements


def measure(src: Summary, dst: Summary, allowance: dict, plausible_factor: float) -> dict:
    gap_s = float(dst.start_s - src.end_s)
    straight_km = float(np.hypot(*(dst.obs_start_xy - src.obs_end_xy))) / 1000.0
    along = cross = math.nan
    if src.known_end:
        predicted = src.fit_end_xy + src.vel_end * gap_s
        delta = dst.fit_start_xy - predicted
        miss_km = float(np.hypot(delta[0], delta[1])) / 1000.0
        speed = float(np.hypot(src.vel_end[0], src.vel_end[1]))
        if speed > 1e-3:
            east, north = src.vel_end[0] / speed, src.vel_end[1] / speed
            along = float(delta[0] * east + delta[1] * north) / 1000.0
            cross = float(delta[0] * -north + delta[1] * east) / 1000.0
    else:
        miss_km = straight_km
    allowance_km = max(0.5, float(allowance["floor_km"]) + float(allowance["per_s_km"]) * gap_s)
    speed_abs = (
        abs(src.speed_end - dst.speed_start)
        if _finite(src.speed_end) and _finite(dst.speed_start)
        else math.nan
    )
    heading_delta = (
        _angle_diff(src.heading_end, dst.heading_start)
        if _finite(src.heading_end) and _finite(dst.heading_start)
        else math.nan
    )
    altitude_delta = (
        abs(src.altitude_ft - dst.altitude_ft)
        if _finite(src.altitude_ft) and _finite(dst.altitude_ft)
        else math.nan
    )
    reported_speed_delta = (
        abs(src.reported_speed - dst.reported_speed)
        if _finite(src.reported_speed) and _finite(dst.reported_speed)
        else math.nan
    )
    reported_heading_delta = (
        _angle_diff(src.reported_heading, dst.reported_heading)
        if _finite(src.reported_heading) and _finite(dst.reported_heading)
        else math.nan
    )
    kinematic_gap = []
    if _finite(src.reported_speed) and _finite(src.speed_end):
        kinematic_gap.append(abs(src.reported_speed - src.speed_end))
    if _finite(dst.reported_speed) and _finite(dst.speed_start):
        kinematic_gap.append(abs(dst.reported_speed - dst.speed_start))
    reported_vs_kinematic = sum(kinematic_gap) / len(kinematic_gap) if kinematic_gap else math.nan
    if src.reference and dst.reference:
        ref_match = 1.0 if src.reference == dst.reference else 0.0
    else:
        ref_match = math.nan
    return {
        "gap_s": gap_s,
        "straight_km": straight_km,
        "miss_km": miss_km,
        "along_miss_km": along,
        "cross_miss_km": cross,
        "miss_over_allowance": miss_km / allowance_km,
        "speed_a_mps": src.speed_end,
        "speed_b_mps": dst.speed_start,
        "speed_abs_diff_mps": speed_abs,
        "heading_delta_deg": heading_delta,
        "straight_speed_mps": straight_km * 1000.0 / gap_s if gap_s > 0 else math.nan,
        "n_earlier_candidates": 0.0,
        "n_earlier_plausible": 0.0,
        "altitude_abs_diff_ft": altitude_delta,
        "altitude_missing_count": float(not _finite(src.altitude_ft))
        + float(not _finite(dst.altitude_ft)),
        "reported_speed_abs_diff_mps": reported_speed_delta,
        "reported_heading_delta_deg": reported_heading_delta,
        "reported_vs_kinematic_mps": reported_vs_kinematic,
        "ref_match": ref_match,
        "low_quality_fraction": max(src.low_quality_fraction, dst.low_quality_fraction),
        "plausible": miss_km <= allowance_km * plausible_factor,
    }


def _earlier_counts(rows: list[dict]) -> None:
    order = sorted(
        range(len(rows)), key=lambda i: (rows[i]["successor_start"], rows[i]["successor_id"])
    )
    for position, index in enumerate(order):
        earlier = [rows[order[previous]] for previous in range(position)]
        rows[index]["n_earlier_candidates"] = float(len(earlier))
        rows[index]["n_earlier_plausible"] = float(sum(1 for item in earlier if item["plausible"]))


def _angle_diff(left: float, right: float) -> float:
    delta = abs(left - right) % 360.0
    return min(delta, 360.0 - delta)


def feature_matrix(items: list[dict], names: list[str]) -> np.ndarray:
    data = np.empty((len(items), len(names)), dtype=float)
    for i, item in enumerate(items):
        for j, name in enumerate(names):
            value = item.get(name, math.nan)
            data[i, j] = float(value) if _finite(value) else math.nan
    return data


def columns_with_signal(matrix: np.ndarray, names: list[str]) -> list[str]:
    kept = []
    for index, name in enumerate(names):
        finite = matrix[:, index][np.isfinite(matrix[:, index])]
        if finite.size < 2 or np.unique(np.round(finite, 5)).size < 2:
            continue
        kept.append(name)
    return kept


def fit_allowance(gaps: list[float], misses: list[float]) -> dict:
    gaps_a = np.asarray(gaps, dtype=float)
    misses_a = np.asarray(misses, dtype=float)
    per_second = float(np.percentile(misses_a / np.maximum(gaps_a, 1e-6), 75))
    residual = misses_a - per_second * gaps_a
    floor = max(1.0, float(np.percentile(residual, 75)))
    ratio = misses_a / np.maximum(floor + per_second * gaps_a, 1e-6)
    factor = max(1.0, float(np.percentile(ratio, 90)))
    return {
        "floor_km": float(floor * factor),
        "per_s_km": float(per_second * factor),
        "rule": "allowance_km = max(0.5, floor_km + per_s_km * gap_s), fit on train true links only",
    }


def fit_estimator(features: np.ndarray, labels: list[int]):
    y = np.asarray(labels, dtype=int)
    if len(np.unique(y)) < 2:
        raise RuntimeError("The learned model needs both positive and negative candidate pairs.")
    positives = max(int(y.sum()), 1)
    negatives = max(int(len(y) - y.sum()), 1)
    weights = np.where(y == 1, negatives / positives, 1.0).astype(float)
    estimator = HistGradientBoostingClassifier(
        loss="log_loss",
        learning_rate=HYPERPARAMETERS["learning_rate"],
        max_iter=HYPERPARAMETERS["max_iter"],
        max_depth=HYPERPARAMETERS["max_depth"],
        min_samples_leaf=HYPERPARAMETERS["min_samples_leaf"],
        l2_regularization=HYPERPARAMETERS["l2_regularization"],
        early_stopping=False,
        random_state=SEED,
    )
    estimator.fit(features, y, sample_weight=weights)
    return estimator


def _score(measurements: list[dict], estimator, names: list[str]) -> dict:
    if not measurements:
        return {}
    probs = estimator.predict_proba(feature_matrix(measurements, names))
    index = list(estimator.classes_).index(1)
    return {
        (item["predecessor_id"], item["successor_id"]): float(prob)
        for item, prob in zip(measurements, probs[:, index])
    }


def select(ids, score_of: dict, *, min_score: float, min_margin: float, mode: str) -> dict:
    """Greedy best-first, or mutual-best. Higher scores are better. Ids only break ties."""
    if mode not in {"greedy", "mutual"}:
        raise ValueError(f"Unknown selection mode {mode}.")
    ranked = {tracklet_id: [] for tracklet_id in ids}
    for (src, dst), score in score_of.items():
        if src in ranked:
            ranked[src].append((float(score), dst))
    for rows in ranked.values():
        rows.sort(key=lambda item: (-item[0], item[1]))
    if mode == "mutual":
        return _select_mutual(ranked, min_score)
    return _select_greedy(ranked, min_score, min_margin)


def _select_greedy(ranked: dict, min_score: float, min_margin: float) -> dict:
    successor_of, predecessor_of = {}, {}
    blocked, skipped, hints, accepted = set(), set(), {}, {}
    ordered = [(score, pred, succ) for pred, rows in ranked.items() for score, succ in rows]
    ordered.sort(key=lambda item: (-item[0], item[1], item[2]))
    for score, pred, succ in ordered:
        if pred in successor_of or pred in blocked or succ in predecessor_of:
            continue
        if (pred, succ) in skipped:
            continue
        remaining = [
            (cand_score, cand)
            for cand_score, cand in ranked[pred]
            if cand not in predecessor_of and (pred, cand) not in skipped
        ]
        if not remaining or remaining[0][1] != succ:
            continue
        if score < min_score:
            blocked.add(pred)
            hints[pred] = ("below_threshold", succ, score, None)
            continue
        runner = remaining[1] if len(remaining) > 1 else None
        if runner is not None and score - runner[0] < min_margin:
            blocked.add(pred)
            hints[pred] = ("insufficient_margin", succ, score, runner)
            continue
        if _cycle(successor_of, pred, succ):
            skipped.add((pred, succ))
            hints[pred] = ("cycle", succ, score, None)
            continue
        successor_of[pred] = succ
        predecessor_of[succ] = pred
        accepted[pred] = (succ, score, runner)

    decisions = {}
    for pred, rows in ranked.items():
        if pred in accepted:
            succ, score, runner = accepted[pred]
            decisions[pred] = _choice(succ, "accepted", succ, score, runner)
            continue
        if not rows:
            decisions[pred] = _choice(None, "no_candidate", None, None, None)
            continue
        if pred in blocked and pred in hints:
            code, focus, score, runner = hints[pred]
            decisions[pred] = _choice(None, code, focus, score, runner)
            continue
        free = [
            cand
            for _score, cand in rows
            if cand not in predecessor_of and (pred, cand) not in skipped
        ]
        best_score, best_id = rows[0]
        if not free:
            code = "cycle" if any((pred, cand) in skipped for _score, cand in rows) else "competing"
        else:
            code = "competing"
        decisions[pred] = _choice(
            None, code, best_id, best_score, None, taken_by=predecessor_of.get(best_id)
        )
    return decisions


def _select_mutual(ranked: dict, min_score: float) -> dict:
    best_successor = {}
    for pred, rows in ranked.items():
        if rows and rows[0][0] >= min_score:
            best_successor[pred] = rows[0]
    best_predecessor = {}
    for pred in sorted(ranked):
        for score, succ in ranked[pred]:
            if score < min_score:
                continue
            current = best_predecessor.get(succ)
            if current is None or score > current[0] or (score == current[0] and pred < current[1]):
                best_predecessor[succ] = (score, pred)
    chosen = {}
    for pred, (score, succ) in best_successor.items():
        if best_predecessor.get(succ, (None, None))[1] == pred:
            chosen[pred] = (score, succ)
    successor_of, predecessor_of = {}, {}
    order = sorted(chosen.items(), key=lambda item: (-item[1][0], item[0], item[1][1]))
    for pred, (score, succ) in order:
        if pred in successor_of or succ in predecessor_of:
            continue
        if _cycle(successor_of, pred, succ):
            continue
        successor_of[pred] = succ
        predecessor_of[succ] = pred
    decisions = {}
    for pred, rows in ranked.items():
        if pred in successor_of:
            succ = successor_of[pred]
            score = next(value for value, cand in rows if cand == succ)
            runner = next(((value, cand) for value, cand in rows if cand != succ), None)
            decisions[pred] = _choice(succ, "accepted", succ, score, runner)
            continue
        if not rows:
            decisions[pred] = _choice(None, "no_candidate", None, None, None)
            continue
        best_score, best_id = rows[0]
        code = "below_threshold" if best_score < min_score else "not_mutual"
        decisions[pred] = _choice(None, code, best_id, best_score, None)
    return decisions


def _choice(successor, code, focus, score, runner, taken_by=None) -> dict:
    runner_score = runner_id = None
    if runner is not None:
        runner_score, runner_id = runner
    return {
        "successor": successor,
        "code": code,
        "focus": focus,
        "score": None if score is None else float(score),
        "runner_id": runner_id,
        "runner_score": None if runner_score is None else float(runner_score),
        "taken_by": taken_by,
    }


def _cycle(successor_of: dict, src: str, dst: str) -> bool:
    seen = set()
    current = dst
    while current in successor_of:
        current = successor_of[current]
        if current == src or current in seen:
            return True
        seen.add(current)
    return src == dst


def _reason(choice: dict, operating: dict) -> str:
    code = choice["code"]
    focus = short(choice["focus"])
    if code == "accepted":
        succ = short(choice["successor"])
        if choice["runner_id"] is None:
            return f"predicted chance {choice['score']:.3f} that {succ} is the next tracklet"
        margin = choice["score"] - choice["runner_score"]
        return (
            f"predicted chance {choice['score']:.3f} that {succ} is the next tracklet, "
            f"{margin:.3f} above {short(choice['runner_id'])} ({choice['runner_score']:.3f})"
        )
    if code == "no_candidate":
        return "no tracklet starts after this one ends"
    if code == "below_threshold":
        return (
            f"best candidate {focus} scores {choice['score']:.3f}, "
            f"below the minimum {operating['min_score']:.2f}"
        )
    if code == "insufficient_margin":
        gap = choice["score"] - choice["runner_score"]
        return (
            f"best candidate {focus} scores {choice['score']:.3f}, only {gap:.3f} above "
            f"{short(choice['runner_id'])} ({choice['runner_score']:.3f}); the minimum margin "
            f"is {operating['min_margin']:.2f}, so this tracklet stays unresolved"
        )
    if code == "cycle":
        return f"linking to {focus} would form a cycle"
    if code == "not_mutual":
        return f"best candidate {focus} is not a mutual best, so this tracklet stays unresolved"
    if choice.get("taken_by"):
        return f"best candidate {focus} was already accepted by {short(choice['taken_by'])}"
    return f"best candidate {focus} was already taken by another tracklet"


def prepare(scenario_dir: Path):
    obs, sup = _scenario_records(scenario_dir)
    labels = load_successors(scenario_dir) if (scenario_dir / "labels.jsonl").exists() else {}
    return build_tracklets(obs, supporting_altitude(sup)), labels, obs, sup


def _scenario_records(scenario_dir: Path):
    meta = json.loads((scenario_dir / "snapshot.json").read_text())
    cutoff = meta.get("receipt_cutoff")
    obs = dedupe(read_jsonl(scenario_dir / "observations.jsonl"))
    sup = dedupe_supporting(read_jsonl(scenario_dir / "supporting.jsonl"))
    if cutoff:
        check_utc(scenario_dir.name, "receipt_cutoff", cutoff)
        limit = parse_time(cutoff)
        obs = [row for row in obs if parse_time(row["received_at"]) <= limit]
        sup = [row for row in sup if parse_time(row["received_at"]) <= limit]
    return obs, sup


def _bundles(dirs, conventions):
    bundles = []
    for scenario_dir in dirs:
        tracklets, labels, _obs, _sup = prepare(scenario_dir)
        bundles.append(
            {
                "name": scenario_dir.name,
                "summaries": summarize(tracklets, conventions),
                "labels": labels,
            }
        )
    return bundles


def _true_misses(bundles):
    dummy = {"floor_km": 1.0, "per_s_km": 0.0}
    gaps, misses = [], []
    for bundle in bundles:
        for src, dst in bundle["labels"].items():
            if not dst or src not in bundle["summaries"] or dst not in bundle["summaries"]:
                continue
            src_s, dst_s = bundle["summaries"][src], bundle["summaries"][dst]
            if dst_s.start_s <= src_s.end_s:
                continue
            item = measure(src_s, dst_s, dummy, PLAUSIBLE_FACTOR)
            gaps.append(item["gap_s"])
            misses.append(item["miss_km"])
    return gaps, misses


def _training_rows(bundles):
    rows, labels = [], []
    for bundle in bundles:
        items = sorted(
            bundle["measurements"],
            key=lambda item: (item["predecessor_id"], item["successor_id"]),
        )
        for item in items:
            rows.append(item)
            labels.append(
                1 if bundle["labels"].get(item["predecessor_id"]) == item["successor_id"] else 0
            )
    return rows, labels


def _candidate_recall(bundles) -> dict:
    labeled = kept = 0
    for bundle in bundles:
        pairs = {(item["predecessor_id"], item["successor_id"]) for item in bundle["measurements"]}
        for src, dst in bundle["labels"].items():
            if not dst:
                continue
            labeled += 1
            kept += (src, dst) in pairs
    return {
        "retained": kept,
        "labeled_links": labeled,
        "recall": (kept / labeled) if labeled else None,
    }


def _evaluate_setting(bundles, mode, min_score, min_margin) -> dict:
    pred, truth = {}, {}
    for bundle in bundles:
        choices = select(
            list(bundle["summaries"]),
            bundle["scores"],
            min_score=min_score,
            min_margin=min_margin,
            mode=mode,
        )
        prefix = bundle["name"]
        for src, choice in choices.items():
            if choice["successor"]:
                pred[f"{prefix}/{src}"] = f"{prefix}/{choice['successor']}"
        for src, dst in bundle["labels"].items():
            truth[f"{prefix}/{src}"] = f"{prefix}/{dst}" if dst else None
    packed = _pack(link_metrics(pred, truth))
    packed.update({"mode": mode, "min_score": min_score, "min_margin": min_margin})
    return packed


def _pick(rows, mode, margin, target):
    pool = [
        row
        for row in rows
        if row["mode"] == mode and (margin is None or row["min_margin"] == margin)
    ]

    def recall(row):
        return -1.0 if row["recall"] is None else row["recall"]

    def precision(row):
        return -1.0 if row["precision"] is None else row["precision"]

    met = [
        row
        for row in pool
        if row["precision"] is not None and row["precision"] + 1e-12 >= float(target)
    ]
    if met:
        best = max(
            met,
            key=lambda row: (
                round(recall(row), 6),
                round(precision(row), 6),
                row["min_margin"],
                row["min_score"],
            ),
        )
        return best, True
    best = max(
        pool,
        key=lambda row: (
            round(precision(row), 6),
            round(recall(row), 6),
            row["min_margin"],
            row["min_score"],
        ),
    )
    return best, False


def _setting(row) -> dict:
    return {"mode": row["mode"], "min_score": row["min_score"], "min_margin": row["min_margin"]}


def _metrics_only(row) -> dict:
    return {
        "precision": row["precision"],
        "recall": row["recall"],
        "correct": row["correct"],
        "wrong": row["wrong"],
        "unresolved": row["unresolved"],
        "accepted": row["accepted"],
        "true_links": row["true_links"],
    }


def _pack(metrics: dict) -> dict:
    return {
        "precision": metrics["precision"] if metrics["predicted_links"] else None,
        "recall": metrics["recall"] if metrics["true_links"] else None,
        "correct": metrics["correct_links"],
        "wrong": metrics["wrong_links"],
        "unresolved": metrics["unreported_links"],
        "accepted": metrics["predicted_links"],
        "true_links": metrics["true_links"],
    }


def _accumulate(pred, truth, prefix, records, labels) -> None:
    for src, dst in links(records).items():
        pred[f"{prefix}/{src}"] = f"{prefix}/{dst}"
    for src, dst in labels.items():
        truth[f"{prefix}/{src}"] = f"{prefix}/{dst}" if dst else None


def _format_row(row) -> str:
    precision = "n/a" if row["precision"] is None else f"{row['precision']:.3f}"
    recall = "n/a" if row["recall"] is None else f"{row['recall']:.3f}"
    return (
        f"{row['mode']:6} score>={row['min_score']:.2f} margin>={row['min_margin']:.2f} "
        f"P {precision} R {recall} correct {row['correct']} wrong {row['wrong']} "
        f"unresolved {row['unresolved']} accepted {row['accepted']}"
    )


def _write_heatmap(matrix: np.ndarray, names: list[str], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    size = len(names)
    corr = np.full((size, size), np.nan)
    for i in range(size):
        corr[i, i] = 1.0
        for j in range(i + 1, size):
            value = _corr(matrix[:, i], matrix[:, j])
            corr[i, j] = corr[j, i] = value
    fig, ax = plt.subplots(figsize=(12, 10))
    image = ax.imshow(corr, vmin=-1, vmax=1, cmap="coolwarm")
    ax.set_xticks(range(size), names, rotation=90, fontsize=8)
    ax.set_yticks(range(size), names, fontsize=8)
    for i in range(size):
        for j in range(size):
            if math.isfinite(corr[i, j]):
                ax.text(j, i, f"{corr[i, j]:.2f}", ha="center", va="center", fontsize=6)
    ax.set_title("Correlation of learned training features on train candidate pairs")
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120)
    plt.close(fig)


def _corr(left: np.ndarray, right: np.ndarray) -> float:
    mask = np.isfinite(left) & np.isfinite(right)
    if int(mask.sum()) < 3:
        return math.nan
    a, b = left[mask], right[mask]
    if float(np.std(a)) < 1e-12 or float(np.std(b)) < 1e-12:
        return math.nan
    return float(np.corrcoef(a, b)[0, 1])


def _sorted(dirs):
    return sorted((Path(path) for path in dirs), key=lambda path: path.name)


def _refuse_test(dirs) -> None:
    bad = [str(path) for path in dirs if path.parent.name == "test"]
    if bad:
        raise SystemExit("This command does not read the test partition: " + ", ".join(bad))


def _finite(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value
