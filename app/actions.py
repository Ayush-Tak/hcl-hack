"""Pending actions: the model can only propose an action; it runs after the customer's OTP."""

import hashlib
import hmac
import json
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from app import db
from app.config import settings

logger = logging.getLogger(__name__)

OTP_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 3
TOO_MANY_ATTEMPTS = (
    "Too many wrong codes, so this request is locked. Please ask again for a new code."
)

# Each executor re-checks its own preconditions, because things can change between
# proposing the action and confirming it (a card already blocked, a scam flag raised).
Executor = Callable[[str, dict, str], dict]
EXECUTORS: dict[str, Executor] = {}


def executor(action_type: str) -> Callable[[Executor], Executor]:
    def register(fn: Executor) -> Executor:
        EXECUTORS[action_type] = fn
        return fn

    return register


def _hash_otp(action_id: str, otp: str) -> str:
    return hmac.new(action_id.encode(), otp.encode(), hashlib.sha256).hexdigest()


def create_pending(
    customer_id: str,
    action_type: str,
    args: dict,
    summary: str,
    trace_id: str,
    risk: str | None = None,
) -> dict:
    action_id = f"ACT-{db.next_counter('action'):06d}"
    otp = f"{secrets.randbelow(10**6):06d}"
    now = datetime.now()
    expires_at = (now + OTP_TTL).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO pending_actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            action_id,
            customer_id,
            action_type,
            db.dumps(args),
            summary,
            _hash_otp(action_id, otp),
            expires_at,
            0,
            "pending",
            None,
            risk,
            trace_id,
            now.isoformat(timespec="seconds"),
        ),
    )
    logger.info("pending action %s %s for %s", action_id, action_type, customer_id)
    return {
        "action_id": action_id,
        "type": action_type,
        "summary": summary,
        "requires_otp": True,
        "expires_at": expires_at,
        "demo_otp": otp if settings.demo_mode else None,
    }


def _claim(action_id: str) -> bool:
    """Move pending -> executing in one statement, so a double click can't run it twice."""
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE pending_actions SET status = 'executing' WHERE id = ? AND status = 'pending'",
            (action_id,),
        )
        return cur.rowcount == 1


def _use_attempt(action_id: str) -> bool:
    """Count the attempt before comparing, in one statement. If we read the count first,
    parallel guesses would all see 0 attempts and the 3-try limit wouldn't hold."""
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE pending_actions SET attempts = attempts + 1 "
            "WHERE id = ? AND status = 'pending' AND attempts < ?",
            (action_id, MAX_ATTEMPTS),
        )
        return cur.rowcount == 1


def _check_otp(action: dict, otp: str) -> str | None:
    if datetime.fromisoformat(action["expires_at"]) < datetime.now():
        db.execute(
            "UPDATE pending_actions SET status = 'expired' WHERE id = ?",
            (action["id"],),
        )
        return "This request has expired. Please ask again and we'll send a new code."
    if not _use_attempt(action["id"]):
        return TOO_MANY_ATTEMPTS
    if hmac.compare_digest(action["otp_hash"], _hash_otp(action["id"], otp.strip())):
        return None
    used = db.one("SELECT attempts FROM pending_actions WHERE id = ?", (action["id"],))[
        "attempts"
    ]
    left = MAX_ATTEMPTS - used
    if left == 0:
        db.execute(
            "UPDATE pending_actions SET status = 'locked' WHERE id = ? AND status = 'pending'",
            (action["id"],),
        )
        return TOO_MANY_ATTEMPTS
    return f"That code is not right. You have {left} attempt{'s' if left != 1 else ''} left."


def confirm(customer_id: str, action_id: str, otp: str, trace_id: str) -> dict:
    """Returns {"ok": True, "result"} or {"ok": False, "reason"}."""
    action = db.one(
        "SELECT * FROM pending_actions WHERE id = ? AND customer_id = ?",
        (action_id, customer_id),
    )
    # Unknown IDs and other customers' IDs get the same answer, so IDs can't be probed.
    if not action:
        return {"ok": False, "reason": "We couldn't find that request."}
    if action["status"] in ("done", "scheduled", "held"):
        return {"ok": True, "result": json.loads(action["result"]), "repeat": True}
    if action["status"] == "locked":
        return {"ok": False, "reason": TOO_MANY_ATTEMPTS}
    if action["status"] != "pending":
        return {"ok": False, "reason": f"This request is {action['status']}."}
    problem = _check_otp(action, otp)
    if problem:
        return {"ok": False, "reason": problem}
    if not _claim(action_id):
        return {"ok": False, "reason": "This request is already being processed."}
    result = EXECUTORS[action["type"]](
        customer_id, json.loads(action["args"]), trace_id
    )
    if result.get("error"):
        status = "failed"
    else:
        # "held" means the confirm-time re-check stopped it; "scheduled" means cooling-off.
        status = (
            result.get("status")
            if result.get("status") in ("scheduled", "held")
            else "done"
        )
    db.execute(
        "UPDATE pending_actions SET status = ?, result = ? WHERE id = ?",
        (status, db.dumps(result), action_id),
    )
    logger.info("action %s %s -> %s", action_id, action["type"], status)
    if result.get("error"):
        return {"ok": False, "reason": result["error"]}
    return {"ok": True, "result": result}
