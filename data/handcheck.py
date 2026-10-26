"""Hand-check set: ~100 records the user labels by hand; used to score both the dataset and the reviewer."""
import json
import random
from pathlib import Path
from typing import Any

from data.reviewer import _item


def export_handcheck(reviewed_ids: list[str], records: list[dict[str, Any]], run_dir: Path,
                     n: int = 100, seed: int = 7) -> Path:
    """Pick n records from the LLM-reviewed sample so reviewer-vs-human agreement is computable."""
    by_id = {r["id"]: r for r in records}
    rng = random.Random(seed)
    ids = list(reviewed_ids)
    rng.shuffle(ids)
    chosen = ids[:n]
    path = run_dir / "handcheck_set.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for i in chosen:  # labels deliberately omitted: hand labelling must be blind
            r = by_id[i]
            f.write(json.dumps({"id": r["id"], **{k: v for k, v in _item(0, r).items() if k != "i"}}) + "\n")
    return path


def load_labels(run_dir: Path) -> dict[str, str]:
    p = run_dir / "handcheck_labels.json"
    return json.loads(p.read_text()) if p.exists() else {}


def label_interactively(run_dir: Path) -> None:
    items = [json.loads(l) for l in (run_dir / "handcheck_set.jsonl").read_text().splitlines()]
    labels = load_labels(run_dir)
    keys = {"a": "allow", "b": "block", "e": "escalate"}
    for n, it in enumerate(items, 1):
        if it["id"] in labels:
            continue
        print(f"\n--- {n}/{len(items)} ---\nInstruction: {it['instruction']}\nScene: {it['scene']}")
        print("History:\n  " + ("\n  ".join(it["history"]) or "(none)"))
        print(f"PROPOSED: {it['proposed_action']}")
        while True:
            k = input("[a]llow / [b]lock / [e]scalate / [q]uit > ").strip().lower()
            if k == "q":
                return
            if k in keys:
                labels[it["id"]] = keys[k]
                (run_dir / "handcheck_labels.json").write_text(json.dumps(labels, indent=2))
                break


def agreement(labels: dict[str, str], dataset: dict[str, dict[str, Any]], review: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = [i for i in labels if i in dataset]
    out: dict[str, Any] = {"labelled": len(ids)}
    if ids:
        out["dataset_vs_human"] = round(sum(labels[i] == dataset[i]["label"] for i in ids) / len(ids), 4)
        rv = [i for i in ids if i in review]
        out["reviewer_vs_human"] = round(sum(labels[i] == review[i]["reviewer_label"] for i in rv) / len(rv), 4) if rv else None
        out["reviewer_labelled_overlap"] = len(rv)
    return out
