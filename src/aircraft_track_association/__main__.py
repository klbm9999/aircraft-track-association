"""Command line entry point: associate tracklets in one or more scenarios and report metrics.

Usage: python -m aircraft_track_association <scenario_dir> [<scenario_dir> ...]

With one directory it prints the metrics plus, for every link it got wrong or left out, the
cost and raw terms of both the predicted and the true successor. With several it prints one
line per scenario and micro-averaged totals. Labels are read only after prediction, to score.
"""

import sys
from pathlib import Path

from .associate import WEIGHTS, cost_matrix, match
from .evaluate import link_metrics
from .io import load_successors
from .track import load_tracklets

COUNTS = ("correct_links", "wrong_links", "unreported_links", "predicted_links", "true_links")


def main(scenario_dir: Path) -> None:
    tracklets = load_tracklets(scenario_dir)
    predicted = match(tracklets)
    truth = load_successors(scenario_dir)  # evaluation only, never an inference input
    m = link_metrics(predicted, truth)
    print(f"{scenario_dir}: {len(tracklets)} tracklets")
    print(
        f"links  correct {m['correct_links']}/{m['true_links']}  wrong {m['wrong_links']}  "
        f"unreported {m['unreported_links']}"
    )
    print(f"precision {m['precision']:.3f}  recall {m['recall']:.3f}  f1 {m['f1']:.3f}")
    print(f"no-successor correct {m['correct_no_successor']}/{m['no_successor']}")

    # Diagnostics: for each mistake, show why the predicted target beat the true one.
    index = {t.id: i for i, t in enumerate(tracklets)}
    cost, terms = cost_matrix(tracklets)
    short = lambda tid: tid[9:17] if tid else "-"  # first UUID block is enough to tell apart
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


def summary(scenario_dirs: list[Path]) -> None:
    totals = dict.fromkeys(COUNTS, 0)
    for d in scenario_dirs:
        m = link_metrics(match(load_tracklets(d)), load_successors(d))
        for k in totals:
            totals[k] += m[k]
        print(
            f"{d.name}  correct {m['correct_links']:2}/{m['true_links']:2}  "
            f"wrong {m['wrong_links']:2}  unreported {m['unreported_links']:2}  "
            f"p {m['precision']:.3f}  r {m['recall']:.3f}  f1 {m['f1']:.3f}"
        )
    # Micro average: pool link counts over scenarios, then compute the ratios once.
    p = totals["correct_links"] / totals["predicted_links"]
    r = totals["correct_links"] / totals["true_links"]
    print(
        f"micro ({len(scenario_dirs)} scenarios)  wrong {totals['wrong_links']}  "
        f"unreported {totals['unreported_links']}  "
        f"p {p:.3f}  r {r:.3f}  f1 {2 * p * r / (p + r):.3f}"
    )


if __name__ == "__main__":
    dirs = [Path(a) for a in sys.argv[1:]]
    main(dirs[0]) if len(dirs) == 1 else summary(dirs)
