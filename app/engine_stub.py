"""Engine stub returning deterministic shapes conforming to docs/ENGINE.md."""

from datetime import datetime, timedelta
from typing import Any

from app import db
from app.trace import Trace, load as load_trace

DEMO_OTP = "123456"


def handle_chat(customer_id: str, message: str, session_id: str) -> dict:
    """Simulate engine chat turn with accurate shapes and real trace recording."""
    tr = Trace(customer_id, message)
    tr.add("guard", "input_guard", ok=True, ms=2)
    lower = message.lower()

    if "block" in lower or "lost" in lower:
        tr.add("llm", "scam_guard", model="gemini-2.5-flash", tokens_in=280, tokens_out=15, ms=310, ok=True)
        tr.add("gate", "policy_gate", decision="allow", tier="action")
        tr.add("tool", "get_cards", args={"customer_id": customer_id}, result={"cards": ["XXXX-4821"]}, ms=5, ok=True)
        
        answer = "I can help you block your debit card ending in 4821 and dispatch a replacement. Please confirm with the OTP sent to your registered mobile (demo OTP: 123456)."
        answer_type = "action_pending"
        data = {"card": "XXXX-XXXX-XXXX-4821", "status": "ACTIVE"}
        citations: list[dict] = []
        pending_action = {
            "action_id": "ACT-000001",
            "type": "block_card",
            "summary": "Block debit card ending in 4821 and issue a replacement",
            "requires_otp": True,
            "expires_at": (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds"),
            "demo_otp": DEMO_OTP,
        }
        warnings = [{"source": "scam_guard", "level": "info", "message": "Card block requested. Ensure you recognize recent transactions."}]
        tools_invoked = [{"name": "get_cards", "ok": True, "ms": 5}]

    elif "phone" in lower or "bills" in lower or "20,000" in lower or "20000" in lower:
        tr.add("llm", "scam_guard", model="gemini-2.5-flash", tokens_in=310, tokens_out=18, ms=320, ok=True)
        tr.add("gate", "policy_gate", decision="allow", tier="read")
        tr.add("tool", "get_balance", args={}, result={"balance": 148250}, ms=4, ok=True)
        tr.add("tool", "get_upcoming_payments", args={}, result={"upcoming_total": 16238}, ms=6, ok=True)
        tr.add("tool", "calculate", args={"expression": "148250 - 16238"}, result={"remaining": 132012}, ms=1, ok=True)

        answer = "Your upcoming bills total Rs 16,238, which leaves Rs 1,32,012 of your Rs 1,48,250 balance. Yes, you can comfortably buy the Rs 20,000 phone."
        answer_type = "calculated"
        data = {"balance": 148250, "upcoming_total": 16238, "remaining": 132012, "budget": 20000, "balance_after": 112012}
        citations = []
        pending_action = None
        warnings = []
        tools_invoked = [
            {"name": "get_balance", "ok": True, "ms": 4},
            {"name": "get_upcoming_payments", "ok": True, "ms": 6},
            {"name": "calculate", "ok": True, "ms": 1},
        ]

    elif "ravi" in lower or "2,500" in lower or "2500" in lower or "debited" in lower:
        tr.add("llm", "scam_guard", model="gemini-2.5-flash", tokens_in=290, tokens_out=22, ms=330, ok=True)
        tr.add("gate", "policy_gate", decision="allow", tier="read")
        tr.add("tool", "check_upi_status", args={"payee": "Ravi Kumar", "amount": 2500}, result={"status": "DEBITED_NOT_CREDITED", "utr": "429182910283"}, ms=12, ok=True)
        tr.add("tool", "search_policy", args={"query": "UPI failed debit compensation"}, result={"tat": "T+1"}, ms=15, ok=True)

        answer = "The UPI payment of Rs 2,500 to Ravi Kumar (UTR 429182910283) was debited yesterday and is currently in reconciliation. Under RBI T+1 rules, if not credited by today 18:00, automatic compensation of Rs 100/day applies. Complaint ticket CMP-00041 has been created."
        answer_type = "retrieved_fact"
        data = {"txn_id": "T00289", "amount": 2500, "payee": "Ravi Kumar", "utr": "429182910283", "status": "DEBITED_NOT_CREDITED", "complaint_id": "CMP-00041"}
        citations = [{"doc": "rbi_failed_txns.md", "section": "UPI Harmonisation T+1", "version": "2024.1", "effective_date": "2024-01-01"}]
        pending_action = None
        warnings = [{"source": "verifier", "level": "info", "message": "Complaint ticket CMP-00041 logged. Resolution SLA: 24 hours."}]
        tools_invoked = [
            {"name": "check_upi_status", "ok": True, "ms": 12},
            {"name": "search_policy", "ok": True, "ms": 15},
        ]

    elif "48,999" in lower or "recognise" in lower or "recognize" in lower:
        tr.add("llm", "scam_guard", model="gemini-2.5-flash", tokens_in=305, tokens_out=25, ms=340, ok=True)
        tr.add("gate", "policy_gate", decision="allow", tier="action")
        tr.add("tool", "get_fraud_score", args={"txn_id": "T00294"}, result={"score": 0.85, "band": "high"}, ms=8, ok=True)

        answer = "We detected a suspicious payment of Rs 48,999 at 02:14 in Dubai to a new merchant. We strongly recommend blocking your debit card ending in 4821 immediately."
        answer_type = "action_pending"
        data = {"txn_id": "T00294", "amount": 48999, "merchant": "Dubai Electronics", "score": 0.85, "band": "high"}
        citations = []
        pending_action = {
            "action_id": "ACT-000001",
            "type": "block_card",
            "summary": "Block debit card ending in 4821 and dispute Rs 48,999 Dubai transaction",
            "requires_otp": True,
            "expires_at": (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds"),
            "demo_otp": DEMO_OTP,
        }
        warnings = [{"source": "scam_guard", "level": "high", "message": "High fraud risk score (0.85). Made abroad from unfamiliar IP."}]
        tools_invoked = [{"name": "get_fraud_score", "ok": True, "ms": 8}]

    elif "otp" in lower or "called" in lower or "frozen" in lower:
        tr.add("llm", "scam_guard", model="gemini-2.5-flash", tokens_in=320, tokens_out=30, ms=350, ok=True)
        tr.add("gate", "policy_gate", decision="block", tier="action")

        answer = "CRITICAL ALERT: Bank staff will NEVER call and request your OTP, PIN, or demand an urgent fund transfer. This is a known impersonation scam. Do not share your OTP or send money."
        answer_type = "escalated"
        data = {"threat_type": "bank_impersonation_vishing", "action_taken": "funds_protected"}
        citations = [{"doc": "cyber_fraud_advisory.md", "section": "Vishing & Impersonation", "version": "2025.2", "effective_date": "2025-06-01"}]
        pending_action = None
        warnings = [{"source": "scam_guard", "level": "high", "message": "Bank impersonation scam detected. Case escalated to Fraud Operations team."}]
        tools_invoked = [{"name": "scam_classifier", "ok": True, "ms": 14}]

    else:
        tr.add("llm", "orchestrator", model="gemini-2.5-flash", tokens_in=250, tokens_out=25, ms=280, ok=True)
        tr.add("tool", "get_balance", args={}, result={"balance": 148250}, ms=4, ok=True)

        answer = "Hello! Your total available balance is Rs 1,48,250 across your primary savings account. How can I assist you with your finances today?"
        answer_type = "retrieved_fact"
        data = {"balance": 148250}
        citations = []
        pending_action = None
        warnings = []
        tools_invoked = [{"name": "get_balance", "ok": True, "ms": 4}]

    tr.finish(answer=answer, answer_type=answer_type, mode="stub")

    return {
        "trace_id": tr.id,
        "answer": answer,
        "answer_type": answer_type,
        "data": data,
        "citations": citations,
        "pending_action": pending_action,
        "warnings": warnings,
        "tools_invoked": tools_invoked,
        "trace_url": f"/ops/traces/{tr.id}",
        "latency_ms": tr.elapsed_ms(),
    }


def confirm_action(customer_id: str, action_id: str, otp: str) -> dict:
    """Run confirmation check on pending action."""
    tr = Trace(customer_id, f"Confirm action {action_id} with OTP")
    tr.add("gate", "otp_gate", action_id=action_id)

    if otp.strip() == DEMO_OTP:
        tr.add("action", "block_card", status="BLOCKED", replacement_id="RPL-000004", ms=20, ok=True)
        answer = "Your card ending 4821 has been blocked immediately. Replacement card RPL-000004 has been issued."
        answer_type = "action_completed"
        data = {"card": "XXXX-XXXX-XXXX-4821", "status": "BLOCKED", "replacement_id": "RPL-000004"}
        warnings: list[dict] = []
        tools = [{"name": "block_card", "ok": True, "ms": 20}]
    else:
        tr.add("action", "verify_otp", ok=False, error="Invalid OTP", ms=5)
        answer = "Invalid OTP provided. Please enter the valid 6-digit OTP (demo OTP: 123456)."
        answer_type = "refused"
        data = {"attempts_left": 2}
        warnings = [{"source": "verifier", "level": "high", "message": "Incorrect OTP entered"}]
        tools = []

    tr.finish(answer=answer, answer_type=answer_type, mode="stub")

    return {
        "trace_id": tr.id,
        "answer": answer,
        "answer_type": answer_type,
        "data": data,
        "citations": [],
        "pending_action": None,
        "warnings": warnings,
        "tools_invoked": tools,
        "trace_url": f"/ops/traces/{tr.id}",
        "latency_ms": tr.elapsed_ms(),
    }


def ingest_transaction(customer_id: str, txn: dict) -> dict:
    """Validate and score incoming transaction."""
    amount = txn.get("amount")
    merchant = txn.get("merchant")
    if amount is None or not merchant:
        raise ValueError("Transaction missing required fields: amount and merchant")

    score = 0.85 if int(amount) > 40000 else 0.12
    band = "high" if score > 0.7 else "low"
    alert_id = "AL-000012" if band == "high" else None
    reasons = [
        f"Rs {amount} is significantly above usual average",
        "first payment to this merchant",
    ] if band == "high" else []

    return {
        "txn_id": "T00294",
        "score": score,
        "band": band,
        "reasons": reasons,
        "alert_id": alert_id,
    }


def get_trace(trace_id: str) -> dict | None:
    """Retrieve trace header and audit spans."""
    head = load_trace(trace_id)
    if not head:
        return None
    head["chain_ok"] = True
    return head


def list_alerts(customer_id: str | None = None) -> list[dict]:
    """List alerts for customer or for operations queue."""
    rows = db.query(
        "SELECT id, customer_id, txn_id, score, band, reasons, analyst, status, created_at, trace_id "
        "FROM alerts ORDER BY created_at DESC"
    )
    if rows:
        alerts = []
        for r in rows:
            if customer_id and r["customer_id"] != customer_id:
                continue
            reasons = r["reasons"]
            if isinstance(reasons, str) and reasons.startswith("["):
                try:
                    import json
                    reasons = json.loads(reasons)
                except Exception:
                    reasons = [reasons]
            alerts.append(dict(r) | {"reasons": reasons if isinstance(reasons, list) else [str(reasons)]})
        if alerts:
            return alerts

    # Fallback demo alert if table is empty
    return [
        {
            "id": "AL-000012",
            "customer_id": customer_id or "C1001",
            "txn_id": "T00294",
            "score": 0.85,
            "band": "high",
            "reasons": [
                "Rs 48,999 is 9x your usual shopping spend",
                "first payment to this merchant",
                "made at 02:14 in Dubai, away from Jaipur",
            ],
            "analyst": "Looks like card-not-present fraud: unusual foreign merchant and off-hours timing.",
            "status": "open",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "trace_id": "BANK-20261024-000124",
        }
    ]


def metrics() -> dict:
    """Return dashboard metrics."""
    return {
        "requests": 42,
        "latency_ms": {"p50": 1650, "p95": 4100},
        "llm_calls": 118,
        "tokens_in": 96000,
        "tokens_out": 5200,
        "fallback_rate": 0.05,
        "verifier_pass_rate": 0.97,
        "alerts": {"low": 0, "medium": 3, "high": 2},
        "open_cases": 1,
        "audit_chain": {"ok": True, "rows": 512},
    }
