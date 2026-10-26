import math
from typing import Tuple


def wilson_score_interval(successes: int, total: int, confidence: float = 0.95) -> Tuple[float, float, float]:
    """
    Computes Wilson score interval for a binomial proportion.
    Returns: (point_estimate, ci_lower, ci_upper)
    """
    if total <= 0:
        return 0.0, 0.0, 0.0

    # Normal critical value (e.g. 1.95996 for 95%)
    z_values = {
        0.90: 1.6448536269514722,
        0.95: 1.959963984540054,
        0.99: 2.5758293035489004
    }
    z = z_values.get(confidence, 1.959963984540054)

    p_hat = successes / total
    denom = 1.0 + (z ** 2) / total
    center = (p_hat + (z ** 2) / (2.0 * total)) / denom
    half_width = (z / denom) * math.sqrt((p_hat * (1.0 - p_hat) / total) + ((z ** 2) / (4.0 * (total ** 2))))

    ci_lower = max(0.0, center - half_width)
    ci_upper = min(1.0, center + half_width)

    return p_hat, ci_lower, ci_upper


def format_rate_ci(successes: int, total: int, pct: bool = True) -> str:
    p, lo, hi = wilson_score_interval(successes, total)
    if pct:
        return f"{p * 100:.1f}% [{lo * 100:.1f}%, {hi * 100:.1f}%]"
    return f"{p:.3f} [{lo:.3f}, {hi:.3f}]"
