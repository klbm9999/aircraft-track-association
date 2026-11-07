# Tracklet Association Heuristic - Results

## Summary

A hand-tuned heuristic links tracklets with micro F1 0.993 on validation and 0.985 on the 69 train scenarios that played no part in tuning.
It makes 1 wrong link on validation and 11 on held-out train.
No tracklet without a labelled successor was ever given one, in any partition.

## Final results (frozen method)

The method and every setting were frozen at commit 5efa814 before the test labels were read for the first time.
No change was made after seeing test results.

| Partition | Scenarios | True links | Accepted | Correct | Wrong | Unresolved | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 80 | 1364 | 1349 | 1337 | 12 | 15 (1.1%) | 0.991 | 0.980 | 0.986 |
| Validation | 20 | 335 | 332 | 331 | 1 | 3 (0.9%) | 0.997 | 0.988 | 0.993 |
| Test | 20 | 339 | 337 | 333 | 4 | 2 (0.6%) | 0.988 | 0.982 | 0.985 |

"Unresolved" is the challenge's unresolved proportion: tracklets with a labelled successor that got no accepted successor, over all tracklets with a labelled successor.
All 480 train, 120 validation and 120 test tracklets without a successor were correctly left unlinked.
Test errors are concentrated in three scenarios: 014 (3 wrong links, F1 0.824), 018 (1 wrong, 1 unresolved) and 010 (1 unresolved).
Train includes the 11 scenarios used for tuning; the held-out split is broken out below.

## Development results

| Partition | Scenarios | True links | Correct | Wrong | Unreported | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Train, used for tuning (001-011) | 11 | 186 | 184 | 1 | 1 | 0.995 | 0.989 | 0.992 |
| Train, not used for tuning (012-080) | 69 | 1178 | 1153 | 11 | 14 | 0.991 | 0.979 | 0.985 |
| Train, all | 80 | 1364 | 1337 | 12 | 15 | 0.991 | 0.980 | 0.986 |
| Validation | 20 | 335 | 331 | 1 | 3 | 0.997 | 0.988 | 0.993 |

Metrics count exact directed successor links, micro-averaged over scenarios.
"Wrong" is a reported link that is not the labelled successor.
"Unreported" is a labelled link whose source was left with no link at all.
A true link lost because its source got a wrong link is counted once, as wrong.

## Method

Each scenario is processed independently, and labels are read only for scoring.

1. **Load.** Identical retransmissions are collapsed by `observation_id`, and supporting records by `record_id`. Conflicting records that share an ID, out-of-range coordinates and missing required fields raise an error.
2. **Project.** Positions go from EPSG:4326 to a local azimuthal equidistant plane centred on the scenario, in metres. EPSG:3857 was rejected because it inflates distances by about 22% at 35N.
3. **Altitude.** Altitude is `altitude_ft * 0.3048` when reported, else the supporting record's `a1` (see below), else the tracklet mean.
4. **Near-duplicates.** Within a tracklet, a report from the same feed within 0.5 s and 50 m of the previous kept report is dropped.
5. **Tracklet model.** Each tracklet gets three Gaussians: horizontal position (x, y), altitude (z) and 3D velocity. Velocity samples span at least 10 s, because position noise of about 200 m over sub-second report gaps otherwise produces velocities above 500 m/s. Start and end states come from straight-line fits over the first and last 6 reports.
6. **Pair costs.** Every source -> target pair where the target starts after the source ends is scored. Pairs that overlap in time get infinite cost. Each term is divided by a fixed scale and weighted (`src/aircraft_track_association/weights.py`):

| Term | Definition | Scale | Weight |
| --- | --- | ---: | ---: |
| plausibility | abs(log(actual gap distance / (end speed x gap time))) | 0.25 | 0.35 |
| heading | 1 - cos(source horizontal mean velocity, source -> target horizontal mean-position offset) | 0.02 | 3.0 |
| vel_cos | 1 - cos(source mean velocity, target mean velocity) | 0.005 | 0.15 |
| vel_w2 | Wasserstein-2 distance between velocity Gaussians | 36 m/s | 0.05 |
| pos_w2 | Wasserstein-2 distance between horizontal position Gaussians | 56 km | 0.03 |
| alt_w2 | Wasserstein-2 distance between altitude Gaussians, sqrt(mean gap^2 + std gap^2) | 700 m | 0.3 |
| dt | gap time, penalising links that skip a tracklet | 225 s | 0.15 |

