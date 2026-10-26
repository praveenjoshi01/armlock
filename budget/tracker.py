import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from common.exceptions import BudgetExceededError


# Standard pricing per million tokens (USD)
MODEL_PRICING = {  # USD per 1M tokens, from developers.openai.com/api/docs/pricing (checked 2026-10-21)
    "gpt-5.4": {"input_per_m": 2.50, "output_per_m": 15.00},
    "gpt-5.4-mini": {"input_per_m": 0.75, "output_per_m": 4.50},
    "gpt-5.5": {"input_per_m": 5.00, "output_per_m": 30.00},
    "gpt-5-mini": {"input_per_m": 0.25, "output_per_m": 2.00},
    "gpt-4.1": {"input_per_m": 2.00, "output_per_m": 8.00},
}


class BudgetTracker:
    def __init__(
        self,
        hard_cap_usd: float = 5.00,
        warning_threshold_usd: float = 4.50,
        results_dir: Path | str = "results",
        run_id: str = "default"
    ):
        self.hard_cap_usd = hard_cap_usd
        self.warning_threshold_usd = warning_threshold_usd
        self.results_dir = Path(results_dir)
        self.run_id = run_id
        self.run_dir = self.results_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.budget_file = self.run_dir / "budget.json"
        self._load_or_init()

    def _default_state(self) -> dict[str, Any]:
        return {
            "hard_cap_usd": self.hard_cap_usd,
            "warning_threshold_usd": self.warning_threshold_usd,
            "total_spend_usd": 0.0,
            "remaining_budget_usd": self.hard_cap_usd,
            "transactions": []
        }

    def _load_or_init(self) -> None:
        if self.budget_file.exists():
            try:
                data = json.loads(self.budget_file.read_text())
                self.data = data
                return
            except Exception:
                pass
        self.data = self._default_state()
        self._save()

    def _save(self) -> None:
        self.data["remaining_budget_usd"] = max(0.0, self.hard_cap_usd - self.total_spend)
        self.budget_file.write_text(json.dumps(self.data, indent=2))

    def _other_runs_spend(self) -> float:
        """The cap is project-wide: include spend recorded by other runs (archived runs excluded)."""
        total = 0.0
        for f in self.results_dir.glob("*/budget.json"):
            if f.parent.name in (self.run_id, "archive") or f.parent.is_symlink():
                continue
            try:
                total += float(json.loads(f.read_text()).get("total_spend_usd", 0.0))
            except (ValueError, OSError):
                pass
        return total

    @property
    def total_spend(self) -> float:
        return float(self.data.get("total_spend_usd", 0.0)) + self._other_runs_spend()

    @property
    def remaining(self) -> float:
        return max(0.0, self.hard_cap_usd - self.total_spend)

    def estimate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> float:
        if model not in MODEL_PRICING:
            raise KeyError(f"No pricing known for model {model!r}; add it to MODEL_PRICING before spending.")
        pricing = MODEL_PRICING[model]
        input_cost = (prompt_tokens / 1_000_000.0) * pricing["input_per_m"]
        output_cost = (completion_tokens / 1_000_000.0) * pricing["output_per_m"]
        return round(input_cost + output_cost, 6)

    def check_budget_or_raise(self, estimated_cost_usd: float) -> None:
        if self.total_spend + estimated_cost_usd > self.hard_cap_usd + 1e-9:
            raise BudgetExceededError(
                current_spend=self.total_spend,
                requested_cost=estimated_cost_usd,
                cap=self.hard_cap_usd
            )

    def record_spend(
        self,
        cost_usd: float,
        stage: str,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        notes: str = ""
    ) -> float:
        self.check_budget_or_raise(cost_usd)
        self.data["total_spend_usd"] = round(float(self.data["total_spend_usd"]) + cost_usd, 6)

        tx = {
            "tx_id": f"tx_{len(self.data['transactions']) + 1:04d}",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd,
            "cumulative_spend_usd": round(self.total_spend, 6),
            "notes": notes
        }
        self.data["transactions"].append(tx)
        self._save()
        return self.total_spend

    def to_dict(self) -> dict[str, Any]:
        return self.data
