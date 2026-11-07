"""Command line entry point.

    python -m aircraft_track_association evaluate <scenario_dir> [...] [--approach NAME]
    python -m aircraft_track_association analyze <scenario_dir> [-o result.json]
    python -m aircraft_track_association stress <scenario_dir> [...] [--keep 0.5] [--seed 0]
    python -m aircraft_track_association tune <scenario_dir> [...] [--trials 2000] [--seed 0]
    python -m aircraft_track_association train <train_dir> [...] --validation <val_dir> [...]
    python -m aircraft_track_association correlate <train_dir> [...] [-o heatmap.png]
    python -m aircraft_track_association serve [--host 127.0.0.1] [--port 8000]

evaluate: run association and score it against labels. With one directory it also prints,
    for every wrong or missing link, the decision reason and both candidates' measurements.
    With several it prints one line per scenario and micro-averaged totals.
analyze: run every approach on one snapshot and print or save the full result (inputs hash,
    model version, configuration, per-tracklet decisions). Labels are not read.
stress: evaluate after thinning each tracklet to a fraction of its reports (see stress.py),
    with the same settings and no retuning.
tune: random search for Gaussian-model weights against labels (see tune.py); prints the best
    trials. It does not change weights.py.
train: fit the learned model on train and choose its thresholds on validation. It does not
    read test.
correlate: write a heatmap of the correlation between the learned training features.
serve: start the HTTP service and web interface.
"""

import argparse
import json
from pathlib import Path

from .decision import links, short
from .evaluate import link_metrics
from .io import load_successors
from .pipeline import APPROACHES, analyze, analyze_dir, read_snapshot

COUNTS = ("correct_links", "wrong_links", "unreported_links", "predicted_links", "true_links")


def explain(scenario_dir: Path, approach: str) -> None:
    result = analyze_dir(scenario_dir)
    records = {r["tracklet_id"]: r for r in result["results"][approach]}
    truth = load_successors(scenario_dir)  # evaluation only, never an inference input
    m = link_metrics(links(records.values()), truth)
    print(f"{scenario_dir} [{approach}]: {len(records)} tracklets")
    print(
        f"links  correct {m['correct_links']}/{m['true_links']}  wrong {m['wrong_links']}  "
        f"unreported {m['unreported_links']}"
    )
    print(f"precision {m['precision']:.3f}  recall {m['recall']:.3f}  f1 {m['f1']:.3f}")
    for src, dst in sorted(truth.items()):
        rec = records[src]
        if rec["successor"] == dst:
            continue
        print(f"\nMISS {short(src)}: predicted {short(rec['successor'])}, true {short(dst)}")
        print(f"  reason: {rec['reason']}")
        for c in rec["candidates"]:
            mark = "true" if c["tracklet_id"] == dst else ""
            detail = " ".join(f"{k}={v:.3g}" for k, v in c["measurements"].items())
            print(f"  {short(c['tracklet_id'])} {mark:4} score={c['score']:.3f} {detail}")


def evaluate(scenario_dirs: list[Path], approaches: list[str], transform=None) -> None:
    """transform(observations, supporting) -> (observations, supporting) alters the inputs."""
    totals = {a: dict.fromkeys(COUNTS, 0) for a in approaches}
    for d in scenario_dirs:
        snap = read_snapshot(d)
        if transform:
            snap["observations"], snap["supporting"] = transform(
                snap["observations"], snap["supporting"]
            )
        result, truth = analyze(**snap), load_successors(d)
        for a in approaches:
            m = link_metrics(links(result["results"][a]), truth)
            for k in COUNTS:
                totals[a][k] += m[k]
            print(
                f"{d.name} {a:8}  correct {m['correct_links']:2}/{m['true_links']:2}  "
                f"wrong {m['wrong_links']:2}  unreported {m['unreported_links']:2}  "
                f"p {m['precision']:.3f}  r {m['recall']:.3f}  f1 {m['f1']:.3f}"
            )
    for a, t in totals.items():
        # Micro average: pool link counts over scenarios, then compute the ratios once.
        p = t["correct_links"] / t["predicted_links"] if t["predicted_links"] else float("nan")
        r = t["correct_links"] / t["true_links"]
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        print(
            f"micro {a:8} ({len(scenario_dirs)} scenarios)  correct {t['correct_links']}/"
            f"{t['true_links']}  wrong {t['wrong_links']}  unreported {t['unreported_links']}  "
            f"p {p:.3f}  r {r:.3f}  f1 {f1:.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m aircraft_track_association")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate", help="score association against labels")
    ev.add_argument("scenario_dirs", nargs="+", type=Path)
    ev.add_argument("--approach", choices=list(APPROACHES), help="default: all approaches")
    an = sub.add_parser("analyze", help="run association on one snapshot, no labels")
    an.add_argument("scenario_dir", type=Path)
    an.add_argument("-o", "--output", type=Path, help="write the result JSON here")
    st = sub.add_parser("stress", help="evaluate after thinning reports")
    st.add_argument("scenario_dirs", nargs="+", type=Path)
    st.add_argument("--keep", type=float, default=0.5, help="fraction of reports kept")
    st.add_argument("--seed", type=int, default=0)
    st.add_argument("--approach", choices=list(APPROACHES), help="default: all approaches")
    tu = sub.add_parser("tune", help="search Gaussian-model weights against labels")
    tu.add_argument("scenario_dirs", nargs="+", type=Path)
    tu.add_argument("--trials", type=int, default=2000)
    tu.add_argument("--seed", type=int, default=0)
    tr = sub.add_parser("train", help="fit the learned model; does not read test")
    tr.add_argument("train_dirs", nargs="+", type=Path)
    tr.add_argument("--validation", nargs="+", required=True, type=Path)
    tr.add_argument("-o", "--output", type=Path)
    co = sub.add_parser("correlate", help="heatmap of learned training-feature correlations")
    co.add_argument("train_dirs", nargs="+", type=Path)
    co.add_argument("-o", "--output", type=Path)
    sv = sub.add_parser("serve", help="start the HTTP service and web interface")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "evaluate":
        if len(args.scenario_dirs) == 1:
            for a in [args.approach] if args.approach else APPROACHES:
                explain(args.scenario_dirs[0], a)
        else:
            evaluate(args.scenario_dirs, [args.approach] if args.approach else list(APPROACHES))
    elif args.command == "analyze":
        text = json.dumps(analyze_dir(args.scenario_dir), indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n")
        else:
            print(text)
    elif args.command == "stress":
        from .stress import thin

        approaches = [args.approach] if args.approach else list(APPROACHES)
        evaluate(args.scenario_dirs, approaches, lambda o, s: thin(o, s, args.keep, args.seed))
    elif args.command == "tune":
        from .tune import search

        best = search(args.scenario_dirs, args.trials, args.seed)
        for r in best[:5]:
            print(
                f"trial {r['trial']:4}  objective {r['objective']:3}  wrong {r['wrong']:3}  "
                f"unresolved {r['unresolved']:3}  margin {r['margin']:.3f}"
            )
        current = next(r for r in best if r["trial"] == 0)
        print(f"current weights: objective {current['objective']}, margin {current['margin']:.3f}")
        print("best weights:", json.dumps({k: round(v, 4) for k, v in best[0]["weights"].items()}))
    elif args.command == "train":
        from .learned import train

        train(args.train_dirs, args.validation, args.output)
    elif args.command == "correlate":
        from .learned import correlate

        correlate(args.train_dirs, args.output)
    else:
        import uvicorn

        uvicorn.run("aircraft_track_association.service:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
