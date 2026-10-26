from collections import Counter, defaultdict
from typing import Any


def compute_eda_metrics(
    records: list[dict[str, Any]],
    review_summary: dict[str, Any] | None = None,
    split_summary: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Computes comprehensive EDA statistics and chart distributions for Module B."""
    total_records = len(records)
    if total_records == 0:
        return {}

    label_counts = Counter(r.get("label", "unknown") for r in records)
    variant_counts = Counter(r.get("variant", "unknown") for r in records)
    scenario_counts = Counter(r.get("scenario", "unknown") for r in records)

    # Label mix per scenario
    scenario_label_mix = defaultdict(lambda: Counter())
    for r in records:
        scen = r.get("scenario", "unknown")
        lbl = r.get("label", "unknown")
        scenario_label_mix[scen][lbl] += 1

    # History length distribution
    history_lengths = [len(r.get("history", [])) for r in records]
    history_dist = Counter(history_lengths)

    # Proposed tool distribution
    tool_counts = Counter(str(r.get("proposed_action", {}).get("tool", "unknown")) for r in records)

    # Instruction length stats
    inst_lengths = [len(r.get("instruction", "").split()) for r in records]
    avg_inst_len = round(sum(inst_lengths) / max(1, len(inst_lengths)), 1)
    min_inst_len = min(inst_lengths) if inst_lengths else 0
    max_inst_len = max(inst_lengths) if inst_lengths else 0

    # Near-duplicate check (instruction + proposed_action)
    action_hashes = set()
    near_dupe_count = 0
    for r in records:
        key = f"{r.get('instruction', '')}_{str(r.get('proposed_action', {}))}"
        if key in action_hashes:
            near_dupe_count += 1
        else:
            action_hashes.add(key)

    # Split sizes
    split_counts = Counter(r.get("split", "unassigned") for r in records)

    # Leakage check
    pair_splits: dict[str, set[str]] = defaultdict(set)
    for r in records:
        p_id = r.get("pair_id")
        split = r.get("split")
        if p_id and split:
            pair_splits[p_id].add(split)

    leaked_pairs = [p for p, splits in pair_splits.items() if len(splits) > 1]
    no_leakage = (len(leaked_pairs) == 0)

    return {
        "total_records": total_records,
        "total_pairs": len({r.get("pair_id") for r in records if r.get("source") == "rlcd"}),
        "total_pairs_all_sources": len({r.get("pair_id") for r in records}),
        "label_distribution": dict(label_counts),
        "variant_distribution": dict(variant_counts),
        "scenario_distribution": dict(scenario_counts),
        "scenario_label_mix": {s: dict(c) for s, c in scenario_label_mix.items()},
        "history_length_histogram": {str(k): v for k, v in sorted(history_dist.items())},
        "proposed_tool_distribution": dict(tool_counts),
        "instruction_length": {
            "avg_words": avg_inst_len,
            "min_words": min_inst_len,
            "max_words": max_inst_len
        },
        "near_duplicate_count": near_dupe_count,
        "split_counts": dict(split_counts),
        "no_pair_id_leakage": no_leakage,
        "leaked_pairs_count": len(leaked_pairs),
        "review_summary": review_summary or {},
        "split_summary": split_summary or {}
    }
