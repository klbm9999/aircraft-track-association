"""Decision settings for the association heuristic: cost-term scales, weights and the
no-match threshold. Kept apart from the scoring code so tuning changes touch only this file.

Cost of linking source -> target = sum(WEIGHTS[k] * term[k] / SCALES[k]); see associate.py
for the term definitions. Tuning history and the effect of each change are in REPORT.md.
"""

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
# Tuned by hand on train scenarios 002-011 (see REPORT.md). Heading and altitude separate
# aircraft flying in parallel, where the other terms nearly tie.
WEIGHTS = {
    "plausibility": 0.35,  # |log(actual gap / projected gap)|
    "heading": 3.0,  # 1 - cos(source mean velocity, source->target mean position offset)
    "vel_cos": 0.15,  # 1 - cos(source mean velocity, target mean velocity)
    "vel_w2": 0.05,  # Wasserstein-2 between velocity Gaussians
    "pos_w2": 0.03,  # Wasserstein-2 between horizontal position Gaussians
    "alt_w2": 0.3,  # Wasserstein-2 between altitude Gaussians
    "dt": 0.15,  # gap length, so a link that skips an intermediate tracklet costs more
}
NO_MATCH_COST = 1.0  # a pair is linked only if its weighted cost is below this