7. **Assignment.** A Hungarian solve over a 2n x 2n matrix gives each tracklet at most one successor and one predecessor. A source stays unlinked when every candidate costs more than 1.0.

Scales are roughly the medians of each term over scenario_001's candidate pairs. They are fixed, so a cost means the same thing in every scenario.

### Supporting altitude (`a1`)

`a1` in `supporting.jsonl` is undocumented and is read as altitude in metres.
On the 10,194 train observations that also report `altitude_ft`, it agrees with `altitude_ft * 0.3048` with correlation 0.999, median ratio 1.000 and at most 162 m difference, which is normal measurement noise.
It is null in about 12% of records, every observation has at most one supporting record, and the `b4` flag does not change its agreement.
It fills 54% of missing altitudes (3,712 of 6,875 across train and validation).
On its own, before the altitude cost existed, it changed no prediction, because altitude then entered the cost only through terms that hardly weighed it.
The other supporting fields (`spd`, `b4`, `rx`) and the optional observation `attributes` are not used.

## Tuning strategy

Tuning was manual and diagnostic-driven, not a search.
Each iteration ran the 10 tuning scenarios (train 002-011), printed the cost terms of the predicted and true successor for every miss, and changed the weights to fix the pattern that explained the most misses.
The objective was F1, which counts a wrong link and an unreported link as equally bad.
No grid or random search, cross-validation or held-out tuning split was used.
The scales and the 10 s velocity window were set earlier from scenario_001 alone.
Train 012-080 and validation played no part in the weight choices up to iteration 2.
For the altitude weight they were looked at after the choice was made, as noted below.

## Tuning log

| Run | plaus | heading | vel_cos | vel_w2 | pos_w2 | alt_w2 | dt | F1 (002-011) | F1 (012-080) | F1 (validation) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Starting weights (from scenario_001) | 0.35 | 0.15 | 0.15 | 0.20 | 0.10 | - | 0.05 | 0.896 | | |
| Iteration 1 | 0.40 | 0.15 | 0.15 | 0.12 | 0.08 | - | 0.15 | 0.896 | | |
| Iteration 2 | 0.35 | 3.0 | 0.15 | 0.05 | 0.03 | - | 0.15 | 0.958 | 0.973 | 0.988 |
| Trial: dt up, heading down | 0.35 | 1.5 | 0.15 | 0.05 | 0.03 | - | 0.6 | 0.952 | 0.962 | 0.967 |
| Trial: dt up | 0.35 | 3.0 | 0.15 | 0.05 | 0.03 | - | 0.6 | 0.958 | 0.959 | 0.966 |
| Trial: dt off | 0.35 | 3.0 | 0.15 | 0.05 | 0.03 | - | 0 | 0.958 | 0.971 | 0.984 |
| Horizontal-only position, no altitude cost | 0.35 | 3.0 | 0.15 | 0.05 | 0.03 | 0 | 0.15 | 0.886 | 0.919 | 0.916 |
| Altitude cost 0.1 | 0.35 | 3.0 | 0.15 | 0.05 | 0.03 | 0.1 | 0.15 | 0.991 | 0.976 | 0.987 |
| **Altitude cost 0.3 (kept)** | 0.35 | 3.0 | 0.15 | 0.05 | 0.03 | 0.3 | 0.15 | 0.991 | 0.985 | 0.993 |
| Altitude cost 0.6 | 0.35 | 3.0 | 0.15 | 0.05 | 0.03 | 0.6 | 0.15 | 0.991 | 0.986 | 0.991 |

Rows above "Horizontal-only position" used a 3D position Gaussian and a 3D heading offset; from that row on, both are 2D and altitude has its own term.

**Iteration 1** lowered the velocity Wasserstein weight and raised the gap penalty, aimed at skip links. It changed no prediction in any of the 10 scenarios.
The misses showed two patterns:
- Aircraft flying in parallel swapped successors, with total costs within 0.03 of each other.
- True links lost because their velocity Wasserstein distance stayed inflated (57-118 m/s) by noisy velocity covariances.

**Iteration 2.** On 13 of the 14 misses inspected in scenarios 002, 006 and 010, the heading term was lower for the true link, often by 10-100x. At weight 0.15 it contributed about 0.001, so iteration 2 raised it to 3.0 and cut the velocity Wasserstein weight further.
Scenario_009 regressed from 18/18 to 16/18, while scenarios 006, 008, 010 and 011 improved.

