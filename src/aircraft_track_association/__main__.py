"""Run association on one scenario and report link metrics.

Usage: python -m aircraft_track_association <scenario_dir>
"""

import sys
from pathlib import Path

from .associate import WEIGHTS, cost_matrix, match
from .evaluate import link_metrics
from .io import load_observations, load_successors
from .track import build_tracklets


def main(scenario_dir: Path) -> None:
    tracklets = build_tracklets(load_observations(scenario_dir))
    predicted = match(tracklets)
    truth = load_successors(scenario_dir)  # evaluation only, never an inference input
    m = link_metrics(predicted, truth)
    print(f"{scenario_dir}: {len(tracklets)} tracklets")
    print(
        f"links  correct {m['correct_links']}/{m['true_links']}  predicted {m['predicted_links']}"
    )
    print(f"precision {m['precision']:.3f}  recall {m['recall']:.3f}  f1 {m['f1']:.3f}")
    print(f"no-successor correct {m['correct_no_successor']}/{m['no_successor']}")

    index = {t.id: i for i, t in enumerate(tracklets)}
    cost, terms = cost_matrix(tracklets)
    short = lambda tid: tid[9:17] if tid else "-"
    for src, dst in sorted(truth.items()):
        got = predicted.get(src)
        if got == dst:
            continue
        print(f"\nMISS {short(src)}: predicted {short(got)}, true {short(dst)}")
        for label, tid in (("predicted", got), ("true", dst)):
            if tid:
                pt = terms.get((index[src], index[tid]))
                detail = " ".join(f"{k}={pt[k]:.3g}" for k in WEIGHTS) if pt else "gated"
                print(f"  {label:9} cost={cost[index[src], index[tid]]:.3f} {detail}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
