"""Payment status with RBI deadlines, complaints, and transfers behind the pre-payment check."""

import re
from datetime import date, datetime, timedelta
from statistics import median

from app import db, sessions
from app.actions import create_pending, executor
from app.formatting import format_inr
from app.tools.registry import Ctx, schema, tool

# RBI turnaround times for failed-but-debited payments (T = transaction date, calendar days).
TAT_DAYS = {"UPI": 1, "IMPS": 1, "ATM": 5}
COMPENSATION_PER_DAY = 100
STATES = {
    "SUCCESS": "completed",
    "PENDING": "pending",
    "FAILED_DEBITED": "failed, but your account was debited",
    "REVERSED": "failed and refunded",
}
COOLING_OFF = timedelta(minutes=30)
COOLING_OFF_AMOUNT = 25_000


def rbi_deadline(txn: dict, today: date) -> dict:
    days = TAT_DAYS.get(txn["channel"])
    if days is None or txn["status"] != "FAILED_DEBITED":
        return {}
    due_by = date.fromisoformat(txn["ts"][:10]) + timedelta(days=days)
    days_late = max(0, (today - due_by).days)
    return {
        "rbi_tat": f"T+{days}",
        "reversal_due_by": due_by.isoformat(),
        "days_past_tat": days_late,
        "compensation_due": days_late * COMPENSATION_PER_DAY,
    }


def _status_view(txn: dict) -> dict:
    return {
        "txn_id": txn["id"],
        "time": txn["ts"],
        "amount": txn["amount"],
        "to": txn["merchant"],
        "channel": txn["channel"],
        "state": STATES.get(txn["status"], txn["status"]),
        "utr": txn["utr"],
    } | rbi_deadline(txn, date.today())


@tool(
    "check_transaction_status",
    "Find a payment and return its state, UTR and, for failed-but-debited payments, the RBI "
    "reversal deadline and any compensation due. With no filters, lists recent problem payments.",
    schema(
        {
            "txn_id": {"type": "string"},
            "merchant": {
                "type": "string",
                "description": "Part of the payee or merchant name",
            },
            "amount": {"type": "integer"},
        }
    ),
)
def check_transaction_status(ctx: Ctx, args: dict) -> dict:
    sql, params = "SELECT * FROM transactions WHERE customer_id = ?", [ctx.customer_id]
    if args.get("txn_id"):
        sql += " AND id = ?"
        params.append(args["txn_id"])
    if args.get("merchant"):
        sql += " AND (merchant LIKE ? OR counterparty LIKE ?)"
        params += [f"%{args['merchant']}%"] * 2
    if args.get("amount"):
        sql += " AND amount = ?"
        params.append(args["amount"])
    if not any(args.get(k) for k in ("txn_id", "merchant", "amount")):
        sql += " AND status IN ('FAILED_DEBITED', 'PENDING')"
    rows = db.query(sql + " ORDER BY ts DESC LIMIT 5", tuple(params))
    if not rows:
        return {"found": 0, "not_found": "No matching payment in your account"}
    return {"found": len(rows), "payments": [_status_view(t) for t in rows]}


def _existing_complaint(ctx: Ctx, txn_id: str) -> dict | None:
    return db.one(
        "SELECT * FROM complaints WHERE customer_id = ? AND txn_id = ? AND type = 'not_received'",
        (ctx.customer_id, txn_id),
    )


