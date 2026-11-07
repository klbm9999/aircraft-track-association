# aircraft-track-association

Govia ML Engineer take-home: associate fragmented aircraft tracklets into per-aircraft tracks.

## Layout

- `candidate-packet/` - the original assessment data package, copied verbatim (see `candidate-packet/README.md`).
- `src/aircraft_track_association/` - heuristic tracklet association; decision settings (weights, scales, no-match cost) are in `weights.py`.
- `tests/` - unit tests and a scenario_001 regression check.

## Method

1. Drop identical retransmissions by `observation_id`, rejecting conflicting reports and out-of-range coordinates.
2. Project positions from EPSG:4326 to a local azimuthal equidistant plane in metres, convert altitude from feet to metres, and fill missing altitude with the tracklet mean.
3. Drop near-duplicate reports (same feed, within 0.5 s and 50 m).
4. Model each tracklet as a 3D position Gaussian and a 3D velocity Gaussian, with velocities taken over baselines of at least 10 s to suppress position noise.
5. Score every source -> target pair that starts after the source ends using projected-gap plausibility, heading and velocity cosines, Wasserstein-2 distances between the velocity and position Gaussians, and a small gap-length penalty.
6. Solve a Hungarian assignment with a no-match option so each tracklet gets at most one successor and one predecessor.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python -m aircraft_track_association candidate-packet/data/train/scenario_001
uv run pytest
```
