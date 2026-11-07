# Tracklet Association Heuristic - Results

## Summary

A hand-tuned heuristic links tracklets with micro F1 0.988 on validation and 0.973 on the 69 train scenarios that played no part in tuning.
No tracklet without a labelled successor was ever given one, in any partition.
The test partition has not been run.

| Partition | Scenarios | True links | Predicted | Correct | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Train, used for tuning (001-011) | 11 | 186 | 184 | 178 | 0.967 | 0.957 | 0.962 |
| Train, not used for tuning (012-080) | 69 | 1178 | 1164 | 1139 | 0.979 | 0.967 | 0.973 |
| Train, all | 80 | 1364 | 1348 | 1317 | 0.977 | 0.966 | 0.971 |
| Validation | 20 | 335 | 333 | 330 | 0.991 | 0.985 | 0.988 |

Metrics count exact directed successor links, micro-averaged over scenarios.

## Method

Each scenario is processed independently, and labels are read only for scoring.

1. **Load.** Identical retransmissions are collapsed by `observation_id`. Conflicting reports that share an ID, out-of-range coordinates and missing required fields raise an error.
2. **Project.** Positions go from EPSG:4326 to a local azimuthal equidistant plane centred on the scenario, in metres. EPSG:3857 was rejected because it inflates distances by about 22% at 35N. Altitude is converted from feet to metres, and missing values take the tracklet mean.
3. **Near-duplicates.** Within a tracklet, a report from the same feed within 0.5 s and 50 m of the previous kept report is dropped.
4. **Tracklet model.** Each tracklet gets a 3D position Gaussian and a 3D velocity Gaussian. Velocity samples span at least 10 s, because position noise of about 200 m over sub-second report gaps otherwise produces velocities above 500 m/s. Start and end states come from straight-line fits over the first and last 6 reports.
5. **Pair costs.** Every source -> target pair where the target starts after the source ends is scored. Pairs that overlap in time get infinite cost. Each term is divided by a fixed scale and weighted:

| Term | Definition | Scale | Weight |
| --- | --- | ---: | ---: |
| plausibility | abs(log(actual gap distance / (end speed x gap time))) | 0.25 | 0.35 |
| heading | 1 - cos(source mean velocity, source -> target mean-position offset) | 0.02 | 3.0 |
| vel_cos | 1 - cos(source mean velocity, target mean velocity) | 0.005 | 0.15 |
| vel_w2 | Wasserstein-2 distance between velocity Gaussians | 36 m/s | 0.05 |
| pos_w2 | Wasserstein-2 distance between position Gaussians | 56 km | 0.03 |
| dt | gap time, penalising links that skip a tracklet | 225 s | 0.15 |

6. **Assignment.** A Hungarian solve over a 2n x 2n matrix gives each tracklet at most one successor and one predecessor. A source stays unlinked when every candidate costs more than 1.0.

Scales are roughly the medians of each term over scenario_001's candidate pairs. They are fixed, so a cost means the same thing in every scenario.

## Tuning log

Weights were tuned by hand on train scenarios 002-011, with at most 3 iterations allowed.
The stopping bar was micro F1 of at least 0.95 on those 10 scenarios.

| Run | plaus | heading | vel_cos | vel_w2 | pos_w2 | dt | Micro F1 (002-011) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Starting weights (from scenario_001) | 0.35 | 0.15 | 0.15 | 0.20 | 0.10 | 0.05 | 0.896 |
| Iteration 1 | 0.40 | 0.15 | 0.15 | 0.12 | 0.08 | 0.15 | 0.896 |
| Iteration 2 (kept) | 0.35 | 3.0 | 0.15 | 0.05 | 0.03 | 0.15 | 0.958 |

Iteration 1 lowered the velocity Wasserstein weight and raised the gap penalty, aimed at skip links. It changed no prediction in any of the 10 scenarios.
The misses showed two patterns:
- Aircraft flying in parallel swapped successors, with total costs within 0.03 of each other.
- True links lost because their velocity Wasserstein distance stayed inflated (57-118 m/s) by noisy velocity covariances.

On 13 of the 14 misses inspected in scenarios 002, 006 and 010, the heading term was lower for the true link, often by 10-100x. At weight 0.15 it contributed about 0.001, so iteration 2 raised it to 3.0 and cut the velocity Wasserstein weight further.
Iteration 2 cleared the bar, so iteration 3 was not run.
Scenario_009 regressed from 18/18 to 16/18 in iteration 2, while scenarios 006, 008, 010 and 011 improved.

The weight order plausibility > vel_w2 > pos_w2 is kept.
Raw weights are not directly comparable across terms, because the fixed scales differ.

## Errors

| Partition | Wrong aircraft | Skipped intermediate | Left unlinked | False link from last tracklet |
| --- | ---: | ---: | ---: | ---: |
| Train, used for tuning | 5 | 1 | 2 | 0 |
| Train, not used for tuning | 22 | 3 | 14 | 0 |
| Validation | 2 | 1 | 2 | 0 |

Linking to the wrong aircraft is the main error mode.
The weakest train scenarios are 017 (F1 0.727), 046 (0.750) and 053 (0.800). They have not been inspected yet.

## Limitations

- The weights, the 10 s velocity window, the scales and the no-match cost of 1.0 were all chosen from training labels, with the full grid searched only by hand.
- The no-match cost is barely exercised: the last tracklet of each aircraft has no later candidates, so it is correctly left unlinked because nothing starts after it ends.
- Plausibility compares only distances. Direction comes in through the heading cosine on mean positions, not through a miss distance at the predicted point.
- Velocity covariance still reflects feed noise more than aircraft motion, which is why the velocity Wasserstein weight had to be cut.
- Optional `attributes` and `supporting.jsonl` are not used.
- The staged-demo snapshots and the test partition have not been run.

## Reproduce

```bash
uv run python -m aircraft_track_association candidate-packet/data/train/scenario_*
uv run python -m aircraft_track_association candidate-packet/data/validation/scenario_*
uv run python -m aircraft_track_association candidate-packet/data/train/scenario_006  # per-miss diagnostics
```
