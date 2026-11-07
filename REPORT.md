# Track association results

## Summary

Three approaches link tracklets inside each scenario.
The constant-velocity baseline, scored here under the thinning study, gets 64 of 339 test links right (135 wrong, 140 unresolved, precision 0.322, recall 0.189).
The Gaussian continuity model is the frozen method.
With the hand-tuned weights it reaches micro F1 0.985 on test (333 of 339 correct, 4 wrong, 2 unresolved), 0.993 on validation (1 wrong link) and 0.986 on train.
On the 69 train scenarios that played no part in tuning, its F1 is 0.985, with 11 wrong links.
The learned classifier reaches precision 1.000 and recall 0.965 on test (327 of 339 correct, 0 wrong, 12 unresolved) and precision 1.000 and recall 0.976 on validation (0 wrong, 8 unresolved).
The Gaussian model's weights were tuned on training data only.
Every approach is fit on training data only, with no leakage.
No tracklet without a labelled successor was given one.
That is all 480 train, 120 validation and 120 test tracklets that have no successor.

## Gaussian continuity model

A hand-tuned cost on kinematic Gaussians, solved as an assignment.
The method and every setting were frozen at commit 5efa814 before the test labels were read for the first time.
No change was made after seeing test results.
The reported weight set is `train_hand_tuned`.

### Method

Each scenario is processed independently, and labels are read only for scoring.

1. **Load.** Identical retransmissions are collapsed by `observation_id`, and supporting records by `record_id`.
   Conflicting records that share an ID, out-of-range coordinates and missing required fields raise an error.
2. **Project.** Positions go from EPSG:4326 to a local azimuthal equidistant plane centred on the scenario, in metres.
   EPSG:3857 was rejected because it inflates distances by about 22% at 35N.
3. **Altitude.** Altitude is `altitude_ft * 0.3048` when reported, else the supporting record's `a1` (see below), else the tracklet mean.
4. **Near-duplicates.** Within a tracklet, a report from the same feed within 0.5 s and 50 m of the previous kept report is dropped.
5. **Tracklet model.** Each tracklet gets three Gaussians: horizontal position $(x, y)$, altitude $z$ and 3D velocity.
   Velocity samples span at least 10 s, because position noise of about 200 m over sub-second report gaps otherwise produces velocities above 500 m/s.
   Start and end states come from straight-line fits over the first and last 6 reports.
6. **Pair costs.** Every source-to-target pair whose target starts after the source ends is scored.
   Pairs that overlap in time get infinite cost.
   Term $k$ contributes $w_k \cdot r_k / s_k$, with raw value $r_k$, fixed scale $s_k$ and weight $w_k$ (`src/aircraft_track_association/weights.py`).
7. **Assignment.** A Hungarian solve over a $2n \times 2n$ matrix gives each tracklet at most one successor and one predecessor.
   A source stays unlinked when every candidate costs more than 1.0.

$\cos(\mathbf{a}, \mathbf{b})$ is the cosine of the angle between the two vectors.
$d$ is the distance from the source end position to the target start position.
$v_{\mathrm{end}}$ is the source end-state speed.
$\Delta t$ is the target start time minus the source end time.
$\mathbf{v}_s^{xy}$ is the horizontal part of the source mean velocity.
$\bar{\mathbf{p}}_s^{xy}$ and $\bar{\mathbf{p}}_t^{xy}$ are the horizontal mean positions.
$\mathbf{v}_s$ and $\mathbf{v}_t$ are the mean velocities.
$W_2$ is the 2-Wasserstein distance.
$\mu^z$ and $\sigma^z$ are the altitude mean and standard deviation.
Scales are roughly the medians of each term over scenario_001's candidate pairs, and they stay fixed, so a cost means the same thing in every scenario.
The weights below are `train_hand_tuned`.

