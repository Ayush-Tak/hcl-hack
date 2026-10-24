"""The interface teammate 2's routes call (docs/ENGINE.md). Routes hold no banking logic."""

import json
import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime

import httpx
from google.genai import errors as genai_errors

from app import actions, db, sessions, trace
from app.agents import fallback, orchestrator, scam_guard, verifier
from app.agents.llm import GeminiSession, LLMError, llm_available
from app.fraud.engine import assess_and_store
from app.models import ChatResponse, Citation, PendingAction, ToolInvoked, Warning
from app.tools import banking, calc, payments, policy, protection, registry  # noqa: F401  (registers tools)
from app.trace import Trace

logger = logging.getLogger(__name__)

ORCHESTRATOR_ERRORS = (genai_errors.APIError, httpx.HTTPError, LLMError)
_CUSTOMER_ID = re.compile(r"\b[CO]\d{4}\b", re.IGNORECASE)
CLARIFY = "Could you tell me a bit more about what you need? For example the payee, amount or card."
# Scam Guard's model call runs here while the Orchestrator works on the same message.
_SIDE_POOL = ThreadPoolExecutor(max_workers=4)
SCAM_GUARD_WAIT_S = 5


def _refusal(customer_id: str, message: str, trace_: Trace) -> str | None:
    others = {m.upper() for m in _CUSTOMER_ID.findall(message)} - {customer_id}
    if others:
        trace_.add("guard", "privacy", decision="refuse", mentioned=sorted(others))
        return "I can only help with your own accounts, so I can't share anything about another customer."
    return None


def _start_scam_guard(ctx: registry.Ctx, message: str) -> Future:
    # The regex result lands before any tool runs; the model's verdict may come later, which
    # is why a transfer re-checks the flag when it's confirmed.
    scam_guard.apply(ctx.session, scam_guard.fast_check(message), ctx.trace, "regex")
    return _SIDE_POOL.submit(scam_guard.classify, message, ctx.trace)


def _finish_scam_guard(ctx: registry.Ctx, pending: Future) -> None:
    try:
        verdict = pending.result(timeout=SCAM_GUARD_WAIT_S)
    except TimeoutError:
        ctx.trace.add("agent", "scam_guard", skipped="timeout")
        return
    except Exception:
        # Scam Guard must fail safe: a bug in it may not take the chat down. It's logged with
        # the stack trace and recorded in the trace, and the regex result still stands.
        logger.exception("scam guard failed (trace %s)", ctx.trace.id)
        ctx.trace.add("agent", "scam_guard", ok=False, skipped="error")
        return
    scam_guard.apply(ctx.session, verdict, ctx.trace, "llm")


def _run_agent(
    ctx: registry.Ctx, message: str
) -> tuple[str | None, str, GeminiSession | None]:
    """Returns (answer, mode, session). Falls back to rules when the LLM is off or fails."""
    if llm_available():
        try:
            answer, session = orchestrator.run(ctx, message, ctx.session["memory"])
            return answer, "agent", session
        except ORCHESTRATOR_ERRORS as exc:
            logger.warning("LLM failed, using rules fallback: %s", str(exc)[:300])
            ctx.trace.add("llm", "orchestrator_failed", ok=False, error=str(exc)[:300])
    return fallback.run(ctx, message), "fallback", None


def _verify(
    ctx: registry.Ctx, answer: str, message: str, session: GeminiSession | None
) -> tuple[str, list[str], list[str] | None]:
    """Amount check, one repair turn, then the template. Returns (answer, unchecked numbers, claim problems)."""
    unknown = verifier.unknown_amounts(answer, ctx.calls, message)
    if unknown and session is not None:
        try:
            repaired = orchestrator.repair(session, unknown)
        except ORCHESTRATOR_ERRORS as exc:
            logger.warning("repair turn failed: %s", str(exc)[:200])
            repaired = None
        if repaired:
            answer, unknown = (
                repaired,
                verifier.unknown_amounts(repaired, ctx.calls, message),
            )
    ctx.trace.add("verifier", "amount_check", ok=not unknown, unknown=unknown)
    if unknown:
        answer = verifier.template_answer(ctx.calls)
    return answer, unknown, verifier.check_claims(answer, ctx.calls, ctx.trace)


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


def _warnings(
    ctx: registry.Ctx, mode: str, unknown: list[str], claims: list[str] | None
) -> list[Warning]:
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
                level="info",
                message="Some numbers couldn't be checked, so this answer shows the exact figures instead.",
            )
        )
    if claims:
        warnings.append(
            Warning(
                source="verifier",
                level="high",
                message="Not confirmed by policy: " + "; ".join(claims),
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
    ctx = registry.Ctx(customer_id, trace_, sessions.get(customer_id, session_id))
    scam_check = _start_scam_guard(ctx, message)
    answer, mode, session = _run_agent(ctx, message)
    _finish_scam_guard(ctx, scam_check)
    answer, unknown, claims = _verify(
        ctx, answer if answer is not None else CLARIFY, message, session
    )
    memory = ctx.session["memory"] + [{"user": message, "assistant": answer}]
    ctx.session["memory"] = memory[-orchestrator.MEMORY_TURNS :]
    return _respond(
        trace_,
        answer,
        _answer_type(ctx.calls),
        mode,
        data={c["name"]: c["result"] for c in ctx.calls if c["ok"]} or None,
        citations=_citations(ctx.calls),
        pending_action=_pending(ctx.calls),
        warnings=_warnings(ctx, mode, unknown, claims),
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
    if result.get("held") or result.get("status") == "held":
        return (
            f"We've held this payment for your safety ({'; '.join(result['signals'])}). "
            f"Case {result['case_id']} is with our team, and they'll call you. "
            "The bank never asks you to move money or share an OTP."
        )
    if result.get("status") == "sent":
        return f"Sent. Reference {result['txn_id']}."
    return "Done."


def confirm_action(customer_id: str, action_id: str, otp: str) -> ChatResponse:
    trace_ = Trace(customer_id, f"confirm {action_id}")
    with trace_.span("action", "confirm", action_id=action_id) as out:
        outcome = actions.confirm(customer_id, action_id, otp, trace_.id)
        out["ok"] = outcome["ok"]
    if not outcome["ok"]:
        return _respond(trace_, outcome["reason"], "refused", "action")
    answer_type = (
        "escalated" if outcome["result"].get("status") == "held" else "action_completed"
    )
    return _respond(
        trace_,
        _completed_text(outcome["result"]),
        answer_type,
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
        verdict = assess_and_store(customer_id, row, trace_)
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
    return sorted_values[
        min(len(sorted_values) - 1, len(sorted_values) * percent // 100)
    ]


def metrics() -> dict:
    rows = db.query("SELECT * FROM traces WHERE mode IN ('agent', 'fallback')")
    latencies = sorted(r["latency_ms"] for r in rows)
    bands = {
        r["band"]: r["n"]
        for r in db.query("SELECT band, COUNT(*) AS n FROM alerts GROUP BY band")
    }
    return {
        "requests": len(rows),
        "latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
        },
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "tokens_in": sum(r["tokens_in"] for r in rows),
        "tokens_out": sum(r["tokens_out"] for r in rows),
        "llm_calls_per_request": round(sum(r["llm_calls"] for r in rows) / len(rows), 2)
        if rows
        else 0,
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
