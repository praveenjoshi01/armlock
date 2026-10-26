class PrerequisiteError(Exception):
    """Raised when a stage or operation is requested out of order."""
    def __init__(self, message: str, missing_stages: list[str] | None = None):
        super().__init__(message)
        self.missing_stages = missing_stages or []


class BudgetExceededError(Exception):
    """Raised when an operation would exceed the project API budget cap."""
    def __init__(self, current_spend: float, requested_cost: float, cap: float):
        super().__init__(
            f"Budget cap exceeded: current spend ${current_spend:.4f} + "
            f"requested ${requested_cost:.4f} > cap ${cap:.2f}"
        )
        self.current_spend = current_spend
        self.requested_cost = requested_cost
        self.cap = cap


class MockDetectedError(Exception):
    """Raised when a mock, stub or simulated component is used where a real one is required."""


class ModelUnavailableError(Exception):
    """Raised when a required real model/server/credential is missing. Never silently degraded."""


class StaleUngatedRunError(Exception):
    """The ungated replication was produced under a different harness config or model; Module D must ask first."""