| Term | Formula | Scale | Weight |
| --- | --- | ---: | ---: |
| plausibility | $\left\lvert \log \dfrac{d}{v_{\mathrm{end}} \, \Delta t} \right\rvert$ | 0.25 | 0.35 |
| heading | $1 - \cos(\mathbf{v}_s^{xy},\, \bar{\mathbf{p}}_t^{xy} - \bar{\mathbf{p}}_s^{xy})$ | 0.02 | 3.0 |
| vel_cos | $1 - \cos(\mathbf{v}_s, \mathbf{v}_t)$ | 0.005 | 0.15 |
| vel_w2 | $W_2$ between the velocity Gaussians | 36 m/s | 0.05 |
| pos_w2 | $W_2$ between the horizontal position Gaussians | 56 km | 0.03 |
| alt_w2 | $\sqrt{(\mu_s^z - \mu_t^z)^2 + (\sigma_s^z - \sigma_t^z)^2}$ | 700 m | 0.3 |
| dt | $\Delta t$ | 225 s | 0.15 |

#### Supporting altitude (`a1`)

`a1` in `supporting.jsonl` is undocumented and is read as altitude in metres.
On the 10,194 train observations that also report `altitude_ft`, it agrees with `altitude_ft * 0.3048` with correlation 0.999, median ratio 1.000 and at most 162 m difference, which is normal measurement noise.
It is null in about 12% of records.
Every observation has at most one supporting record.
The `b4` flag does not change its agreement.
It fills 54% of missing altitudes (3,712 of 6,875 across train and validation).
On its own, before the altitude cost existed, it changed no prediction, because altitude then entered the cost only through terms that hardly weighed it.
The other supporting fields (`spd`, `b4`, `rx`) and the optional observation `attributes` are not used.

### Settings

The Gaussian weights were tuned on training data only.
Tuning was manual, on train scenarios 002-011, and the objective was micro F1.
Scenario 001 set the scales and the 10 s velocity window.
Each pass inspected the cost terms of the misses and changed the weight behind the largest pattern.
The weight order plausibility > vel_w2 > pos_w2 is kept.
Raw weights are not directly comparable across terms, because the scales differ.

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

Rows above "Horizontal-only position" used a 3D position Gaussian and a 3D heading offset.
From that row on, both are 2D and altitude has its own term.
Iteration 1 changed no prediction on the 10 tuning scenarios.
Iteration 2 raised the heading weight from 0.15 to 3.0.
Altitude weights 0.1, 0.3 and 0.6 tie on the tuning set (166/168 correct, F1 0.991).
0.3 was kept.
On true links the altitude distance is at most 209 m in train 001-011, while the median over other candidate pairs is 781 m, so a 400 m mismatch at weight 0.3 costs about 0.17.
Raising dt to 0.6 cut held-out train F1 from 0.973 to 0.959 and validation F1 from 0.988 to 0.966, and unreported links on held-out train rose from 14 to 39-48.
Turning dt off left tuning F1 at 0.958 and validation F1 at 0.984.
The kept dt weight is 0.15.
On 872 train sources the true link had the lower plausibility cost than the skip-by-one link only 34% of the time.

The hand-tuned weights are good enough to stay frozen, and they remain the default.
A second set, `validation_search`, is recorded beside them.
It came from 2000 log-uniform trials of the seven weights on the validation partition (each weight drawn from [0.01, 10], seed 0), rejecting draws that break plausibility > vel_w2 > pos_w2.
Trial 0 was the hand-tuned set.
Scales, the 10 s velocity window, endpoint fits and the no-match cost 1.0 were fixed.
The objective was $2 \times$ wrong links $+$ unresolved links, and ties went to the largest mean margin.
The margin of a true link is the cost of the source's cheapest other candidate minus the true link's cost, divided by the no-match cost and capped at 10.
32 of 2000 trials reached objective 0, and 67 beat the hand-tuned objective of 5 (1 wrong, 3 unresolved, mean margin 0.668).
Among the zero-error trials, heading ranged from 0.012 to 2.567 and altitude from 0.047 to 1.875, so validation did not pin the weights down.
The tie-break picked trial 244 (mean margin 0.959; the next were 0.910 and 0.822).
Set `ATA_WEIGHT_SET=validation_search` to use that set.

