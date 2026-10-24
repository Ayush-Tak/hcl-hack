"""Explainable fraud signals. Weights are the start values from CLAUDE.md."""

from datetime import datetime, timedelta
from statistics import median

from app.formatting import format_inr

AMOUNT_VS_MEDIAN = 0.35
FIRST_TIME_MERCHANT = 0.20
NIGHT_HOURS = 0.15
AWAY_FROM_HOME = 0.20
RAPID_REPEATS = 0.10


def _amount_signal(txn: dict, history: list[dict]) -> tuple[float, str] | None:
    same_category = [
        t["amount"] for t in history if t["category"] == txn.get("category")
    ]
    if not same_category:
        return None
    usual = median(same_category)
    if txn["amount"] <= 3 * usual:
        return None
    times = round(txn["amount"] / usual)
    return (
        AMOUNT_VS_MEDIAN,
        f"{format_inr(txn['amount'])} is {times}x your usual {txn['category']} spend",
    )


def _rapid_signal(
    txn: dict, history: list[dict], when: datetime
) -> tuple[float, str] | None:
    window_start = when - timedelta(minutes=10)
    recent = [
        t for t in history if window_start <= datetime.fromisoformat(t["ts"]) <= when
    ]
    if len(recent) + 1 < 3:
        return None
    return RAPID_REPEATS, f"{len(recent) + 1} payments within 10 minutes"


def rule_signals(
    txn: dict, history: list[dict], home_city: str
) -> list[tuple[float, str]]:
    """history: the customer's debits from the 90 days before this transaction."""
    when = datetime.fromisoformat(txn["ts"])
    signals = []
    amount = _amount_signal(txn, history)
    if amount:
        signals.append(amount)
    if not any(t["merchant"] == txn["merchant"] for t in history):
        signals.append((FIRST_TIME_MERCHANT, f"first payment to {txn['merchant']}"))
    if when.hour < 5:
        signals.append((NIGHT_HOURS, f"made at {when:%H:%M}"))
    if txn.get("city") and txn["city"] != home_city:
        signals.append(
            (AWAY_FROM_HOME, f"made in {txn['city']}, away from {home_city}")
        )
    rapid = _rapid_signal(txn, history, when)
    if rapid:
        signals.append(rapid)
    return signals
