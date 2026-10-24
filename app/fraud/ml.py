"""Customer-specific transaction anomaly scoring."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import datetime

import numpy as np
from sklearn.ensemble import IsolationForest


def _is_debit(txn: dict) -> bool:
    return str(txn.get("direction", "debit")).lower() == "debit"


def _timestamp(txn: dict) -> datetime:
    value = txn.get("ts", txn.get("time"))
    if not value:
        raise ValueError("transaction must include a ts or time")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid transaction timestamp: {value!r}") from exc


def _median_amount(history: list[dict]) -> float:
    amounts = [float(row["amount"]) for row in history if _is_debit(row)]
    return statistics.median(amounts) if amounts else 0.0


def _usual_city(history: list[dict]) -> str | None:
    cities = [str(row["city"]) for row in history if _is_debit(row) and row.get("city")]
    return Counter(cities).most_common(1)[0][0] if cities else None


def _features(
    txn: dict, history: list[dict], median: float, usual_city: str | None
) -> np.ndarray:
    amount = float(txn["amount"])
    merchant = str(txn.get("merchant", "")).casefold()
    known_merchants = {
        str(row.get("merchant", "")).casefold()
        for row in history
        if _is_debit(row) and row.get("merchant")
    }
    ts = _timestamp(txn)
    recent_debits = 0
    for row in history:
        if row is txn or not _is_debit(row):
            continue
        age_seconds = _seconds_between(ts, _timestamp(row))
        if 0 <= age_seconds <= 600:
            recent_debits += 1
    city = str(txn.get("city", "")).casefold()
    return np.array(
        [
            math.log1p(amount / max(median, 1.0)),
            ts.hour + ts.minute / 60,
            float(bool(merchant) and merchant not in known_merchants),
            float(bool(city) and usual_city is not None and city != usual_city.casefold()),
            recent_debits,
        ],
        dtype=float,
    )


def _seconds_between(later: datetime, earlier: datetime) -> float:
    """Return a timestamp difference while handling naive/aware values consistently."""
    if later.tzinfo is None and earlier.tzinfo is not None:
        earlier = earlier.replace(tzinfo=None)
    elif later.tzinfo is not None and earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=later.tzinfo)
    return (later - earlier).total_seconds()


def _rule_risk(txn: dict, history: list[dict], features: np.ndarray) -> float:
    amounts = [float(row["amount"]) for row in history if _is_debit(row)]
    if amounts:
        amount_median = statistics.median(amounts)
        deviations = [abs(value - amount_median) for value in amounts]
        scale = max(statistics.median(deviations) * 1.4826, amount_median * 0.1, 1.0)
        amount_z = max(0.0, (float(txn["amount"]) - amount_median) / scale)
    else:
        amount_z = 0.0
    amount_risk = 1 - math.exp(-0.5 * amount_z**2)
    hour = float(features[1])
    signals = [
        amount_risk,
        0.55 if hour < 5 else 0.0,
        0.45 if features[2] else 0.0,
        0.50 if features[3] else 0.0,
        min(0.75, 0.25 * float(features[4])),
    ]
    return 1 - math.prod(1 - signal for signal in signals)


def score(txn: dict, history: list[dict]) -> float:
    """Score a debit against this customer's debit history, returning a value in [0, 1]."""
    if "amount" not in txn:
        raise ValueError("transaction must include amount")
    amount = float(txn["amount"])
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("transaction amount must be a finite non-negative number")
    debits = [row for row in history if _is_debit(row)]
    if not debits or not _is_debit(txn):
        return 0.0

    median = _median_amount(debits)
    city = _usual_city(debits)
    current_features = _features(txn, debits, median, city)
    if len(debits) < 20:
        return _rule_risk(txn, debits, current_features)

    model = IsolationForest(
        n_estimators=200,
        contamination="auto",
        random_state=42,
    )
    history_features = np.vstack(
        [_features(row, debits, median, city) for row in debits]
    )
    model.fit(history_features)
    baseline = model.decision_function(history_features)
    current = float(model.decision_function(current_features.reshape(1, -1))[0])
    center = float(np.median(baseline))
    spread = max(float(np.median(np.abs(baseline - center))) * 1.4826, 0.01)
    deviation = max(0.0, (center - current) / spread)
    forest_risk = 1 / (1 + math.exp(-1.5 * (deviation - 2)))
    return min(1.0, max(forest_risk, _rule_risk(txn, debits, current_features)))


def explain(txn: dict, history: list[dict]) -> list[str]:
    """Return short, plain-English reasons based on the customer's own debit history."""
    if "amount" not in txn:
        raise ValueError("transaction must include amount")
    debits = [row for row in history if _is_debit(row)]
    if not debits or not _is_debit(txn):
        return ["There is not enough debit history to compare this transaction."]

    median = _median_amount(debits)
    city = _usual_city(debits)
    features = _features(txn, debits, median, city)
    reasons: list[str] = []
    amount = float(txn["amount"])
    if median and amount >= 3 * median:
        reasons.append(
            f"₹{amount:,.0f} is about {amount / median:.1f}× your usual debit of ₹{median:,.0f}."
        )
    hour = float(features[1])
    if hour < 5:
        reasons.append(
            f"This payment was made at {_timestamp(txn).strftime('%H:%M')}, an unusual overnight hour."
        )
    if features[2]:
        reasons.append("This is your first payment to this merchant.")
    if features[3]:
        reasons.append(f"This payment is in {txn.get('city')}, away from your usual city of {city}.")
    if features[4] > 0:
        reasons.append(
            f"There were {int(features[4])} other debit(s) in the 10 minutes before this payment."
        )
    if not reasons:
        reasons.append("The amount, merchant, time and location match your usual debit activity.")
    return reasons


def anomaly_score(txn: dict, history: list[dict]) -> float:
    """Compatibility name used by the fraud engine."""
    return score(txn, history)