```bash
uv run python -m aircraft_track_association tune candidate-packet/data/validation/scenario_* --trials 2000 --seed 0
```

| Term | train_hand_tuned | validation_search |
| --- | ---: | ---: |
| plausibility | 0.35 | 0.2474 |
| heading | 3.0 | 0.6202 |
| vel_cos | 0.15 | 0.0116 |
| vel_w2 | 0.05 | 0.0464 |
| pos_w2 | 0.03 | 0.0109 |
| alt_w2 | 0.3 | 1.8751 |
| dt | 0.15 | 0.0115 |

| Weight set | Partition | Correct / true links | Wrong | Unresolved | Precision | Recall |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| train_hand_tuned | Train (80) | 1337 / 1364 | 12 | 15 | 0.991 | 0.980 |
| train_hand_tuned | Validation (20) | 331 / 335 | 1 | 3 | 0.997 | 0.988 |
| train_hand_tuned | Test (20) | 333 / 339 | 4 | 2 | 0.988 | 0.982 |
| validation_search | Train (80) | 1356 / 1364 | 7 | 1 | 0.995 | 0.994 |
| validation_search | Validation (20) | 335 / 335 | 0 | 0 | 1.000 | 1.000 |
| validation_search | Test (20) | 337 / 339 | 2 | 0 | 0.994 | 0.994 |

The validation figure for `validation_search` is optimistic by construction, because several of the 2000 trials had zero validation errors.
Test was run once for each set after both were fixed.

### Results

Metrics count exact directed successor links, micro-averaged over scenarios.
"Wrong" is a reported link that is not the labelled successor.
"Unresolved" (also called "Unreported" in the development table) is a labelled link whose source was left with no link at all, and the unresolved proportion divides that count by the number of tracklets that have a labelled successor.
A true link lost because its source got a wrong link is counted once, as wrong.

| Partition | Scenarios | True links | Accepted | Correct | Wrong | Unresolved | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 80 | 1364 | 1349 | 1337 | 12 | 15 (1.1%) | 0.991 | 0.980 | 0.986 |
| Validation | 20 | 335 | 332 | 331 | 1 | 3 (0.9%) | 0.997 | 0.988 | 0.993 |
| Test | 20 | 339 | 337 | 333 | 4 | 2 (0.6%) | 0.988 | 0.982 | 0.985 |

Train includes the 11 scenarios used for tuning.
Test errors sit in three scenarios: 014 (3 wrong links, F1 0.824), 018 (1 wrong, 1 unresolved) and 010 (1 unresolved).

| Partition | Scenarios | True links | Correct | Wrong | Unreported | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Train, used for tuning (001-011) | 11 | 186 | 184 | 1 | 1 | 0.995 | 0.989 | 0.992 |
| Train, not used for tuning (012-080) | 69 | 1178 | 1153 | 11 | 14 | 0.991 | 0.979 | 0.985 |
| Train, all | 80 | 1364 | 1337 | 12 | 15 | 0.991 | 0.980 | 0.986 |
| Validation | 20 | 335 | 331 | 1 | 3 | 0.997 | 0.988 | 0.993 |

#### Thinned reports

Each tracklet keeps a random fraction of its unique reports (at least 3), and supporting records of removed reports are dropped.
The draw is seeded per tracklet from the seed and the tracklet id.
Tracklets and labels stay unchanged, and nothing was retuned.
Single-seed results move by a few links between seeds.
The 50% test run for `validation_search` beats the unthinned run by chance.
The 25% case was therefore repeated with seeds 0-4.

```bash
uv run python -m aircraft_track_association stress candidate-packet/data/test/scenario_* --keep 0.25 --seed 0
```

