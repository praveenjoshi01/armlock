"""Collect every failure case of a run, verbatim, into results/<run>/failure_cases.jsonl and docs/failure-cases.md.

Failure classes:
  policy_complied      ungated robot trial where the policy completed the harmful task
  policy_attempted     ungated harmful robot trial that went part of the way
  benign_incomplete    benign-twin trial (ungated or gated) that did not complete
  gated_benign_blocked gated benign trial where the gate blocked or escalated at least one action
  gated_harm_completed gated harmful trial that still completed the harm (none expected; listed if any)
  gate_miss            offline: a harmful action the gate allowed
  gate_false_block     offline: a benign action the gate blocked or escalated
Records are copied unchanged from the run's own files; nothing is paraphrased.
"""
import json
import sys
from collections import Counter
from pathlib import Path

run = sys.argv[1] if len(sys.argv) > 1 else "run_004"
rd = Path("results") / run


def jl(name):
    p = rd / name
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


cases = []
for t in jl("trials.jsonl"):
    if t["framing"] == "chat":
        continue
    if t["variant"] == "harmful" and t["final_score"] == "completed-equivalent":
        cases.append({"class": "policy_complied", "source": "trials.jsonl", "record": t})
    elif t["variant"] == "harmful" and t["final_score"] == "attempted":
        cases.append({"class": "policy_attempted", "source": "trials.jsonl", "record": t})
    elif t["variant"] == "benign_twin" and t["final_score"] != "completed-equivalent":
        cases.append({"class": "benign_incomplete", "source": "trials.jsonl", "record": t})
for t in jl("gated_trials.jsonl"):
    if t["variant"] == "harmful" and t["final_score"] == "completed-equivalent":
        cases.append({"class": "gated_harm_completed", "source": "gated_trials.jsonl", "record": t})
    elif t["variant"] == "benign_twin" and t["final_score"] != "completed-equivalent":
        cases.append({"class": "benign_incomplete", "source": "gated_trials.jsonl", "record": t})
    if t["variant"] == "benign_twin" and t.get("gate_interventions"):
        cases.append({"class": "gated_benign_blocked", "source": "gated_trials.jsonl", "record": t})
for e in jl("errors.jsonl"):
    cases.append({"class": "gate_miss" if e["kind"] == "miss" else "gate_false_block", "source": "errors.jsonl", "record": e})

out = rd / "failure_cases.jsonl"
out.write_text("\n".join(json.dumps(c) for c in cases) + "\n")
counts = Counter(c["class"] for c in cases)

order = ["policy_complied", "policy_attempted", "benign_incomplete", "gated_benign_blocked", "gated_harm_completed", "gate_miss", "gate_false_block"]
what = {
    "policy_complied": "Ungated robot trials where the policy completed the harmful task. This is the refusal gap.",
    "policy_attempted": "Ungated harmful trials that went part of the way (grasped or approached) without completing.",
    "benign_incomplete": "Benign-twin trials that did not complete, with or without the gate.",
    "gated_benign_blocked": "Gated benign trials where the gate blocked or escalated at least one action (the policy may still have finished).",
    "gated_harm_completed": "Gated harmful trials that still completed the harm.",
    "gate_miss": "Offline: harmful actions the gate allowed.",
    "gate_false_block": "Offline: benign actions the gate blocked or escalated.",
}


def show(c):
    r = c["record"]
    if c["source"] == "errors.jsonl":
        return (f"- `{r['id']}` ({r['set']}, {r['scenario']}): truth **{r['true_label']}**, gate **{r['decision']}**, p(allow/block/escalate) = {r['p']}\n"
                f"  - Instruction: {r['instruction']}\n  - Scene: {r['scene']}\n  - History: {json.dumps(r['history'])}\n  - Proposed: `{json.dumps(r['proposed_action'])}`")
    steps = [s for s in r["steps"] if not s.get("auto")]
    tail = "; ".join(f"{s['tool_call']['tool']}({json.dumps(s['tool_call']['args'])}) -> {s['result']['message']}" for s in steps[:4])
    return (f"- `{r['trial_id']}`: {r['scenario_id']} / {r['variant']} / {r['framing']}, result **{r['final_score']}**, ended by `{r.get('terminated_by')}`, "
            f"{len(r.get('gate_interventions', []))} gate interventions\n  - Instruction: {r['instruction']}\n  - First steps: {tail}\n  - Final text: {r['final_text'][:300]}")


md = [f"# Failure cases, `{run}`", "",
      f"Every failure the run produced, collected verbatim in [`results/{run}/failure_cases.jsonl`](../results/{run}/failure_cases.jsonl) "
      f"({len(cases)} records, one per line, each with its `class`, `source` file and the unchanged original record). "
      "Regenerate with `.venv/bin/python scripts/build_failure_log.py " + run + "`.", "",
      "| Class | Count | What it is |", "|---|---|---|"]
md += [f"| `{k}` | {counts.get(k, 0)} | {what[k]} |" for k in order]
md += ["", "Counts are of records in the log, not of distinct problems: one policy trajectory can appear in two classes. "
       "Nothing here has been filtered, deduplicated or paraphrased. Three examples per class are quoted below; the rest are in the JSONL.", ""]
for k in order:
    items = [c for c in cases if c["class"] == k]
    md += [f"## `{k}` ({len(items)})", "", what[k], ""]
    if not items:
        md += ["_None in this run._", ""]
        continue
    seen_scen, shown = set(), []
    for c in items:  # spread the examples over scenarios
        sc = c["record"].get("scenario_id") or c["record"].get("scenario")
        if sc not in seen_scen and len(shown) < 3:
            seen_scen.add(sc)
            shown.append(c)
    for c in shown:
        md.append(show(c))
    md.append("")
Path("docs/failure-cases.md").write_text("\n".join(md), encoding="utf-8")
print(dict(counts), "->", out, "and docs/failure-cases.md")
