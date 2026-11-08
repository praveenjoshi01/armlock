#!/usr/bin/env python
"""Track progress towards the winning criteria (docs/winning-criteria.md), one entry per iteration, and plot it.

  .venv/bin/python scripts/progress.py add run_004 --label "physics + hard negatives" [--iteration 2]
  .venv/bin/python scripts/progress.py plot            # rewrite results/progress/progress.png
  .venv/bin/python scripts/progress.py show            # table in the terminal

Each `add` appends/replaces an entry in results/progress/iterations.jsonl and redraws the cumulative chart, saving a
copy as results/progress/iteration_NN.png so every iteration keeps its own picture.
If results/<run>/data_safety.json holds {"violations": n} it is used for the data-safety criterion.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.criteria import BENIGN_FB_MAX, BENIGN_SCORED, evaluate_run  # noqa: E402

OUT = ROOT / "results" / "progress"
LOG = OUT / "iterations.jsonl"


def load() -> list[dict]:
    return [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []


def save(rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["iteration"])))


def add(run_id: str, label: str, iteration: int | None) -> dict:
    run_dir = ROOT / "results" / run_id
    ds = run_dir / "data_safety.json"
    violations = json.loads(ds.read_text())["violations"] if ds.exists() else None
    ev = evaluate_run(run_dir, violations)
    rows = load()
    it = iteration if iteration is not None else (max((r["iteration"] for r in rows), default=-1) + 1)
    row = {"iteration": it, "run_id": run_id, "label": label, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "all_met": ev["all_met"], "failing": ev["failing"], "criteria": ev["criteria"], "numbers": ev["numbers"]}
    save([r for r in rows if r["iteration"] != it] + [row])
    plot(it)
    return row


def _series(rows: list[dict], key: str, field: str):
    return [((r["numbers"]["sets"].get(key) or {}).get(field)) for r in rows]


def plot(upto: int | None = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [r for r in load() if upto is None or r["iteration"] <= upto]
    if not rows:
        raise SystemExit("no iterations recorded yet: run `scripts/progress.py add <run_id> --label ...`")
    xs = [r["iteration"] for r in rows]
    names = [f"{r['iteration']}\n{r['run_id']}" for r in rows]
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.8))
    fig.suptitle(f"armlock: progress to the winning criteria (iteration {xs[-1]}, "
                 f"{'ALL MET' if rows[-1]['all_met'] else 'not yet met'})", fontsize=13)

    colors = {"test": "#2a6fdb", "hard_negatives": "#e07b00", "heldout_harness": "#7a3db8", "decomposed": "#888888"}
    # panel 1: harmful actions allowed (count) - target is exactly zero
    a = ax[0]
    for k, col in colors.items():
        ys = _series(rows, k, "harmful_passed")
        pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
        if pts:
            a.plot(*zip(*pts), marker="o", color=col, label=k)
    il = [(x, (r["numbers"]["inloop"]["harmful_completed_gated"] or [None])[0]) for x, r in zip(xs, rows)]
    il = [(x, y) for x, y in il if y is not None]
    if il:
        a.plot(*zip(*il), marker="s", ls="--", color="#c0392b", label="closed loop: harmful trials completed")
    a.axhline(0, color="green", lw=2, label="target = 0")
    a.set_title("Harmful actions allowed (count, lower is better)")
    a.set_ylabel("count"); a.set_xticks(xs); a.set_xticklabels(names, fontsize=8); a.legend(fontsize=7)
    a.set_ylim(bottom=-0.5)

    # panel 2: benign false-block rate on scored sets
    b = ax[1]
    for k in BENIGN_SCORED:
        ys = _series(rows, k, "fb_rate")
        pts = [(x, 100 * y) for x, y in zip(xs, ys) if y is not None]
        if pts:
            b.plot(*zip(*pts), marker="o", color=colors[k], label=k)
    b.axhline(100 * BENIGN_FB_MAX, color="green", lw=2, label=f"target <= {100 * BENIGN_FB_MAX:.0f}%")
    b.set_title("Benign actions blocked or escalated (%, lower is better)")
    b.set_ylabel("% of benign actions"); b.set_xticks(xs); b.set_xticklabels(names, fontsize=8); b.legend(fontsize=7)
    b.set_ylim(bottom=0)

    # panel 3: closed-loop benign trials interrupted
    c = ax[2]
    pts = []
    for x, r in zip(xs, rows):
        iv = r["numbers"]["inloop"]["benign_with_intervention"]
        if iv and iv[1]:
            pts.append((x, 100 * iv[0] / iv[1]))
    if pts:
        c.plot(*zip(*pts), marker="o", color="#2a6fdb", label="benign trials with any gate intervention")
    c.axhline(1, color="green", lw=2, label="target <= 1%")
    c.set_title("Closed loop: benign trials interrupted (%)")
    c.set_ylabel("% of 200 benign trials"); c.set_xticks(xs); c.set_xticklabels(names, fontsize=8); c.legend(fontsize=7)
    c.set_ylim(bottom=0)

    fig.tight_layout(rect=(0, 0, 1, 0.94))
    OUT.mkdir(parents=True, exist_ok=True)
    main = OUT / "progress.png"
    fig.savefig(main, dpi=130)
    fig.savefig(OUT / f"iteration_{xs[-1]:02d}.png", dpi=130)
    plt.close(fig)
    return main


def show() -> None:
    for r in load():
        s = r["numbers"]["sets"]
        def f(k):
            v = s.get(k)
            return "n/a" if not v else f"harm {v['harmful_passed']}/{v['harmful_n']}, fb {v['fb_rate'] * 100:.1f}%" if v["fb_rate"] is not None else "n/a"
        print(f"it {r['iteration']:>2} {r['run_id']:<8} {'MET ' if r['all_met'] else 'open'} test[{f('test')}] hard[{f('hard_negatives')}] "
              f"heldout[{f('heldout_harness')}]  {r['label']}")
        if not r["all_met"]:
            print("      failing:", ", ".join(r["failing"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("add"); pa.add_argument("run_id"); pa.add_argument("--label", required=True); pa.add_argument("--iteration", type=int)
    sub.add_parser("plot"); sub.add_parser("show")
    a = ap.parse_args()
    if a.cmd == "add":
        row = add(a.run_id, a.label, a.iteration)
        print(f"iteration {row['iteration']} ({row['run_id']}): {'ALL CRITERIA MET' if row['all_met'] else 'not met: ' + ', '.join(row['failing'])}")
        print(f"chart: {OUT / 'progress.png'}")
    elif a.cmd == "plot":
        print(plot())
    else:
        show()
