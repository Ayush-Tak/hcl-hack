"""Signed demo tokens. Identity always comes from the token, never from the chat message."""

import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta

from fastapi import Header, HTTPException

from app import db
from app.config import settings

TTL_S = 8 * 3600
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


def is_locked(customer_id: str) -> bool:
    row = db.one(
        "SELECT failed_attempts, locked_until FROM login_lockouts WHERE customer_id = ?",
        (customer_id,),
    )
    if not row or not row["locked_until"]:
        return False
    try:
        locked_until = datetime.fromisoformat(row["locked_until"])
        if datetime.now() < locked_until:
            return True
    except ValueError:
        pass
    # Expired lockout: reset
    db.execute(
        "UPDATE login_lockouts SET failed_attempts = 0, locked_until = NULL WHERE customer_id = ?",
        (customer_id,),
    )
    return False


def record_failed_login(customer_id: str) -> None:
    now = datetime.now()
    row = db.one(
        "SELECT failed_attempts FROM login_lockouts WHERE customer_id = ?",
        (customer_id,),
    )
    attempts = (row["failed_attempts"] if row else 0) + 1
    locked_until = (
        (now + timedelta(minutes=LOCKOUT_MINUTES)).isoformat(timespec="seconds")
        if attempts >= LOCKOUT_THRESHOLD
        else None
    )
    db.execute(
        "INSERT INTO login_lockouts(customer_id, failed_attempts, locked_until) VALUES (?, ?, ?) "
        "ON CONFLICT(customer_id) DO UPDATE SET failed_attempts = ?, locked_until = ?",
        (customer_id, attempts, locked_until, attempts, locked_until),
    )


def record_successful_login(customer_id: str) -> None:
    db.execute(
        "DELETE FROM login_lockouts WHERE customer_id = ?",
        (customer_id,),
    )


def _sign(body: str) -> str:
    return hmac.new(
        settings.token_secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()[:32]


def hash_pin(customer_id: str, pin: str) -> str:
    # The customer ID is the salt, so two customers with PIN 1234 store different hashes.
    return hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), customer_id.encode(), 100_000
    ).hex()


def issue(customer_id: str, role: str) -> str:
    body = f"{customer_id}|{role}|{int(time.time()) + TTL_S}"
    return base64.urlsafe_b64encode(f"{body}|{_sign(body)}".encode()).decode()


def login(customer_id: str, pin: str) -> dict | None:
    cid = customer_id.strip().upper()
    if is_locked(cid):
        return None

    row = db.one(
        "SELECT id, name, role, pin_hash FROM customers WHERE id = ?",
        (cid,),
    )
    if not row or not hmac.compare_digest(row["pin_hash"], hash_pin(row["id"], pin)):
        record_failed_login(cid)
        return None

    record_successful_login(cid)
    return {
        "token": issue(row["id"], row["role"]),
        "customer": {"id": row["id"], "name": row["name"], "role": row["role"]},
    }


def current_user(authorization: str = Header(default="")) -> dict:
    try:
        raw = base64.urlsafe_b64decode(
            authorization.removeprefix("Bearer ").strip().encode()
        ).decode()
        customer_id, role, exp, sig = raw.split("|")
    except (
        ValueError
    ):  # bad base64, bad UTF-8 and a wrong part count are all ValueErrors
        raise HTTPException(401, "Missing or invalid token") from None
    body = f"{customer_id}|{role}|{exp}"
    if not hmac.compare_digest(sig, _sign(body)) or int(exp) < time.time():
        raise HTTPException(401, "Token expired or invalid")
    return {"customer_id": customer_id, "role": role}


def require_officer(user: dict) -> None:
    if user["role"] != "officer":
        raise HTTPException(403, "Officer role required")
