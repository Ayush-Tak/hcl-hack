from datetime import date

from app import actions, db, engine, sessions
from app.agents import verifier
from app.agents.fraud_analyst import AnalystVerdict
from app.agents.scam_guard import ScamVerdict
from app.formatting import format_inr
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


def test_policy_search_cites_both_docs_and_refuses_unknown_topics() -> None:
    ctx = _ctx()
    delay = registry.execute(ctx, "search_policy", {"query": "Is there compensation if my UPI refund is delayed?"})
    assert (delay["results"][0]["doc"], delay["results"][0]["section"]) == ("failed-transactions", "Compensation for delay")
    assert delay["results"][0]["version"] == "1.0"
    card = registry.execute(ctx, "search_policy", {"query": "How do I report a card transaction I do not recognise?"})
    assert card["results"][0]["doc"] == "card-disputes"
    assert "not_found" in registry.execute(ctx, "search_policy", {"query": "What is the fee for a duplicate debit card?"})


def test_affordability_chip_is_calculated_from_tools() -> None:
    reply = engine.handle_chat("C1001", "Can I buy a ₹20,000 phone and still pay my bills?", "afford")
    dues = registry.execute(_ctx(), "get_upcoming_payments", {})["total_due"]
    assert reply.answer_type == "calculated"
    assert "calculate" in [t.name for t in reply.tools_invoked]
    assert format_inr(148_250 - dues - 20_000) in reply.answer


def test_bank_asked_for_otp_chip_warns_about_a_scam() -> None:
    reply = engine.handle_chat("C1001", "The bank called and asked for my OTP", "otp-chip")
    assert "never calls to ask for your OTP" in reply.answer
    assert any(w.source == "scam_guard" and w.level == "high" for w in reply.warnings)


def test_transfer_amount_is_not_read_from_the_upi_id() -> None:
    no_amount = engine.handle_chat("C1001", "send money to 8949071335@paytm", "upi-a")
    assert no_amount.pending_action is None
    with_amount = engine.handle_chat("C1001", "pay 8949071335@paytm Rs 500", "upi-b")
    assert with_amount.pending_action.summary.startswith("Send Rs 500 to 8949071335@paytm")


def test_scheduled_transfer_checks_the_balance() -> None:
    too_much = registry.execute(_ctx(), "transfer_money", {"payee": "new.person@okaxis", "amount": 2_00_000})
    assert "Not enough balance" in too_much["error"]
    # A cooling-off transfer that fit when proposed, but no longer fits when confirmed.
    pending = registry.execute(_ctx(), "transfer_money", {"payee": "new.person@okaxis", "amount": 1_00_000})
    assert pending["decision"] == "cooling_off"
    db.execute("UPDATE accounts SET balance = 50000 WHERE customer_id = 'C1001'")
    outcome = actions.confirm("C1001", pending["pending_action"]["action_id"], pending["pending_action"]["demo_otp"], "t")
    assert not outcome["ok"] and "Not enough balance" in outcome["reason"]
