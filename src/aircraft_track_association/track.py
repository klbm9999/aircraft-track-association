"""Turn one scenario's observations into per-tracklet motion models in metres.

Pipeline for each tracklet:
1. Project latitude/longitude (EPSG:4326) to a local azimuthal equidistant plane centred on the
   scenario. Distances stay true to within metres over the ~100 km scenario extent; EPSG:3857
   would inflate them by 1/cos(lat), about 22% at 35N.
2. Convert altitude from feet to metres, filling missing values with the tracklet mean.
3. Sort by observation time and drop near-duplicate reports.
4. Summarise the tracklet as:
   - a 3D position Gaussian (mean, covariance) over its reports,
   - a 3D velocity Gaussian over velocity samples spanning at least MIN_BASELINE_S,
   - start and end states (position, velocity) from straight-line fits at each end, used to
     project the aircraft across the gap to a candidate successor.
"""

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from pyproj import Transformer

from .io import parse_time

FT_TO_M = 0.3048
DUP_DT_S = 0.5  # near-duplicate: same tracklet and feed, closer than this in time...
DUP_DIST_M = 50.0  # ...and this in space
ENDPOINT_POINTS = 6  # points used for the straight-line fit at each end of a tracklet
# Velocity samples span at least this long: ~200 m position noise over sub-second report
# gaps otherwise yields spurious 500+ m/s velocities.
MIN_BASELINE_S = 10.0


@dataclass
class Tracklet:
    id: str
    t: np.ndarray  # (n,) seconds, sorted
    p: np.ndarray  # (n, 3) metres: east, north, altitude
    pos_mean: np.ndarray  # (3,) position Gaussian
    pos_cov: np.ndarray  # (3, 3)
    vel_mean: np.ndarray  # (3,) velocity Gaussian, m/s
    vel_cov: np.ndarray  # (3, 3)
    start_pos: np.ndarray  # line fit over the first points, evaluated at t[0]
    start_vel: np.ndarray
    end_pos: np.ndarray  # line fit over the last points, evaluated at t[-1]
    end_vel: np.ndarray


def projector(observations: list[dict]) -> Transformer:
    """Lon/lat -> metres on a plane tangent at the scenario's mean position."""
    lat0 = float(np.mean([o["latitude"] for o in observations]))
    lon0 = float(np.mean([o["longitude"] for o in observations]))
    crs = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m"
    return Transformer.from_crs("EPSG:4326", crs, always_xy=True)


def line_fit(t: np.ndarray, p: np.ndarray, t_ref: float) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares straight line through the points; returns (position at t_ref, velocity)."""
    vel, pos = np.polyfit(t - t_ref, p, 1)
    return pos, vel


def build_tracklet(tid: str, t: np.ndarray, p: np.ndarray) -> Tracklet:
    """Fit the position/velocity Gaussians and endpoint states for one cleaned tracklet."""
    # Pair each report with the first report at least MIN_BASELINE_S later, so every velocity
    # sample averages over enough motion to swamp position noise.
    nxt = np.searchsorted(t, t + MIN_BASELINE_S)
    src = np.flatnonzero(nxt < len(t))
    if len(src) == 0:  # tracklet shorter than the baseline: one end-to-end sample
        src, nxt = np.array([0]), np.array([len(t) - 1])
    v = (p[nxt[src]] - p[src]) / (t[nxt[src]] - t[src])[:, None]

    # Endpoint states come from a line fit, not the raw first/last report, so one noisy report
    # cannot throw off the projection across the gap.
    k = min(ENDPOINT_POINTS, len(t))
    start_pos, start_vel = line_fit(t[:k], p[:k], t[0])
    end_pos, end_vel = line_fit(t[-k:], p[-k:], t[-1])

    vel_cov = np.cov(v.T) if len(v) > 1 else np.zeros((3, 3))  # single velocity sample
    return Tracklet(
        id=tid,
        t=t,
        p=p,
        pos_mean=p.mean(0),
        pos_cov=np.cov(p.T),
        vel_mean=v.mean(0),
        vel_cov=vel_cov,
        start_pos=start_pos,
        start_vel=start_vel,
        end_pos=end_pos,
        end_vel=end_vel,
    )


def build_tracklets(observations: list[dict]) -> list[Tracklet]:
    """Group a scenario's (already ID-deduplicated) observations into Tracklet models."""
    to_m = projector(observations)
    # Fallback only for a tracklet with no altitude at all; none occur in the packaged data.
    alts = [o["altitude_ft"] for o in observations if o.get("altitude_ft") is not None]
    scenario_alt = float(np.mean(alts)) if alts else 0.0

    groups: dict[str, list[dict]] = defaultdict(list)
    for o in observations:
        groups[o["tracklet_id"]].append(o)

    tracklets = []
    for tid, obs in groups.items():
        obs.sort(key=lambda o: parse_time(o["observed_at"]))  # file order is not time order

        # Metric coordinates: x east, y north, z altitude (missing -> tracklet mean).
        present = [o["altitude_ft"] for o in obs if o.get("altitude_ft") is not None]
        mean_alt = float(np.mean(present)) if present else scenario_alt
        t = np.array([parse_time(o["observed_at"]) for o in obs])
        x, y = to_m.transform([o["longitude"] for o in obs], [o["latitude"] for o in obs])
        z = [
            FT_TO_M * (o["altitude_ft"] if o.get("altitude_ft") is not None else mean_alt)
            for o in obs
        ]
        p = np.column_stack([x, y, z])

        # Drop a report that repeats the last kept one from the same feed in time and space.
        keep = [0]
        for i in range(1, len(obs)):
            j = keep[-1]
            near = (
                t[i] - t[j] < DUP_DT_S
                and np.linalg.norm(p[i] - p[j]) < DUP_DIST_M
                and obs[i]["source"] == obs[j]["source"]
            )
            if not near:
                keep.append(i)
        tracklets.append(build_tracklet(tid, t[keep], p[keep]))
    return tracklets