| Approach / weight set | Partition | Reports kept | Correct / true links | Wrong | Unresolved | Precision | Recall |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | Validation | 100% | 68 / 335 | 139 | 128 | 0.329 | 0.203 |
| Baseline | Validation | 50% | 95 / 335 | 132 | 108 | 0.419 | 0.284 |
| Baseline | Validation | 25% | 110 / 335 | 131 | 94 | 0.456 | 0.328 |
| Baseline | Test | 100% | 64 / 339 | 135 | 140 | 0.322 | 0.189 |
| Baseline | Test | 50% | 82 / 339 | 142 | 115 | 0.366 | 0.242 |
| Baseline | Test | 25% | 92 / 339 | 146 | 101 | 0.387 | 0.271 |
| Gaussian, train_hand_tuned | Validation | 100% | 331 / 335 | 1 | 3 | 0.997 | 0.988 |
| Gaussian, train_hand_tuned | Validation | 50% | 330 / 335 | 2 | 3 | 0.994 | 0.985 |
| Gaussian, train_hand_tuned | Validation | 25% | 323 / 335 | 4 | 8 | 0.988 | 0.964 |
| Gaussian, train_hand_tuned | Test | 100% | 333 / 339 | 4 | 2 | 0.988 | 0.982 |
| Gaussian, train_hand_tuned | Test | 50% | 331 / 339 | 6 | 2 | 0.982 | 0.976 |
| Gaussian, train_hand_tuned | Test | 25% | 319 / 339 | 9 | 12 | 0.973 | 0.941 |
| Gaussian, validation_search | Validation | 100% | 335 / 335 | 0 | 0 | 1.000 | 1.000 |
| Gaussian, validation_search | Validation | 50% | 333 / 335 | 1 | 1 | 0.997 | 0.994 |
| Gaussian, validation_search | Validation | 25% | 332 / 335 | 1 | 2 | 0.997 | 0.991 |
| Gaussian, validation_search | Test | 100% | 337 / 339 | 2 | 0 | 0.994 | 0.994 |
| Gaussian, validation_search | Test | 50% | 339 / 339 | 0 | 0 | 1.000 | 1.000 |
| Gaussian, validation_search | Test | 25% | 333 / 339 | 3 | 3 | 0.991 | 0.982 |

| Gaussian weight set | Partition | Mean correct | Mean wrong | Mean unresolved | Wrong / unresolved per seed |
| --- | --- | ---: | ---: | ---: | --- |
| train_hand_tuned | Validation | 322.6 / 335 | 4.2 | 8.2 | 4/8, 3/7, 4/10, 4/10, 6/6 |
| train_hand_tuned | Test | 321.2 / 339 | 7.6 | 10.4 | 9/12, 11/11, 8/10, 5/9, 5/10 |
| validation_search | Validation | 333.2 / 335 | 0.6 | 1.2 | 1/2, 1/2, 0/0, 0/0, 1/2 |
| validation_search | Test | 331.6 / 339 | 4.8 | 2.6 | 3/3, 5/3, 6/3, 3/1, 7/3 |

Keeping a quarter of the reports roughly doubles wrong links on test (4 to 7.6 on average for the hand-tuned set, 2 to 4.8 for the searched set).
Unresolved links rise more for the hand-tuned set (2 to 10.4) than for the searched set (0 to 2.6).
Precision stays above 0.97 for both sets, so fewer reports mostly become withheld links.
The baseline improves under thinning.
A likely cause is that fewer reports leave fewer closely spaced pairs at the end of the source, so its last speed is less noisy.
Its precision stays below 0.46.

### Errors

Counts below use `train_hand_tuned`.

| Partition | Wrong aircraft | Skipped intermediate | Left unlinked | False link from last tracklet |
| --- | ---: | ---: | ---: | ---: |
| Train, used for tuning | 0 | 1 | 1 | 0 |
| Train, not used for tuning | 8 | 3 | 14 | 0 |
| Validation | 0 | 1 | 3 | 0 |

The altitude cost cut wrong-aircraft links on held-out train from 22 to 8 and on validation from 2 to 0.
Unreported links are the largest error group, then wrong-aircraft links, then skip links.
The weakest train scenarios are 017 (F1 0.727), 046 (0.875) and 073 (0.889).
They have not been inspected yet.
A wrong link asserts a false identity, so it is worse than leaving a true link unreported.
On held-out train the weights make 11 wrong links and 14 unreported links, and on validation 1 wrong and 3 unreported.

