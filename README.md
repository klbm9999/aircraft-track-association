# aircraft-track-association

Govia ML Engineer take-home: decide which disconnected aircraft movement fragments (tracklets) belong together.
For each tracklet the system either accepts one successor (the next fragment of the same aircraft) or leaves it unresolved, and explains why.

Two approaches are implemented and compared:

- **Baseline** - a constant-velocity projection rule with a fixed 5% tolerance.
- **Gaussian continuity model** - each tracklet is summarised as position, altitude and velocity Gaussians, candidate links are scored with seven continuity costs, and a global assignment picks a consistent set of links.

Neither approach learns from data: the baseline has one fixed setting, and the Gaussian model's weights were tuned by hand on training scenarios.
The learned approach that the challenge asks for is not built yet (see [Status](#status)).

## Results

Micro-averaged over every scenario in each partition, counting exact directed successor links.
"Wrong" is an accepted link that is not the labelled successor; "unresolved" is a labelled link whose source got no successor.

| Approach | Partition | Correct / true links | Wrong | Unresolved | Precision | Recall |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | Train (80) | 285 / 1364 | 553 | 526 | 0.340 | 0.209 |
| Baseline | Validation (20) | 68 / 335 | 139 | 128 | 0.329 | 0.203 |
| Baseline | Test (20) | 64 / 339 | 135 | 140 | 0.322 | 0.189 |
| Gaussian | Train (80) | 1337 / 1364 | 12 | 15 | 0.991 | 0.980 |
| Gaussian | Validation (20) | 331 / 335 | 1 | 3 | 0.997 | 0.988 |
| Gaussian | Test (20) | 333 / 339 | 4 | 2 | 0.988 | 0.982 |

The Gaussian model's settings were frozen before the test labels were first read, and nothing was changed after.
The baseline's 5% tolerance was given up front, not tuned.
Tracklets without a labelled successor were never given one by the Gaussian model in any partition.
Candidate generation keeps every pair whose target starts after its source ends, so candidate-generation recall is 100% for both approaches.
Full tuning history, error analysis and the trade-off between wrong and unresolved links are in [REPORT.md](REPORT.md).

The baseline's precision stays between 0.25 and 0.36 at any tolerance on validation, so tightening it cannot make it trustworthy:

| Baseline tolerance | Accepted | Correct | Wrong | Unresolved | Precision | Recall |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5% | 56 | 16 | 40 | 279 | 0.286 | 0.048 |
| 1% | 93 | 23 | 70 | 242 | 0.247 | 0.069 |
| 2% | 137 | 36 | 101 | 198 | 0.263 | 0.107 |
| 5% (used) | 207 | 68 | 139 | 128 | 0.329 | 0.203 |
| 10% | 247 | 89 | 158 | 88 | 0.360 | 0.266 |

It checks speed only: any tracklet roughly the right distance away passes, whatever its direction, so with six aircraft per scenario it often picks the wrong one.

### Additional evidence (staged demo)

Snapshot 1 and snapshot 2 are analysed independently with the same settings; saved results are in `results/staged_demo/`.
Snapshot 2 adds one delayed tracklet, `691cc611`, whose observation times fall inside an earlier gap.

- Gaussian model: snapshot 1 accepts `aa82aa85 -> 1d397900` (cost 0.350, gap 346 s). In snapshot 2 that link is replaced by `aa82aa85 -> 691cc611` (cost 0.175, gap 122 s) and `691cc611 -> 1d397900` (cost 0.116, gap 106 s). This is the same aircraft with a newly available intermediate fragment, not a change of identity. Both snapshots match their own labels exactly (17/17 and 18/18).
- Baseline: leaves the new tracklet unlinked and changes nothing else; it gets 2 of 17 and 2 of 18 links right.

## How it works

Shared preparation, used by both approaches (`io.py`, `track.py`):

1. **Validate.** Required fields present, IDs non-empty strings, `observed_at` and `received_at` ISO 8601 in UTC, latitude and longitude numeric and in range, `altitude_ft` numeric or null, `attributes` an object. Violations are rejected with a message naming the record.
2. **Deduplicate.** Records with the same ID and equal parsed JSON are kept once; different content under one ID is rejected. A missing field and an explicit null count as different, as the clarifications specify. The same rules apply to supporting records keyed on `record_id`.
3. **Receipt cutoff.** When a cutoff is given, records received after it are left out, so a snapshot only sees evidence released by then.
4. **Project.** Latitude/longitude are projected to a local azimuthal equidistant plane centred on the scenario, in metres. EPSG:3857 was not used because it inflates distances by about 22% at these latitudes.
5. **Altitude.** `altitude_ft * 0.3048` when reported, else supporting `a1` (see below), else the tracklet's mean altitude.
6. **Order and near-duplicates.** Reports are sorted by `observed_at` (observation ID breaks ties). A report from the same feed within 0.5 s and 50 m of the previous kept one is dropped.
7. **Velocity.** Velocity samples span at least 10 s, because reports can be under a second apart and ~200 m of position noise would otherwise give speeds off by hundreds of m/s.

**Baseline** (`baseline.py`): for each candidate pair, compare the horizontal displacement from the source's last report to the target's first report with the source's last speed times the time gap.
Score is `|displacement / projected - 1|`; pairs within 5% are acceptable and are taken greedily, best score first, skipping any source that already has a successor or target that already has a predecessor.

**Gaussian continuity model** (`associate.py`, settings in `weights.py`): each tracklet gets a horizontal position Gaussian, an altitude Gaussian and a 3D velocity Gaussian, plus start and end states from line fits over its first and last 6 reports.
Each candidate pair is scored by a weighted sum of seven costs:

| Term | Meaning |
| --- | --- |
| plausibility | `abs(log(actual gap distance / projected gap distance))` |
| heading | is the target ahead of the source along its direction of travel |
| vel_cos | are both tracklets flying the same way |
| vel_w2 | Wasserstein-2 distance between velocity Gaussians |
| pos_w2 | Wasserstein-2 distance between horizontal position Gaussians |
| alt_w2 | Wasserstein-2 distance between altitude Gaussians |
| dt | gap length, so a link that skips an intermediate tracklet costs more |

A Hungarian assignment over the cost matrix, extended with a "no match" option per source at cost 1.0, selects links so every tracklet has at most one successor and one predecessor.
A competing candidate can lose even when it is a source's cheapest option, if giving it to another source lowers the total cost; the decision reason says so.

**Scores** are costs for both approaches: lower is better and 0 is a perfect fit.
They are not probabilities and have not been calibrated.

**Constraints.** A target must start strictly after its source ends, so accepted links can never form a cycle.
Results do not depend on input order or on identical duplicates; tests check both.

## Signals used and excluded

| Signal | Use | Basis |
| --- | --- | --- |
| `observed_at`, `latitude`, `longitude` | used | documented core fields |
| `altitude_ft` | used, converted to metres | documented core field; missing in about 23% of reports |
| `received_at` | used only to apply receipt cutoffs | documented; it says when evidence was available, not where the aircraft was |
| supporting `a1` | used to fill missing altitude | undocumented; inferred as altitude in metres, see below |
| `source` | not a feature | used only to restrict near-duplicate removal to the same feed |
| observation `attributes` (`spd`, `hdg`, `c2`, `v`, `ref`, `tag`, `q`, `node`, `r7`, ...) | excluded | units and meanings are undocumented or inconsistent between feeds; not investigated yet |
| supporting `spd`, `b4`, `rx` | excluded | uncorrelated with altitude (correlation near 0); not compared with speed or heading yet; `b4` does not change `a1`'s accuracy |
| IDs and file order | never features | used only for grouping, joining and tie-breaking |

`a1` is read as altitude in metres because, on the 10,194 train observations that also report `altitude_ft`, it agrees with `altitude_ft * 0.3048` with correlation 0.999, median ratio 1.000 and at most 162 m difference.
It is null in about 12% of supporting records and fills 54% of missing altitudes.
This is an inference from data, not a documented meaning.

## Assumptions

- Each scenario is independent; links never cross scenarios.
- Motion is smooth and subsonic, so a local planar projection and straight-line extrapolation over gaps of a few minutes are adequate, as the data contract allows.
- A tracklet needs at least two reports at distinct times; otherwise it is rejected.
- Supporting records are joined only by `observation_id`, as documented.
- Each observation has at most one supporting altitude; two different values for one observation are rejected rather than resolved silently.

## Known limitations

- No learned approach yet, and so no training command, model artifact or random seed. Both approaches are deterministic and configured in code; `GET /health` and every result report the configuration hash.
- The Gaussian model's weights, scales, velocity window and no-match cost were chosen by hand from training labels, not by a systematic search, and the operating threshold was not chosen on validation as the challenge asks.
- Lowering the no-match cost removes few wrong links per unresolved link it adds; most remaining wrong links are confident swaps between nearby aircraft. Abstaining when the best and second-best candidates are close would target them better.
- The `dt` penalty grows with the absolute gap, so it cannot be raised to stop skipped tracklets without also penalising long true gaps.
- No stress test has been run.
- Results are on synthetic data with simplified motion and sources; they are not evidence of real-world accuracy.

## Setup and commands

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).
Dependencies are pinned in `uv.lock`.

```bash
uv sync                                   # create the environment
uv run pytest                             # run the tests
uv run ruff check . && uv run ruff format --check .   # lint
```

Evaluate against labels (one directory prints per-link diagnostics; several print per-scenario lines and totals):

```bash
uv run python -m aircraft_track_association evaluate candidate-packet/data/train/scenario_*
uv run python -m aircraft_track_association evaluate candidate-packet/data/validation/scenario_*
uv run python -m aircraft_track_association evaluate candidate-packet/data/test/scenario_*
uv run python -m aircraft_track_association evaluate candidate-packet/data/test/scenario_014 --approach gaussian
```

Run association on one snapshot without labels and save the result (this is how `results/staged_demo/` was produced):

```bash
uv run python -m aircraft_track_association analyze candidate-packet/data/staged_demo/snapshot_1 -o results/staged_demo/snapshot_1.json
uv run python -m aircraft_track_association analyze candidate-packet/data/staged_demo/snapshot_2 -o results/staged_demo/snapshot_2.json
```

Start the service and web interface, then open http://127.0.0.1:8000:

```bash
uv run python -m aircraft_track_association serve
```

The interface lets a reviewer pick a scenario and run both approaches, see tracklets and accepted links on a longitude/latitude plot, compare baseline and Gaussian decisions against the labels, inspect each tracklet's ranked candidates, measurements, decision reason and original reports, and compare the two staged snapshots.

## API

All errors are JSON, `{"error": {"code": str, "message": str}}`: 404 `not_found`, 422 `invalid_request` (malformed body) or `invalid_input` (records that break the data contract), 500 `internal_error`.

`GET /health` returns `status`, `model_version`, `approaches` and `config_sha256`.

`POST /tracklets/associate` takes raw records exactly as they appear in the JSONL files:

```json
{
  "snapshot_id": "optional string",
  "receipt_cutoff": "optional ISO 8601 UTC timestamp",
  "observations": [{"observation_id": "...", "tracklet_id": "...", "observed_at": "...", "...": "..."}],
  "supporting": [{"record_id": "...", "observation_id": "...", "...": "..."}]
}
```

It returns:

```json
{
  "snapshot_id": "...", "receipt_cutoff": "...",
  "input_sha256": "hash of the deduplicated, ID-sorted inputs",
  "model_version": "0.1.0", "config_sha256": "...", "config": {"...": "every decision setting"},
  "counts": {"observation_rows": 0, "unique_observations": 0, "tracklets": 0, "...": 0},
  "tracklets": [{"tracklet_id": "...", "source": "...", "observation_count": 0, "start_s": 0, "end_s": 0}],
  "results": {
    "baseline": [
      {
        "tracklet_id": "...",
        "status": "accepted | unresolved",
        "successor": "tracklet id or null",
        "score": 0.0,
        "reason": "short explanation",
        "candidates": [{"tracklet_id": "...", "score": 0.0, "measurements": {"...": 0.0}}]
      }
    ],
    "gaussian": ["same shape"]
  }
}
```

`candidates` holds up to five candidates, best first.
Labels are never read by this endpoint.

`GET /scenarios` lists the packaged scenarios, and `GET /scenarios/{partition}/{name}` returns one scenario's raw records plus its labels; the interface uses the labels only to display correctness.

## Layout

- `candidate-packet/` - the assessment data package, copied verbatim, plus `SOURCE_NOTES.md` transcribed from `Source Notes.docx`.
- `src/aircraft_track_association/`
  - `io.py` - validation, deduplication, file loading.
  - `track.py` - projection, altitude, near-duplicates, tracklet Gaussians.
  - `baseline.py` - the baseline rule.
  - `associate.py`, `weights.py` - the Gaussian continuity model and its settings.
  - `decision.py` - the per-tracklet decision record both approaches return.
  - `pipeline.py` - one analysis run, shared by the CLI and the service.
  - `evaluate.py` - link metrics.
  - `service.py`, `static/index.html` - HTTP service and web interface.
  - `__main__.py` - command line.
- `tests/` - unit, pipeline and API tests.
- `results/staged_demo/` - saved results for both staged snapshots.
- `REPORT.md` - tuning history, error analysis and limitations of the Gaussian model.

## Status

Done: data preparation, both approaches with per-tracklet decisions and reasons, evaluation on all partitions, staged demo with saved results, backend, web interface and tests.
Not done: a learned approach and its comparison, threshold selection on validation, a stress test, a written failure analysis of test errors, and investigation of the optional observation attributes.
