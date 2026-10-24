"""The interface teammate 2's routes call (docs/ENGINE.md). Routes hold no banking logic."""

import importlib
import importlib.util
import json
import logging
import re
from datetime import datetime

import httpx
from google.genai import errors as genai_errors

from app import actions, db, trace
from app.agents import fallback, orchestrator
from app.agents.llm import LLMError, llm_available
from app.fraud.engine import assess_and_store
from app.models import ChatResponse, Citation, PendingAction, ToolInvoked, Warning
from app.tools import banking, calc, payments, policy, protection, registry  # noqa: F401  (registers tools)
from app.trace import Trace

logger = logging.getLogger(__name__)

_guard = (
    importlib.import_module("app.guard")
    if importlib.util.find_spec("app.guard")
    else None
)

# Per-session memory and flags. In memory for the demo; Redis in production (design.md section 10).
_SESSIONS: dict[tuple[str, str], dict] = {}

# Keyword stand-in for the Scam Guard agent (D-17): each group is one social-engineering pattern.
SCAM_PATTERNS = {
    "bank or police impersonation": r"bank (officer|manager|staff)|from the bank|police|cbi|customs|rbi officer",
    "urgency or threats": r"frozen|freeze|blocked today|immediately|urgent|arrest|last chance",
    "asks for an OTP or code": r"(share|tell|read|give).{0,20}(otp|code|pin)",
    "remote access app": r"anydesk|teamviewer|quick ?support|screen ?shar",
    "KYC update story": r"\bkyc\b",
}
_CUSTOMER_ID = re.compile(r"\b[CO]\d{4}\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SMALL_NUMBER = 10  # days, counts and "T+1" are fine; we check amounts and IDs


def _session(customer_id: str, session_id: str) -> dict:
    return _SESSIONS.setdefault(
        (customer_id, session_id),
        {"memory": [], "scam_risk": "none", "scam_patterns": []},
    )


def _scam_check(ctx: registry.Ctx, message: str) -> None:
    found = [
        name
        for name, pattern in SCAM_PATTERNS.items()
        if re.search(pattern, message, re.IGNORECASE)
    ]
    risky = len(found) >= 2 or {"asks for an OTP or code", "remote access app"} & set(
        found
    )
    if found:
        # The flag only goes up within a session: a calm follow-up message doesn't clear it.
        ctx.session["scam_risk"] = (
            "high" if risky or ctx.session["scam_risk"] == "high" else "low"
        )
        ctx.session["scam_patterns"] = sorted(
            set(ctx.session["scam_patterns"]) | set(found)
        )
    ctx.trace.add("guard", "scam_check", risk=ctx.session["scam_risk"], patterns=found)


def _refusal(customer_id: str, message: str, trace_: Trace) -> str | None:
    others = {m.upper() for m in _CUSTOMER_ID.findall(message)} - {customer_id}
    if others:
        trace_.add("guard", "privacy", decision="refuse", mentioned=sorted(others))
        return "I can only help with your own accounts, so I can't share anything about another customer."
    if _guard is not None:
        try:
            _guard.check(message)
        except ValueError as exc:
            trace_.add("guard", "input_guard", decision="refuse", reason=str(exc))
            return "I can't help with that request."
    return None


def _run_agent(ctx: registry.Ctx, message: str) -> tuple[str | None, str]:
    """Returns (answer, mode). Falls back to rules when the LLM is off or fails."""
    if llm_available():
        try:
            return orchestrator.run(ctx, message, ctx.session["memory"]), "agent"
        except (genai_errors.APIError, httpx.HTTPError, LLMError) as exc:
            logger.warning("LLM failed, using rules fallback: %s", exc)
            ctx.trace.add("llm", "orchestrator_failed", ok=False, error=str(exc)[:300])
    return fallback.run(ctx, message), "fallback"


def _answer_type(calls: list[dict]) -> str:
    results = [c["result"] for c in calls]
    if any(r.get("pending_action") for r in results):
        return "action_pending"
    if any(r.get("escalated") for r in results):
        return "escalated"
    if any(c["name"] == "search_policy" and c["result"].get("results") for c in calls):
        return "retrieved_fact"
    if any(c["name"] == "calculate" and "value" in c["result"] for c in calls):
        return "calculated"
    if not results or any(r.get("clarify") for r in results):
        return "clarification_needed"
    if all(r.get("not_found") or r.get("error") for r in results):
        return "not_found"
    return "retrieved_fact"


def _numbers_in(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_numbers_in(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_numbers_in(v) for v in value))
    if isinstance(value, bool) or value is None:
        return set()
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(str(value))}