**Gap-penalty trials.** Raising the dt weight to 0.6 removed every skip link on held-out train (3 to 0), but it is an absolute penalty, so true links with long gaps crossed the no-match cost and unreported links rose from 14 to 39-48.
Lowering heading at the same time added swaps between aircraft.
Turning dt off added skip links (3 to 5 on held-out train).
Plausibility does not prevent skips: on 872 train sources the true link had the lower plausibility cost than the skip-by-one link only 34% of the time, because a fixed position error is a smaller fraction of a longer gap.
The dt weight stayed at 0.15.

**Altitude.** The position Gaussian was split into a horizontal Gaussian and a separate altitude Gaussian with its own cost.
Removing altitude from the position and heading terms alone made results much worse (validation wrong links 3 to 27).
The 3D heading offset had been doing altitude discrimination implicitly: parallel aircraft at different altitudes tilt the offset vector, which is part of why the heading weight of 3.0 worked.
With an explicit altitude cost, every weight tried beat iteration 2.
On true links the altitude distance is at most 209 m in train 001-011, while the median over other candidate pairs is 781 m.
Weights 0.1, 0.3 and 0.6 tie on the tuning set (166/168 correct), so the tuning set could not choose.
0.3 was chosen as the middle value: a 400 m altitude mismatch then costs about 0.17, the same order as the dt penalty for a skipped tracklet.
The held-out and validation figures for all three weights were seen before this choice, so they are not fully blind for it.

The weight order plausibility > vel_w2 > pos_w2 is kept.
Raw weights are not directly comparable across terms, because the fixed scales differ.

## Errors

| Partition | Wrong aircraft | Skipped intermediate | Left unlinked | False link from last tracklet |
| --- | ---: | ---: | ---: | ---: |
| Train, used for tuning | 0 | 1 | 1 | 0 |
| Train, not used for tuning | 8 | 3 | 14 | 0 |
| Validation | 0 | 1 | 3 | 0 |

The altitude cost cut wrong-aircraft links on held-out train from 22 to 8 and on validation from 2 to 0.
Unreported links are now the largest error group, followed by wrong-aircraft and skip links.
The weakest train scenarios are 017 (F1 0.727), 046 (0.875) and 073 (0.889). They have not been inspected yet.

### Wrong versus unreported links

A wrong link asserts a false identity, so it is worse than leaving a true link unreported.
On held-out train the weights now make fewer wrong links than unreported ones (11 vs 14), and on validation 1 vs 3.

The obvious lever is the no-match cost: lower it and the solver abstains more often.

| No-match cost | Train 012-080 wrong | Train 012-080 unreported | Validation wrong | Validation unreported |
| ---: | ---: | ---: | ---: | ---: |
| 1.0 (current) | 11 | 14 | 1 | 3 |
| 0.5 | 12 | 57 | 2 | 10 |
| 0.3 | 7 | 206 | 0 | 60 |
| 0.2 | 4 | 568 | 0 | 181 |

It still does not remove wrong links efficiently: cutting wrong links on held-out train from 11 to 7 costs 192 extra unreported links.
At 0.5, wrong links rise slightly, because dropping some links lets the assignment reshuffle others.
The remaining wrong links are confident: their absolute cost is as low as a true link's.
A better abstention signal would be ambiguity, for example the margin between a source's best and second-best candidate, rather than absolute cost.
This sweep is a report, not a tuning step, and the no-match cost stays at 1.0.
The operating threshold should be chosen on validation, as the challenge requires.

## Limitations

- The weights, the 10 s velocity window, the scales and the no-match cost of 1.0 were all chosen from training labels by hand, without a systematic search.
- The no-match cost is barely exercised: the last tracklet of each aircraft has no later candidates, so it is correctly left unlinked because nothing starts after it ends.
- Plausibility compares only distances, and favours longer gaps slightly. Direction comes in through the heading cosine on mean positions, not through a miss distance at the predicted point.
- The dt penalty is absolute, so it cannot be raised to stop skips without also penalising legitimately long gaps. A penalty relative to the source's shortest available gap would avoid that.
- Velocity covariance still reflects feed noise more than aircraft motion, which is why the velocity Wasserstein weight had to be cut.
- The staged-demo snapshots have not been run.

## Reproduce

```bash
uv run python -m aircraft_track_association candidate-packet/data/train/scenario_*
uv run python -m aircraft_track_association candidate-packet/data/validation/scenario_*
uv run python -m aircraft_track_association candidate-packet/data/train/scenario_017  # per-miss diagnostics
```
