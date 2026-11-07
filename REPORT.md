# Tracklet Association Heuristic - Results

## Summary

A hand-tuned heuristic links tracklets with micro F1 0.988 on validation and 0.973 on the 69 train scenarios that played no part in tuning.
No tracklet without a labelled successor was ever given one, in any partition.
The test partition has not been run.

| Partition | Scenarios | True links | Correct | Wrong | Unreported | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Train, used for tuning (001-011) | 11 | 186 | 178 | 6 | 2 | 0.967 | 0.957 | 0.962 |
| Train, not used for tuning (012-080) | 69 | 1178 | 1139 | 25 | 14 | 0.979 | 0.967 | 0.973 |
| Train, all | 80 | 1364 | 1317 | 31 | 16 | 0.977 | 0.966 | 0.971 |
| Validation | 20 | 335 | 330 | 3 | 2 | 0.991 | 0.985 | 0.988 |

Metrics count exact directed successor links, micro-averaged over scenarios.
"Wrong" is a reported link that is not the labelled successor.
"Unreported" is a labelled link whose source was left with no link at all.
A true link lost because its source got a wrong link is counted once, as wrong.

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

## Tuning strategy

Tuning was manual and diagnostic-driven, not a search.
Each iteration ran the 10 tuning scenarios (train 002-011), printed the cost terms of the predicted and true successor for every miss, and changed the weights to fix the pattern that explained the most misses.
At most 3 iterations were allowed, and the stopping bar was micro F1 of at least 0.95 on those same 10 scenarios.
The objective was F1, which counts a wrong link and an unreported link as equally bad.
No grid or random search, cross-validation or held-out tuning split was used.
The scales and the 10 s velocity window were set earlier from scenario_001 alone.
Train 012-080 and validation played no part in any choice, so they are the fair estimates.

## Tuning log

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

### Wrong versus unreported links

A wrong link asserts a false identity, so it is worse than leaving a true link unreported.
The current weights make more wrong links than unreported ones in every partition: 25 vs 14 on train 012-080, and 3 vs 2 on validation.

The obvious lever is the no-match cost: lower it and the solver abstains more often.
The sweep below shows it does not remove wrong links efficiently.

| No-match cost | Train 012-080 wrong | Train 012-080 unreported | Validation wrong | Validation unreported |
| ---: | ---: | ---: | ---: | ---: |
| 1.0 (current) | 25 | 14 | 3 | 2 |
| 0.5 | 26 | 49 | 6 | 10 |
| 0.3 | 18 | 162 | 3 | 42 |
| 0.2 | 10 | 355 | 1 | 111 |

Cutting wrong links on train 012-080 from 25 to 18 costs 148 extra unreported links.
At 0.5, wrong links even rise on validation, because dropping some links lets the assignment reshuffle others.
Most wrong links are confident swaps between parallel aircraft: their absolute cost is as low as a true link's.
A better abstention signal would be ambiguity, for example the margin between a source's best and second-best candidate, rather than absolute cost.
This sweep is a report, not a tuning step, and the no-match cost stays at 1.0.
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