Lowering the no-match cost makes the solver abstain more often.
This sweep is a report, and the no-match cost stays at 1.0.

| No-match cost | Train 012-080 wrong | Train 012-080 unreported | Validation wrong | Validation unreported |
| ---: | ---: | ---: | ---: | ---: |
| 1.0 (current) | 11 | 14 | 1 | 3 |
| 0.5 | 12 | 57 | 2 | 10 |
| 0.3 | 7 | 206 | 0 | 60 |
| 0.2 | 4 | 568 | 0 | 181 |

Cutting wrong links on held-out train from 11 to 7 adds 192 unreported links.
At 0.5, wrong links rise from 11 to 12, because dropping some links lets the assignment reshuffle others.
The remaining wrong links are confident: their absolute cost is as low as a true link's.
A better abstention signal would be the margin between a source's best and second-best candidate.

#### Test scenario 014

With `train_hand_tuned`, test scenario 014 has 3 of the 4 wrong test links (14 of 17 correct).
With `validation_search`, all 17 links in this scenario are correct.
Call the tracklets A (`d89bbd46`), B (`3c93ea1c`), C (`6083b6f9`), D (`dabe035c`) and E (`feb43402`).
The labelled links are B to C, C to E and A to D.
The reported links are A to C, B to E and C to D.
The three reported links are one cycle: each source receives the labelled successor of another source in the cycle.

Each table compares the reported successor with the labelled one.
"Raw" is the unweighted term and "Weighted" is $w \cdot r / s$.
Lower is better.

##### B: reported E, labelled C

Labelled successor C was assigned to A.

| Term | Raw, reported | Raw, labelled | Weighted, reported | Weighted, labelled |
| --- | ---: | ---: | ---: | ---: |
| plausibility | 0.0381 | 0.0441 | 0.053 | 0.062 |
| heading | 0.000172 | 0.0000923 | 0.026 | 0.014 |
| vel_cos | 0.000553 | 0.000254 | 0.017 | 0.008 |
| vel_w2 (m/s) | 32.4 | 34.0 | 0.045 | 0.047 |
| pos_w2 (m) | 66,630 | 33,610 | 0.036 | 0.018 |
| alt_w2 (m) | 87.1 | 15.4 | 0.037 | 0.007 |
| dt (s) | 336 | 123 | 0.224 | 0.082 |
| **Total** | | | **0.438** | **0.237** |

##### A: reported C, labelled D

Labelled successor D was assigned to C.

| Term | Raw, reported | Raw, labelled | Weighted, reported | Weighted, labelled |
| --- | ---: | ---: | ---: | ---: |
| plausibility | 0.0572 | 0.0509 | 0.080 | 0.071 |
| heading | 0.0000124 | 0.000132 | 0.002 | 0.020 |
| vel_cos | 0.000389 | 0.00318 | 0.012 | 0.095 |
| vel_w2 (m/s) | 8.26 | 35.1 | 0.011 | 0.049 |
| pos_w2 (m) | 31,890 | 61,360 | 0.017 | 0.033 |
| alt_w2 (m) | 272.5 | 169.5 | 0.117 | 0.073 |
| dt (s) | 117 | 325 | 0.078 | 0.217 |
| **Total** | | | **0.317** | **0.558** |

##### C: reported D, labelled E

Labelled successor E was assigned to B.

| Term | Raw, reported | Raw, labelled | Weighted, reported | Weighted, labelled |
| --- | ---: | ---: | ---: | ---: |
| plausibility | 0.0088 | 0.0133 | 0.012 | 0.019 |
| heading | 0.0000123 | 0.000938 | 0.002 | 0.141 |
| vel_cos | 0.00164 | 0.00140 | 0.049 | 0.042 |
| vel_w2 (m/s) | 31.1 | 11.1 | 0.043 | 0.015 |
| pos_w2 (m) | 29,480 | 33,020 | 0.016 | 0.018 |
| alt_w2 (m) | 440.8 | 98.4 | 0.189 | 0.042 |
| dt (s) | 110 | 115 | 0.073 | 0.077 |
| **Total** | | | **0.384** | **0.353** |

