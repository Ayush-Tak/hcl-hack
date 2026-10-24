"""Fraud score = rules + (when teammate 3's model exists) an IsolationForest signal."""

import importlib
import importlib.util
import logging
from datetime import datetime, timedelta

from app import db
from app.agents import fraud_analyst
from app.fraud.rules import rule_signals
from app.trace import Trace

logger = logging.getLogger(__name__)

ANOMALY_WEIGHT = 0.15
# ml.score() is a risk from 0 to 1; teammate 3's tests put normal payments under 0.3 and clear anomalies over 0.7.
ANOMALY_RISK = 0.7
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
    model_reasons = _model_reasons(txn, history, signals)
    score = round(min(1.0, sum(weight for weight, _ in signals)), 2)
    return {
        "score": score,
        "band": band_for(score),
        "reasons": [reason for _, reason in signals] + model_reasons,
    }


def _model_reasons(
    txn: dict, history: list[dict], signals: list[tuple[float, str]]
) -> list[str]:
    """Teammate 3's IsolationForest: one weighted signal when it calls the payment anomalous,
    plus its own explanations, labelled so a reviewer can tell the two detectors apart."""
    if _ml is None:
        return []
    risk = _ml.score(txn, history)
    if risk < ANOMALY_RISK:
        return []
    signals.append(
        (
            ANOMALY_WEIGHT,
            f"unusual compared with your last 90 days (model risk {risk:.2f})",
        )
    )
    return [f"Model: {reason}" for reason in _ml.explain(txn, history)]


def assess_and_store(customer_id: str, txn: dict, trace: Trace) -> dict:
    """Score a stored transaction, get the analyst's view on medium or high, save it, open an alert."""
    verdict = assess(customer_id, txn) | {"analyst": None}
    if verdict["band"] != "low":
        home = db.one("SELECT home_city FROM customers WHERE id = ?", (customer_id,))
        verdict = fraud_analyst.review(
            txn, verdict, home["home_city"] if home else "", trace
        )
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
            else _open_alert(customer_id, txn, verdict, trace.id)
        )
    logger.info("fraud %s score %.2f %s", txn["id"], verdict["score"], verdict["band"])
    return verdict | {"alert_id": alert_id}


def _open_alert(customer_id: str, txn: dict, verdict: dict, trace_id: str) -> str:
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
            verdict["analyst"],
            "open",
            datetime.now().isoformat(timespec="seconds"),
            trace_id,
        ),
    )
    return alert_id