@tool(
    "raise_complaint",
    "Raise a 'debited but not received' complaint for a failed-but-debited payment. Inside the RBI "
    "deadline it opens a ticket; past it, it escalates to a person with the compensation due.",
    schema({"txn_id": {"type": "string"}}, ["txn_id"]),
    tier="write",
)
def raise_complaint(ctx: Ctx, args: dict) -> dict:
    txn = db.one(
        "SELECT * FROM transactions WHERE id = ? AND customer_id = ?",
        (args["txn_id"], ctx.customer_id),
    )
    if not txn:
        return {"not_found": "No such payment in your account"}
    if txn["status"] != "FAILED_DEBITED":
        return {
            "error": f"This payment is {STATES.get(txn['status'], txn['status'])}; no complaint needed"
        }
    existing = _existing_complaint(ctx, txn["id"])
    if existing:
        return {
            "complaint_id": existing["id"],
            "status": existing["status"],
            "already_raised": True,
            "escalated": existing["status"] == "escalated",
        }
    deadline = rbi_deadline(txn, date.today())
    escalated = deadline["days_past_tat"] > 0
    complaint_id = f"CMP-{db.next_counter('complaint'):06d}"
    db.execute(
        "INSERT INTO complaints VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            complaint_id,
            ctx.customer_id,
            txn["id"],
            "not_received",
            "escalated" if escalated else "open",
            datetime.now().isoformat(timespec="seconds"),
            deadline["reversal_due_by"],
            COMPENSATION_PER_DAY,
            f"UTR {txn['utr']}",
            ctx.trace.id,
        ),
    )
    if escalated:
        _open_case(
            ctx.customer_id,
            ctx.trace.id,
            "not_received",
            f"{complaint_id}: {format_inr(txn['amount'])} past RBI TAT, "
            f"compensation {format_inr(deadline['compensation_due'])}",
        )
    return {
        "complaint_id": complaint_id,
        "status": "escalated" if escalated else "open",
        "escalated": escalated,
        "utr": txn["utr"],
    } | deadline


def _open_case(customer_id: str, trace_id: str, topic: str, summary: str) -> str:
    case_id = f"CASE-{db.next_counter('case'):06d}"
    db.execute(
        "INSERT INTO cases VALUES (?,?,?,?,?,?,?)",
        (
            case_id,
            customer_id,
            topic,
            summary,
            "open",
            datetime.now().isoformat(timespec="seconds"),
            trace_id,
        ),
    )
    return case_id


def pre_payment_check(
    customer_id: str, payee: str, amount: int, scam_flag: bool
) -> dict:
    known = db.one(
        "SELECT id FROM payees WHERE customer_id = ? AND (upi_id = ? OR name LIKE ?)",
        (customer_id, payee, payee),
    )
    since = (datetime.now() - timedelta(days=90)).isoformat()
    usual = [
        r["amount"]
        for r in db.query(
            "SELECT amount FROM transactions WHERE customer_id = ? AND direction = 'debit' "
            "AND channel = 'UPI' AND ts >= ?",
            (customer_id, since),
        )
    ]
    signals = []
    if not known:
        signals.append("first payment to this payee")
    if usual and amount > 3 * median(usual):
        signals.append(f"{format_inr(amount)} is over 3x your usual UPI payment")
    if scam_flag:
        signals.append("this conversation shows signs of a scam")
    if scam_flag and len(signals) > 1:
        decision = "hold"
    elif not known and amount >= COOLING_OFF_AMOUNT:
        decision = "cooling_off"
    else:
        decision = "step_up" if signals else "allow"
    return {"decision": decision, "signals": signals}


_UPI_ID = re.compile(r"^[\w.\-]{2,}@[a-zA-Z]{2,}$")