def _unknown_amounts(answer: str, calls: list[dict], message: str) -> list[str]:
    """The code half of the Verifier (D-14): every number in the reply must come from a tool or the customer."""
    known = _numbers_in([c["result"] for c in calls]) | _numbers_in(message)
    known |= {k.split(".")[0] for k in known}
    found = {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(answer)}
    return sorted(n for n in found if float(n) >= SMALL_NUMBER and n not in known)


def _warnings(ctx: registry.Ctx, mode: str, unknown: list[str]) -> list[Warning]:
    warnings = []
    if ctx.session["scam_risk"] == "high":
        warnings.append(
            Warning(
                source="scam_guard",
                level="high",
                message="This looks like a common scam ("
                + ", ".join(ctx.session["scam_patterns"])
                + "). The bank never asks you to move money or share an OTP.",
            )
        )
    for call in ctx.calls:
        if call["name"] == "transfer_money" and call["result"].get("signals"):
            level = "info" if call["result"]["decision"] == "step_up" else "high"
            warnings.append(
                Warning(
                    source="pre_payment",
                    level=level,
                    message="; ".join(call["result"]["signals"]),
                )
            )
    if mode == "fallback":
        warnings.append(
            Warning(
                source="fallback",
                level="info",
                message="Running in basic mode; answers are simpler.",
            )
        )
    if unknown:
        warnings.append(
            Warning(
                source="verifier",
                level="high",
                message="Some numbers in this answer could not be checked: "
                + ", ".join(unknown),
            )
        )
    return warnings


def _citations(calls: list[dict]) -> list[Citation]:
    hits = [
        h
        for c in calls
        if c["name"] == "search_policy"
        for h in c["result"].get("results", [])
    ]
    return [
        Citation(
            doc=h["doc"],
            section=h["section"],
            version=h["version"],
            effective_date=h["effective_date"],
        )
        for h in hits
    ]


def _pending(calls: list[dict]) -> PendingAction | None:
    pendings = [
        c["result"]["pending_action"]
        for c in calls
        if c["result"].get("pending_action")
    ]
    return PendingAction(**pendings[-1]) if pendings else None


def _respond(
    trace_: Trace, answer: str, answer_type: str, mode: str, **fields: object
) -> ChatResponse:
    trace_.finish(answer, answer_type, mode)
    return ChatResponse(
        trace_id=trace_.id,
        answer=answer,
        answer_type=answer_type,
        trace_url=f"/ops/traces/{trace_.id}",
        latency_ms=trace_.elapsed_ms(),
        **fields,
    )


def handle_chat(customer_id: str, message: str, session_id: str) -> ChatResponse:
    trace_ = Trace(customer_id, message)
    refusal = _refusal(customer_id, message, trace_)
    if refusal:
        return _respond(trace_, refusal, "refused", "guard")
    ctx = registry.Ctx(customer_id, trace_, _session(customer_id, session_id))
    _scam_check(ctx, message)
    answer, mode = _run_agent(ctx, message)
    if answer is None:
        answer = "Could you tell me a bit more about what you need? For example the payee, amount or card."
    answer_type = _answer_type(ctx.calls)
    unknown = _unknown_amounts(answer, ctx.calls, message)
    trace_.add("verifier", "amount_check", ok=not unknown, unknown=unknown)
    ctx.session["memory"] = (
        ctx.session["memory"] + [{"user": message, "assistant": answer}]
    )[-orchestrator.MEMORY_TURNS :]
    return _respond(
        trace_,
        answer,
        answer_type,
        mode,
        data={c["name"]: c["result"] for c in ctx.calls if c["ok"]} or None,
        citations=_citations(ctx.calls),
        pending_action=_pending(ctx.calls),
        warnings=_warnings(ctx, mode, unknown),
        tools_invoked=[
            ToolInvoked(name=s["name"], ok=s.get("ok", True), ms=s.get("ms", 0))
            for s in trace_.spans
            if s["kind"] == "tool"
        ],
    )


def _completed_text(result: dict) -> str:
    if result.get("status") == "BLOCKED":
        return (
            f"Your card {result['card']} is blocked. Replacement {result['replacement_id']} "
            f"will arrive in {result['delivery']}."
        )
    if result.get("status") == "scheduled":
        return f"Your payment is scheduled and will be released at {result['release_at']}. You can still cancel it."
    if result.get("status") == "sent":
        return f"Sent. Reference {result['txn_id']}."
    return "Done."


