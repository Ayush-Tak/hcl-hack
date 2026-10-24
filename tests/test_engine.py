from datetime import date

from app import actions, db, engine, sessions
from app.agents import verifier
from app.agents.fraud_analyst import AnalystVerdict
from app.agents.scam_guard import ScamVerdict
from app.tools import registry
from app.tools.payments import rbi_deadline
from app.trace import Trace


def _ctx(customer_id: str = "C1001", session_id: str = "test") -> registry.Ctx:
    return registry.Ctx(
        customer_id, Trace(customer_id, "test"), sessions.get(customer_id, session_id)
    )


def _wrong(otp: str) -> str:
    return f"{(int(otp) + 1) % 10**6:06d}"


def test_gate_strips_customer_id() -> None:
    ctx = _ctx()
    result = registry.execute(ctx, "get_balance", {"customer_id": "C1002"})
    assert result["accounts"][0]["account"] == "XXXX 5001"  # C1001's own account
    gate = next(s for s in ctx.trace.spans if s["kind"] == "gate")
    assert gate["stripped_args"] == ["customer_id"]


def test_three_wrong_otps_lock_the_action() -> None:
    pending = registry.execute(_ctx(), "block_card", {"card_type": "debit"})[
        "pending_action"
    ]
    for _ in range(3):
        assert not actions.confirm(
            "C1001", pending["action_id"], _wrong(pending["demo_otp"]), "t"
        )["ok"]
    after = actions.confirm("C1001", pending["action_id"], pending["demo_otp"], "t")
    assert not after["ok"] and "locked" in after["reason"]
    assert db.one("SELECT status FROM cards WHERE id = 'K1001D'")["status"] == "ACTIVE"


def test_compensation_past_t_plus_1() -> None:
    txn = {"channel": "UPI", "status": "FAILED_DEBITED", "ts": "2026-10-01T10:00:00"}
    deadline = rbi_deadline(txn, date(2026, 10, 5))
    assert deadline["reversal_due_by"] == "2026-10-02"
    assert deadline["days_past_tat"] == 3
    assert deadline["compensation_due"] == 300


def test_dubai_payment_scores_high() -> None:
    result = engine.ingest_transaction(
        "C1001",
        {
            "amount": 48_999,
            "merchant": "QuickGadgets Online",
            "channel": "CARD",
            "city": "Dubai",
            "ts": f"{date.today()}T02:14:00",
        },
    )
    assert result["band"] == "high"
    assert result["alert_id"]
    assert any("Dubai" in reason for reason in result["reasons"])


def test_fraud_analyst_cannot_lower_the_band(fake_llm: dict) -> None:
    fake_llm["fraud_analyst"] = AnalystVerdict(
        band="low", explanation="Looks fine to me."
    )
    result = engine.ingest_transaction(
        "C1001",
        {
            "amount": 48_999,
            "merchant": "QuickGadgets Online",
            "channel": "CARD",
            "city": "Dubai",
            "ts": f"{date.today()}T02:14:00",
        },
    )
    assert result["band"] == "high"
    assert result["analyst"] == "Looks fine to me."


def test_scam_flag_and_new_payee_hold_the_transfer() -> None:
    ctx = _ctx(session_id="scam")
    ctx.session["scam_risk"] = "high"
    result = registry.execute(
        ctx, "transfer_money", {"payee": "fix-kyc@okicici", "amount": 5_000}
    )
    assert result["held"] and result["decision"] == "hold"
    assert "pending_action" not in result


def test_late_scam_verdict_holds_the_transfer_at_confirm(fake_llm: dict) -> None:
    # The message has no scam keywords, so only the model's verdict (which lands after the
    # transfer was proposed) can catch it: the confirm-time re-check must.
    fake_llm["scam_guard"] = ScamVerdict(
        risk="high", patterns=["coached by a caller"], reason="test"
    )
    reply = engine.handle_chat("C1001", "send 5000 to new.person@okaxis", "late-flag")
    assert reply.answer_type == "action_pending"
    assert any(w.source == "scam_guard" for w in reply.warnings)
    before = db.one("SELECT balance FROM accounts WHERE customer_id = 'C1001'")[
        "balance"
    ]
    done = engine.confirm_action(
        "C1001", reply.pending_action.action_id, reply.pending_action.demo_otp
    )
    assert done.answer_type == "escalated"
    assert done.data["status"] == "held"
    assert (
        db.one("SELECT balance FROM accounts WHERE customer_id = 'C1001'")["balance"]
        == before
    )


def test_second_transfer_cannot_overdraw() -> None:
    # C1003 has Rs 23,910. Two Rs 20,000 transfers: the debit's own balance check stops the second.
    first = registry.execute(
        _ctx("C1003"), "transfer_money", {"payee": "a.one@okaxis", "amount": 20_000}
    )
    second = registry.execute(
        _ctx("C1003"), "transfer_money", {"payee": "b.two@okaxis", "amount": 20_000}
    )
    for pending in (first["pending_action"], second["pending_action"]):
        outcome = actions.confirm(
            "C1003", pending["action_id"], pending["demo_otp"], "t"
        )
    assert not outcome["ok"] and "Not enough balance" in outcome["reason"]
    assert (
        db.one("SELECT balance FROM accounts WHERE customer_id = 'C1003'")["balance"]
        == 3_910
    )


def test_verifier_reads_indian_grouping_and_rupee_sign() -> None:
    calls = [{"name": "get_balance", "result": {"balance": 148250, "due": 16238}}]
    assert (
        verifier.unknown_amounts("You have ₹1,48,250 and Rs. 16,238 is due.", calls, "")
        == []
    )
    assert verifier.unknown_amounts("You have Rs 1,48,520 left.", calls, "") == [
        "148520"
    ]