@tool(
    "transfer_money",
    "Send money by UPI to a payee. Runs the bank's pre-payment safety check first, then creates a "
    "pending transfer the customer confirms with an OTP. It may hold the payment instead.",
    schema(
        {
            "payee": {
                "type": "string",
                "description": "UPI ID like name@okaxis, or a saved payee's name",
            },
            "amount": {"type": "integer", "description": "Rupees"},
        },
        ["payee", "amount"],
    ),
    tier="action",
)
def transfer_money(ctx: Ctx, args: dict) -> dict:
    payee, amount = args["payee"].strip(), int(args["amount"])
    if amount <= 0:
        return {"error": "Amount must be more than zero"}
    saved = db.one(
        "SELECT upi_id FROM payees WHERE customer_id = ? AND name LIKE ?",
        (ctx.customer_id, payee),
    )
    upi_id = saved["upi_id"] if saved else payee
    if not _UPI_ID.match(upi_id):
        return {"error": "Please give a UPI ID like name@bank, or a saved payee's name"}
    check = pre_payment_check(
        ctx.customer_id, upi_id, amount, ctx.session.get("scam_risk") == "high"
    )
    ctx.trace.add("gate", "pre_payment_check", **check)
    if check["decision"] == "hold":
        case_id = _open_case(
            ctx.customer_id,
            ctx.trace.id,
            "scam_hold",
            f"Held {format_inr(amount)} to {upi_id}: " + "; ".join(check["signals"]),
        )
        return {
            "held": True,
            "escalated": True,
            "case_id": case_id,
            "amount": amount,
            "payee": upi_id,
        } | check
    summary = f"Send {format_inr(amount)} to {upi_id}"
    if check["decision"] == "cooling_off":
        summary += " (released 30 minutes after you confirm)"
    pending = create_pending(
        ctx.customer_id,
        "transfer",
        {
            "upi_id": upi_id,
            "amount": amount,
            "cooling_off": check["decision"] == "cooling_off",
            "session_id": ctx.session.get("id", ""),
        },
        summary,
        ctx.trace.id,
        risk=check["decision"],
    )
    return {"pending_action": pending, "amount": amount, "payee": upi_id} | check


def _hold_at_confirm(customer_id: str, args: dict, trace_id: str) -> dict | None:
    """Re-run the pre-payment check with the scam flag as it is now. Scam Guard may have
    flagged the conversation after the transfer was proposed (it runs in parallel)."""
    session = sessions.get(customer_id, args["session_id"])
    check = pre_payment_check(
        customer_id, args["upi_id"], args["amount"], session["scam_risk"] == "high"
    )
    if check["decision"] != "hold":
        return None
    case_id = _open_case(
        customer_id,
        trace_id,
        "scam_hold",
        f"Held at confirm {format_inr(args['amount'])} to {args['upi_id']}: "
        + "; ".join(check["signals"]),
    )
    return {
        "status": "held",
        "case_id": case_id,
        "amount": args["amount"],
        "payee": args["upi_id"],
        "signals": check["signals"],
    }


def _debit(customer_id: str, account_id: str, args: dict) -> str | None:
    """Check and debit in one statement, so two confirms at once can't overdraw the account."""
    txn_id = f"T9{db.next_counter('live_txn'):04d}"
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE accounts SET balance = balance - ? WHERE id = ? AND balance >= ?",
            (args["amount"], account_id, args["amount"]),
        )
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            return None
        conn.execute(
            "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                txn_id,
                account_id,
                customer_id,
                datetime.now().isoformat(timespec="seconds"),
                args["amount"],
                "debit",
                f"UPI to {args['upi_id']}",
                "transfer",
                "UPI",
                None,
                "SUCCESS",
                None,
                args["upi_id"],
                None,
                None,
            ),
        )
        conn.execute("COMMIT")
    return txn_id


@executor("transfer")
def execute_transfer(customer_id: str, args: dict, trace_id: str) -> dict:
    held = _hold_at_confirm(customer_id, args, trace_id)
    if held:
        return held
    if args["cooling_off"]:
        # No money moves yet; the debit (with the same balance check) happens at release.
        release_at = (datetime.now() + COOLING_OFF).isoformat(timespec="seconds")
        return {
            "status": "scheduled",
            "release_at": release_at,
            "amount": args["amount"],
            "payee": args["upi_id"],
        }
    account = db.one("SELECT id FROM accounts WHERE customer_id = ?", (customer_id,))
    txn_id = _debit(customer_id, account["id"], args)
    balance = db.one("SELECT balance FROM accounts WHERE id = ?", (account["id"],))[
        "balance"
    ]
    if txn_id is None:
        return {"error": f"Not enough balance: {format_inr(balance)} available"}
    return {
        "status": "sent",
        "txn_id": txn_id,
        "amount": args["amount"],
        "payee": args["upi_id"],
        "balance_after": balance,
    }