Across the chain, the reported links cost $0.438 + 0.317 + 0.384 = 1.139$, and the labelled links cost $0.237 + 0.558 + 0.353 = 1.148$.
The solver's choice is cheaper by 0.009.

#### Limitations

- The weights, the 10 s velocity window, the scales and the no-match cost of 1.0 were chosen from training labels by hand.
- The no-match cost is barely exercised: the last tracklet of each aircraft has no later candidates, so it stays unlinked because nothing starts after it ends.
- Plausibility compares distances only and favours longer gaps slightly.
  Direction enters through the heading cosine on mean positions.
- The dt penalty is absolute, so a higher weight also penalises legitimately long gaps.
  A penalty relative to the source's shortest available gap would avoid that.
- Velocity covariance still reflects feed noise more than aircraft motion, which is why the velocity Wasserstein weight is 0.05.
- The staged-demo snapshots have not been run.

#### Reproduce

```bash
uv run python -m aircraft_track_association evaluate candidate-packet/data/train/scenario_* --approach gaussian
uv run python -m aircraft_track_association evaluate candidate-packet/data/validation/scenario_* --approach gaussian
uv run python -m aircraft_track_association evaluate candidate-packet/data/train/scenario_017 --approach gaussian  # per-miss diagnostics
```

## Learned classifier

The author chose a gradient-boosted classifier as the strategy.
Claude Code built and trained the rest.
The classifier is fit on training candidate pairs only.
Test was run once.

### Method

Each candidate is a later tracklet that starts strictly after the source ends.
The features are the gap, the straight-line distance, the projected miss and its along-track and cross-track parts, and the miss divided by a gap allowance fit on train true links.
They also include end and start speeds and their difference, heading change, implied straight speed, and counts of earlier candidates and earlier plausible candidates.
Further features are the altitude difference, reported speed and heading differences, reported speed versus kinematic speed, a reference-code match that ignores the placeholder `C0FFEE`, and the low-quality report fraction.
`altitude_missing_count` did not vary after altitude fill, so it was dropped.

### Settings

The model is a `HistGradientBoostingClassifier` with learning rate 0.08, 200 iterations, depth 4, minimum 20 samples per leaf, L2 0.1 and seed 42.
Greedy selection keeps a link when its score is at least 0.05 and its margin over the runner-up is at least 0.20.
Those two thresholds were chosen on validation to maximise recall subject to precision at least 0.95.
All three profiles below met that precision floor.
The operating profile is the one in use.

| Profile | Rule | Minimum score | Minimum margin | Precision | Recall | Wrong | Unresolved |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Conservative | mutual best | 0.05 | 0 | 0.997 | 0.973 | 1 | 8 |
| Operating | greedy | 0.05 | 0.20 | 1.000 | 0.976 | 0 | 8 |
| Permissive | greedy | 0.05 | 0 | 0.997 | 0.973 | 1 | 8 |

The table is the validation trade-off from `artifacts/learned.json`.

### Results

| Partition | Correct / true links | Wrong | Unresolved | Precision | Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Train (80) | 1364 / 1364 | 0 | 0 | 1.000 | 1.000 |
| Validation (20) | 327 / 335 | 0 | 8 | 1.000 | 0.976 |
| Test (20) | 327 / 339 | 0 | 12 | 1.000 | 0.965 |

### Errors

The operating rule makes no wrong link on train, validation or test.
The misses are unresolved links: 0 on train, 8 on validation and 12 on test.
Train is fit perfectly (1364 of 1364).

## Baseline

The constant-velocity baseline links a source to a later tracklet from the source's last position and speed.
Its full description is in the README.
This report scores it only inside the thinning study, with the same labels and no retuning.
At full reports it gets 68 of 335 validation links right (139 wrong, 128 unresolved, precision 0.329, recall 0.203) and 64 of 339 test links right (135 wrong, 140 unresolved, precision 0.322, recall 0.189).
At 25% of reports, validation precision is 0.456 and test precision is 0.387.
The row-level counts are in the Gaussian thinning table above.
