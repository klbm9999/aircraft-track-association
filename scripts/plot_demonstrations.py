"""Plot the README demonstrations on the local east-north plane.

Uses the same tracklet projection as the model (`track.load_tracklets`): kilometres
east and north of the scenario centre. Aircraft letters run from lowest mean altitude
to highest. Fragment numbers run from earliest to latest.

    uv run python scripts/plot_demonstrations.py

Writes `docs/scenario_001.png` and `docs/staged_demo.png`.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch

from aircraft_track_association.associate import decide as gaussian_decide
from aircraft_track_association.baseline import decide as baseline_decide
from aircraft_track_association.decision import links, short
from aircraft_track_association.io import load_successors
from aircraft_track_association.track import load_tracklets

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Okabe-Ito, skipping yellow. Index 0 is the lowest aircraft.
COLOURS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9"]

# Train scenario_001 fragments the writeup discusses, by short id.
# B is the aircraft with three plausible continuations. D is the parallel aircraft
# the baseline confuses with it. F is the clear high continuation. E4 is the low distractor.
CALLOUT = {
    "7e157a59": "B1",
    "d7c3b003": "B2",
    "b22bc68f": "B3",
    "03b4b60d": "B4",
    "054df159": "D1",
    "f1642a8a": "D2",
    "e293fd97": "E4",
    "4aa1f86b": "F3",
    "27f73aa2": "F4",
}


def chains(scenario: Path) -> list[list]:
    """Aircraft chains, lowest mean altitude first. Each chain is earliest fragment first."""
    tracklets = load_tracklets(scenario)
    succ = {src: dst for src, dst in load_successors(scenario).items() if dst}
    by_id = {tr.id: tr for tr in tracklets}
    pred = {dst: src for src, dst in succ.items()}
    groups = []
    for start in (tid for tid in by_id if tid not in pred):
        group = []
        cur = start
        while cur:
            group.append(by_id[cur])
            cur = succ.get(cur)
        groups.append(group)
    groups.sort(key=lambda group: float(np.mean([tr.alt_mean for tr in group])))
    return groups


def labels_for(groups: list[list]) -> dict[str, str]:
    names = {}
    for index, group in enumerate(groups):
        letter = chr(ord("A") + index)
        for number, tr in enumerate(group, start=1):
            names[tr.id] = f"{letter}{number}"
    return names


def km(tr) -> np.ndarray:
    return tr.p[:, :2] / 1000.0


def draw_tracks(ax, groups, names, label_filter=None, faded=False, offsets=None) -> None:
    offsets = offsets or {}
    for index, group in enumerate(groups):
        colour = "#d0d0d0" if faded else COLOURS[index % len(COLOURS)]
        width = 1.0 if faded else 2.0
        for tr in group:
            path = km(tr)
            ax.plot(
                path[:, 0], path[:, 1], color=colour, lw=width, solid_capstyle="round", zorder=2
            )
            name = names[tr.id]
            if label_filter is not None and name not in label_filter:
                continue
            mid = path[len(path) // 2]
            ax.annotate(
                name,
                mid,
                textcoords="offset points",
                xytext=offsets.get(name, (4, 4)),
                fontsize=8,
                color="#222222",
                zorder=5,
                bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.85},
            )


def draw_links(ax, groups, accepted: dict[str, str], colour, style="-", width=1.3, rad=0.0) -> None:
    by_id = {tr.id: tr for group in groups for tr in group}
    for src, dst in accepted.items():
        if src not in by_id or dst not in by_id:
            continue
        start = km(by_id[src])[-1]
        end = km(by_id[dst])[0]
        ax.add_patch(
            FancyArrowPatch(
                start,
                end,
                arrowstyle="-|>",
                mutation_scale=10,
                color=colour,
                lw=width,
                linestyle=style,
                connectionstyle=f"arc3,rad={rad}",
                zorder=3,
            )
        )


def style_plane(ax, xlabel=True) -> None:
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, color="#eeeeee", zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if xlabel:
        ax.set_xlabel("East of scenario centre (km)")
    ax.set_ylabel("North of scenario centre (km)")


def require_link(accepted: dict[str, str], src_short: str, dst_short: str, what: str) -> None:
    pair = {short(src): short(dst) for src, dst in accepted.items()}
    got = pair.get(src_short)
    if got != dst_short:
        raise SystemExit(f"{what}: expected {src_short} -> {dst_short}, got {got}")


def plot_scenario() -> None:
    scenario = ROOT / "candidate-packet/data/train/scenario_001"
    groups = chains(scenario)
    names = labels_for(groups)
    tracklets = [tr for group in groups for tr in group]
    gaussian = links(gaussian_decide(tracklets))
    baseline = links(baseline_decide(tracklets))
    for src_name, dst_name in (("B1", "B2"), ("F3", "F4")):
        src = next(tid for tid, name in names.items() if name == src_name)
        dst = next(tid for tid, name in names.items() if name == dst_name)
        require_link(gaussian, short(src), short(dst), "gaussian")
    src = next(tid for tid, name in names.items() if name == "B1")
    dst = next(tid for tid, name in names.items() if name == "D2")
    require_link(baseline, short(src), short(dst), "baseline")
    # The callout names are the lettering rule, not a second naming scheme.
    for tid, name in names.items():
        if short(tid) in CALLOUT and CALLOUT[short(tid)] != name:
            raise SystemExit(f"label drift: {short(tid)} is {name}, expected {CALLOUT[short(tid)]}")

    discussed = set(CALLOUT.values())
    # Push labels off the track they name so the parallel pair B/D stays readable.
    offsets = {
        "B1": (0, 11),
        "B2": (0, 11),
        "B3": (-4, 11),
        "B4": (-6, 10),
        "D1": (0, -14),
        "D2": (0, -14),
        "E4": (0, 10),
        "F3": (6, -13),
        "F4": (0, -13),
    }
    fig, ax = plt.subplots(figsize=(11.2, 7.6))
    draw_tracks(ax, groups, names, label_filter=discussed, offsets=offsets)
    draw_links(ax, groups, gaussian, "#222222", width=1.15)
    draw_links(ax, groups, {src: baseline[src]}, "#D55E00", style=(0, (3, 2)), width=1.8, rad=0.25)
    altitudes = [float(np.mean([tr.alt_mean for tr in group])) / 1000 for group in groups]
    handles = [
        plt.Line2D([0], [0], color=COLOURS[i], lw=2.5, label=f"{chr(ord('A') + i)}  {alt:.1f} km")
        for i, alt in enumerate(altitudes)
    ]
    handles.append(plt.Line2D([0], [0], color="#222222", lw=1.15, label="Gaussian link"))
    handles.append(
        plt.Line2D(
            [0], [0], color="#D55E00", lw=1.8, linestyle=(0, (3, 2)), label="Baseline link B1 to D2"
        )
    )
    ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=8, title="Mean altitude")
    style_plane(ax)
    ax.set_title(
        "Train scenario 001. Six aircraft, four fragments each, flying upper right to lower left."
    )
    fig.tight_layout()
    DOCS.mkdir(parents=True, exist_ok=True)
    fig.savefig(DOCS / "scenario_001.png", dpi=140)
    plt.close(fig)
    print("altitudes", [round(a, 2) for a in altitudes])
    print("labels", {short(tid): name for tid, name in names.items()})


def plot_staged() -> None:
    snap1 = ROOT / "candidate-packet/data/staged_demo/snapshot_1"
    snap2 = ROOT / "candidate-packet/data/staged_demo/snapshot_2"
    groups1 = chains(snap1)
    groups2 = chains(snap2)
    focus_short = "aa82aa85"
    focus1 = next(group for group in groups1 if any(short(tr.id) == focus_short for tr in group))
    focus2 = next(group for group in groups2 if any(short(tr.id) == focus_short for tr in group))
    roles = {"aa82aa85": "earlier", "691cc611": "delayed", "1d397900": "later", "d81aeacc": "last"}

    def named(group):
        return {tr.id: roles.get(short(tr.id), short(tr.id)) for tr in group}

    g1 = links(gaussian_decide([tr for group in groups1 for tr in group]))
    g2 = links(gaussian_decide([tr for group in groups2 for tr in group]))
    require_link(g1, "aa82aa85", "1d397900", "snapshot 1")
    require_link(g2, "aa82aa85", "691cc611", "snapshot 2 earlier")
    require_link(g2, "691cc611", "1d397900", "snapshot 2 delayed")

    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.8), sharex=True, sharey=True)
    panels = (
        (
            axes[0],
            groups1,
            focus1,
            g1,
            "Snapshot 1. The earlier fragment links straight to the later one.",
        ),
        (axes[1], groups2, focus2, g2, "Snapshot 2. The delayed fragment sits in that gap."),
    )
    east = np.concatenate([km(tr)[:, 0] for group in groups2 for tr in group])
    north = np.concatenate([km(tr)[:, 1] for group in groups2 for tr in group])
    pad = 4.0
    xlim = (float(east.min()) - pad, float(east.max()) + pad)
    ylim = (float(north.min()) - pad, float(north.max()) + pad)
    for ax, groups, focus, accepted, title in panels:
        others = [group for group in groups if group is not focus]
        draw_tracks(ax, others, labels_for(groups), label_filter=set(), faded=True)
        draw_tracks(
            ax,
            [focus],
            named(focus),
            label_filter=set(roles.values()),
            offsets={
                "earlier": (8, -12),
                "delayed": (10, -12),
                "later": (8, 8),
                "last": (6, 10),
            },
        )
        focus_ids = {tr.id for tr in focus}
        draw_links(
            ax, [focus], {s: d for s, d in accepted.items() if s in focus_ids}, "#0072B2", width=1.6
        )
        style_plane(ax, xlabel=True)
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(DOCS / "staged_demo.png", dpi=140)
    plt.close(fig)


def main() -> None:
    plot_scenario()
    plot_staged()
    print(f"wrote {DOCS / 'scenario_001.png'}")
    print(f"wrote {DOCS / 'staged_demo.png'}")


if __name__ == "__main__":
    main()