def confirm_action(customer_id: str, action_id: str, otp: str) -> ChatResponse:
    trace_ = Trace(customer_id, f"confirm {action_id}")
    with trace_.span("action", "confirm", action_id=action_id) as out:
        outcome = actions.confirm(customer_id, action_id, otp)
        out["ok"] = outcome["ok"]
    if not outcome["ok"]:
        return _respond(trace_, outcome["reason"], "refused", "action")
    return _respond(
        trace_,
        _completed_text(outcome["result"]),
        "action_completed",
        "action",
        data=outcome["result"],
    )


def ingest_transaction(customer_id: str, txn: dict) -> dict:
    if not txn.get("amount") or not txn.get("merchant"):
        raise ValueError("amount and merchant are required")
    trace_ = Trace(customer_id, f"ingest {txn['merchant']} {txn['amount']}")
    account = db.one("SELECT id FROM accounts WHERE customer_id = ?", (customer_id,))
    row = {
        "id": f"T8{db.next_counter('ingest'):04d}",
        "account_id": account["id"],
        "customer_id": customer_id,
        "ts": txn.get("ts") or datetime.now().isoformat(timespec="seconds"),
        "amount": int(txn["amount"]),
        "direction": txn.get("direction", "debit"),
        "merchant": txn["merchant"],
        "category": txn.get("category", "shopping"),
        "channel": txn.get("channel", "CARD"),
        "city": txn.get("city"),
        "status": txn.get("status", "SUCCESS"),
        "utr": None,
        "counterparty": txn.get("counterparty"),
    }
    db.execute(
        f"INSERT INTO transactions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )
    with trace_.span("tool", "fraud_score", txn_id=row["id"]) as out:
        verdict = assess_and_store(customer_id, row, trace_.id)
        out["result"] = verdict
    trace_.finish(f"{verdict['band']} risk", "retrieved_fact", "ingest")
    return {"txn_id": row["id"]} | verdict


def get_trace(trace_id: str) -> dict | None:
    loaded = trace.load(trace_id)
    return loaded | {"chain_ok": trace.verify_chain()["ok"]} if loaded else None


def list_alerts(customer_id: str | None) -> list[dict]:
    sql, params = "SELECT * FROM alerts", ()
    if customer_id:
        sql, params = sql + " WHERE customer_id = ?", (customer_id,)
    rows = db.query(sql + " ORDER BY created_at DESC", params)
    return [r | {"reasons": json.loads(r["reasons"])} for r in rows]


def _percentile(sorted_values: list[int], percent: int) -> int:
    if not sorted_values:
        return 0
    return sorted_values[min(len(sorted_values) - 1, len(sorted_values) * percent // 100)]


def metrics() -> dict:
    rows = db.query("SELECT * FROM traces WHERE mode IN ('agent', 'fallback')")
    latencies = sorted(r["latency_ms"] for r in rows)
    bands = {
        r["band"]: r["n"]
        for r in db.query("SELECT band, COUNT(*) AS n FROM alerts GROUP BY band")
    }
    return {
        "requests": len(rows),
        "latency_ms": {"p50": _percentile(latencies, 50), "p95": _percentile(latencies, 95)},
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "tokens_in": sum(r["tokens_in"] for r in rows),
        "tokens_out": sum(r["tokens_out"] for r in rows),
        "fallback_rate": round(
            sum(r["mode"] == "fallback" for r in rows) / len(rows), 2
        )
        if rows
        else 0,
        "alerts": {band: bands.get(band, 0) for band in ("low", "medium", "high")},
        "open_cases": db.one("SELECT COUNT(*) AS n FROM cases WHERE status = 'open'")[
            "n"
        ],
        "audit_chain": trace.verify_chain(),
    }


def get_summary(customer_id: str) -> dict:
    account = db.one(
        "SELECT number_masked, balance FROM accounts WHERE customer_id = ?",
        (customer_id,),
    )
    recent = db.query(
        "SELECT id, ts, amount, direction, merchant, status FROM transactions "
        "WHERE customer_id = ? ORDER BY ts DESC LIMIT 5",
        (customer_id,),
    )
    return {
        "balance": account["balance"],
        "account_masked": account["number_masked"],
        "recent": recent,
    }
