"""Decision settings for the association heuristic: cost-term scales, weights and the
no-match threshold. Kept apart from the scoring code so tuning changes touch only this file.

Cost of linking source -> target = sum(WEIGHTS[k] * term[k] / SCALES[k]); see associate.py
for the term definitions. Tuning history and the effect of each change are in REPORT.md.
"""

import os

# Each term is divided by a fixed scale (roughly its median over scenario_001's gated pairs)
# so costs, and the no-match threshold, mean the same thing in every scenario.
SCALES = {
    "plausibility": 0.25,
    "heading": 0.02,
    "vel_cos": 0.005,
    "vel_w2": 36.0,  # m/s
    "pos_w2": 56_000.0,  # m
    "alt_w2": 700.0,  # m
    "dt": 225.0,  # s
}
# Named weight sets; WEIGHTS is the active one. Choose another with ATA_WEIGHT_SET=<name>.
# Term meanings (see associate.pair_terms):
#   plausibility  |log(actual gap / projected gap)|
#   heading       1 - cos(source mean velocity, source->target mean position offset)
#   vel_cos       1 - cos(source mean velocity, target mean velocity)
#   vel_w2        Wasserstein-2 between velocity Gaussians
#   pos_w2        Wasserstein-2 between horizontal position Gaussians
#   alt_w2        Wasserstein-2 between altitude Gaussians
#   dt            gap length, so a link that skips an intermediate tracklet costs more
WEIGHT_SETS = {
    # Tuned by hand on train scenarios 002-011 (see REPORT.md). Heading and altitude separate
    # aircraft flying in parallel, where the other terms nearly tie.
    "train_hand_tuned": {
        "plausibility": 0.35,
        "heading": 3.0,
        "vel_cos": 0.15,
        "vel_w2": 0.05,
        "pos_w2": 0.03,
        "alt_w2": 0.3,
        "dt": 0.15,
    },
    # Random search on the validation partition (tune.py, 2000 trials, seed 0), minimising
    # 2 * wrong + unresolved links, ties broken by the larger mean margin.
    "validation_search": {
        "plausibility": 0.2474,
        "heading": 0.6202,
        "vel_cos": 0.0116,
        "vel_w2": 0.0464,
        "pos_w2": 0.0109,
        "alt_w2": 1.8751,
        "dt": 0.0115,
    },
}
WEIGHT_SET = os.environ.get("ATA_WEIGHT_SET", "train_hand_tuned")
if WEIGHT_SET not in WEIGHT_SETS:
    raise ValueError(f"ATA_WEIGHT_SET must be one of {sorted(WEIGHT_SETS)}, got {WEIGHT_SET!r}")
WEIGHTS = WEIGHT_SETS[WEIGHT_SET]
NO_MATCH_COST = 1.0  # a pair is linked only if its weighted cost is below this
