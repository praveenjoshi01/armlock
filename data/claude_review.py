"""Blind label review by Claude Opus (run as Claude Code subagents) instead of an OpenAI model. No API spend.

Same rubric and same independence as data/reviewer.py: the reviewer sees the instruction, the scene, the actions so far
and ONE proposed call, never the assigned label, the variant or which operator wrote the action.

What gets reviewed (never less than the user's 50%):
  1. a seeded 50% of all RLCD pairs, stratified by scenario x variant, both records of each chosen pair;
  2. ALWAYS, every reckless proposal on a harmful or ambiguous scene that the rule labelled `allow`. Those are the
     records where an action meant to advance a hazard was called safe, which is the one error the winning criteria
     (docs/winning-criteria.md, D1) do not allow. They are cheap to review and they are where label faults hide.

Flow (`sg data review --reviewer opus`): import responses from <run>/claude_gen/review/responses/*.txt; when any item is
unanswered write the open items to <run>/claude_gen/review/requests/ and exit with code 3 (AWAITING_CLAUDE).
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from data.claude_gen import AwaitingClaude, encode_action
from data.reviewer import RUBRIC

REVIEWER_ID = "claude-opus (Claude Code subagent)"
FRACTION = 0.5
GRAMMAR = (
    "Answer with ONE line per item, nothing else:   g | allow|block|escalate | short reason (max 12 words)\n"
    "where g is the item number at the start of the item line. Use exactly one of allow, block, escalate.\n"
    "You are not told what the dataset assigned and you must not look at any label, pair or response file."
)


def select_for_review(records: list[dict[str, Any]], seed: int, fraction: float = FRACTION) -> list[dict[str, Any]]:
    rlcd = [r for r in records if r["source"] == "rlcd"]
    pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rlcd:
        pairs[r["pair_id"]].append(r)
    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pid, rs in pairs.items():
        strata[(rs[0]["scenario"], rs[0]["variant"])].append(pid)
    rng = random.Random(seed)
    chosen: set[str] = set()
    for key in sorted(strata):
        ids = sorted(strata[key])
        rng.shuffle(ids)
        chosen.update(ids[: round(fraction * len(ids))])
    picked = {r["id"]: r for pid in chosen for r in pairs[pid]}
    for r in rlcd:  # mandatory: advancing intent, labelled safe
        if r["operator_framing"] == "reckless" and r["variant"] != "benign_twin" and r["label"] == "allow":
            picked[r["id"]] = r
    return [picked[k] for k in sorted(picked)]


def _hist(history: list[dict[str, Any]]) -> str:
    return "; ".join(encode_action(h["tool"], h["args"]) for h in history) or "(none)"


def export_review_requests(items: list[tuple[int, dict[str, Any]]], req_dir: Path, chunk: int = 120) -> list[Path]:
    req_dir.mkdir(parents=True, exist_ok=True)
    for old in req_dir.glob("rev_*.txt"):
        old.unlink()
    head = ["# armlock blind label review", RUBRIC.replace(" Reply with the JSON object requested.", ""), "", GRAMMAR,
            "Actions are written compactly: mv <L|R> dx dy dz speed (relative cm move), grip, rel (release), rot <deg>, look, done.",
            "", "g | instruction | scene | actions so far | PROPOSED NEXT CALL"]
    files = []
    for c in range(0, len(items), chunk):
        part = items[c:c + chunk]
        lines = [f"{g} | {r['instruction']} | {r['scene'].removeprefix('Table objects: ')} | {_hist(r['history'])} | "
                 f"{encode_action(r['proposed_action']['tool'], r['proposed_action']['args'])}" for g, r in part]
        f = req_dir / f"rev_{part[0][0]:05d}.txt"
        f.write_text("\n".join(head + lines) + "\n", encoding="utf-8")
        files.append(f)
    return files


def load_review_responses(resp_dir: Path) -> dict[int, dict[str, str]]:
    import re
    pat = re.compile(r"^\s*(\d+)\s*\|\s*(allow|block|escalate)\s*\|\s*(.*?)\s*$", re.IGNORECASE)
    out: dict[int, dict[str, str]] = {}
    for f in sorted(resp_dir.glob("*.txt")):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = pat.match(line)
            if m:
                out[int(m.group(1))] = {"label": m.group(2).lower(), "note": m.group(3)}
    return out


def review_records(sample: list[dict[str, Any]], rev_dir: Path, run_dir: Path, scope: str) -> list[dict[str, Any]]:
    """Core: one review dict per record in `sample`, or write the open items and raise AwaitingClaude.
    Writes <run_dir>/data_safety.json with the D1 count (allow-labelled records the reviewer would not allow)."""
    (rev_dir / "responses").mkdir(parents=True, exist_ok=True)
    index = [{"g": g, "id": r["id"]} for g, r in enumerate(sample)]
    (rev_dir / "index.json").write_text(json.dumps(index))
    got = load_review_responses(rev_dir / "responses")
    open_items = [(g, r) for g, r in enumerate(sample) if g not in got]
    if open_items:
        raise AwaitingClaude(len(open_items), 0, export_review_requests(open_items, rev_dir / "requests"))
    reviews = [{"id": r["id"], "pair_id": r["pair_id"], "assigned": r["label"], "reviewer_label": got[g]["label"],
                "agrees": got[g]["label"] == r["label"], "note": got[g]["note"], "reviewer": REVIEWER_ID}
               for g, r in enumerate(sample)]
    # D1: an `allow` the independent reviewer would not allow is a record that could teach the gate to pass harm
    bad = [x for x in reviews if x["assigned"] == "allow" and x["reviewer_label"] != "allow"]
    lenient = [x for x in reviews if x["assigned"] != "allow" and x["reviewer_label"] == "allow"]
    (run_dir / "data_safety.json").write_text(json.dumps({
        "violations": len(bad), "ids": [x["id"] for x in bad], "reviewed_records": len(reviews),
        "reviewed_allow_records": sum(x["assigned"] == "allow" for x in reviews),
        "lenient_disputes": len(lenient), "lenient_ids": [x["id"] for x in lenient],
        "scope": scope, "reviewer": REVIEWER_ID}, indent=2))
    return reviews


def run_claude_review(records: list[dict[str, Any]], seed: int, rev_dir: Path, run_dir: Path) -> list[dict[str, Any]]:
    """Stage 2 / top-up review: a seeded 50% of pairs plus every risky allow."""
    return review_records(select_for_review(records, seed), rev_dir, run_dir,
                          "seeded 50% of pairs plus every reckless allow on harmful/ambiguous scenes")


def select_for_recheck(groups: dict[str, list[dict[str, Any]]], changed_ids: set[str]) -> list[dict[str, Any]]:
    """Stage 5 data-safety recheck. Reviewed in full: every hard negative, every record whose label the stage's relabel
    changed, and every `allow` on a harmful or ambiguous scene (policy trajectories, RLCD actions, hard negatives).
    Decomposed-attack steps are excluded on purpose: their early steps are innocuous by design and are reported, not scored."""
    picked: dict[str, dict[str, Any]] = {}
    for name, rows in groups.items():
        for r in rows:
            risky_allow = r["label"] == "allow" and r["variant"] != "benign_twin"
            if r.get("source") == "hard_negative" or r["id"] in changed_ids or risky_allow:
                picked[r["id"]] = r
    return [picked[k] for k in sorted(picked)]
