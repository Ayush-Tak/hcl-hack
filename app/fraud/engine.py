"""Fraud score = rules + (when teammate 3's model exists) an IsolationForest signal."""

import importlib
import importlib.util
import logging
from datetime import datetime, timedelta

from app import db
from app.fraud.rules import rule_signals

logger = logging.getLogger(__name__)

ANOMALY_WEIGHT = 0.15
ANOMALY_PERCENTILE = 0.95
HIGH, MEDIUM = 0.6, 0.3

# Look the module up instead of try/except ImportError, so a real error inside ml.py
# (say, scikit-learn missing) still shows up rather than silently switching ML off.
_ml = (
    importlib.import_module("app.fraud.ml")
    if importlib.util.find_spec("app.fraud.ml")
    else None
)


def band_for(score: float) -> str:
    if score >= HIGH:
        return "high"
    return "medium" if score >= MEDIUM else "low"


def _history(customer_id: str, txn: dict) -> list[dict]:
    since = (datetime.fromisoformat(txn["ts"]) - timedelta(days=90)).isoformat()
    return db.query(
        "SELECT * FROM transactions WHERE customer_id = ? AND direction = 'debit' "
        "AND ts >= ? AND ts < ? AND id != ?",
        (customer_id, since, txn["ts"], txn.get("id", "")),
    )


def assess(customer_id: str, txn: dict) -> dict:
    home = db.one("SELECT home_city FROM customers WHERE id = ?", (customer_id,))
    history = _history(customer_id, txn)
    signals = rule_signals(txn, history, home["home_city"] if home else "")
    if _ml is not None and _ml.score(txn, history) >= ANOMALY_PERCENTILE:
        signals.append((ANOMALY_WEIGHT, "unusual compared with your last 90 days"))
    score = round(min(1.0, sum(weight for weight, _ in signals)), 2)
    return {
        "score": score,
        "band": band_for(score),
        "reasons": [reason for _, reason in signals],
    }


def assess_and_store(customer_id: str, txn: dict, trace_id: str | None) -> dict:
    """Score a stored transaction, save the score, and open an alert for medium or high."""
    verdict = assess(customer_id, txn)
    db.execute(
        "UPDATE transactions SET fraud_score = ?, fraud_band = ? WHERE id = ?",
        (verdict["score"], verdict["band"], txn["id"]),
    )
    alert_id = None
    if verdict["band"] != "low":
        existing = db.one("SELECT id FROM alerts WHERE txn_id = ?", (txn["id"],))
        alert_id = (
            existing["id"]
            if existing
            else _open_alert(customer_id, txn, verdict, trace_id)
        )
    logger.info("fraud %s score %.2f %s", txn["id"], verdict["score"], verdict["band"])
    return verdict | {"alert_id": alert_id}


def _open_alert(
    customer_id: str, txn: dict, verdict: dict, trace_id: str | None
) -> str:
    alert_id = f"AL-{db.next_counter('alert'):06d}"
    db.execute(
        "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            alert_id,
            customer_id,
            txn["id"],
            verdict["score"],
            verdict["band"],
            db.dumps(verdict["reasons"]),
            None,
            "open",
            datetime.now().isoformat(timespec="seconds"),
            trace_id,
        ),
    )
    return alert_id
