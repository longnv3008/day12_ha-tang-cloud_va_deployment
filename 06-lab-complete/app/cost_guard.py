"""
Cost guard module — daily budget protection.

Tracks cumulative LLM token costs per day (UTC).
Raises HTTP 402 when the configured daily budget is exceeded.
Resets automatically at midnight UTC.
"""
import time
from fastapi import HTTPException


# Module-level state (resets when process restarts or at midnight UTC)
_daily_cost: float = 0.0
_cost_reset_day: str = time.strftime("%Y-%m-%d", time.gmtime())


def check_budget(daily_budget_usd: float) -> None:
    """
    Guard: raise 402 if daily budget is already exhausted.

    Args:
        daily_budget_usd: max spend allowed per UTC day (e.g. 5.0).

    Raises:
        HTTPException(402): if accumulated cost >= budget.
    """
    global _daily_cost, _cost_reset_day

    today = time.strftime("%Y-%m-%d", time.gmtime())
    if today != _cost_reset_day:
        _daily_cost = 0.0
        _cost_reset_day = today

    if _daily_cost >= daily_budget_usd:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Daily budget of ${daily_budget_usd:.2f} exhausted. "
                "Try again tomorrow."
            ),
        )


def record_cost(input_tokens: int, output_tokens: int) -> None:
    """
    Accumulate cost for a completed LLM call.

    Pricing approximation (gpt-4o-mini):
        $0.00015 / 1K input tokens
        $0.00060 / 1K output tokens
    """
    global _daily_cost
    cost = (input_tokens / 1_000) * 0.00015 + (output_tokens / 1_000) * 0.00060
    _daily_cost += cost


def get_daily_cost() -> float:
    """Return accumulated cost for today (USD)."""
    return _daily_cost
