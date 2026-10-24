"""Card blocks, fraud checks on a transaction, and disputes."""

import secrets
from datetime import datetime

from app import db
from app.actions import create_pending, executor
from app.fraud.engine import assess_and_store
from app.tools.registry import Ctx, schema, tool


@tool(
    "block_card",
    "Block a lost or stolen card and order a replacement. Creates a pending action the customer "
    "confirms with an OTP.",
    schema(
        {
            "card_type": {"type": "string", "enum": ["debit", "credit"]},
            "last4": {
                "type": "string",
                "description": "Last 4 digits, if the customer gave them",
            },
        }
    ),
    tier="action",
)
def block_card(ctx: Ctx, args: dict) -> dict:
    cards = db.query(
        "SELECT * FROM cards WHERE customer_id = ? AND status = 'ACTIVE'",
        (ctx.customer_id,),
    )
    if args.get("card_type"):
        cards = [c for c in cards if c["type"] == args["card_type"]]
    if args.get("last4"):
        cards = [c for c in cards if c["number_masked"].endswith(args["last4"])]
    if not cards:
        return {"not_found": "No active card matches"}
    if len(cards) > 1:
        return {
            "clarify": "Which card?",
            "cards": [f"{c['type']} {c['number_masked']}" for c in cards],
        }
    card = cards[0]
    pending = create_pending(
        ctx.customer_id,
        "block_card",
        {"card_id": card["id"]},
        f"Block {card['type']} card {card['number_masked']} and send a replacement",
        ctx.trace.id,
    )
    return {"pending_action": pending, "card": card["number_masked"]}


@executor("block_card")
def execute_block_card(customer_id: str, args: dict, trace_id: str) -> dict:
    card = db.one(
        "SELECT * FROM cards WHERE id = ? AND customer_id = ?",
        (args["card_id"], customer_id),
    )
    if not card:
        return {"error": "Card not found"}
    if card["status"] == "ACTIVE":
        db.execute("UPDATE cards SET status = 'BLOCKED' WHERE id = ?", (card["id"],))
    return {
        "card": card["number_masked"],
        "status": "BLOCKED",
        "replacement_id": f"RPL-{secrets.randbelow(10**6):06d}",
        "delivery": "5 to 7 working days",
    }


def _riskiest_recent(customer_id: str) -> dict | None:
    rows = db.query(
        "SELECT * FROM transactions WHERE customer_id = ? AND direction = 'debit' "
        "ORDER BY ts DESC LIMIT 10",
        (customer_id,),
    )
    return max(rows, key=lambda t: t["amount"], default=None)


@tool(
    "fraud_verification",
    "Check a transaction the customer doesn't recognise: fraud risk score 0 to 1, band and reasons. "
    "Without txn_id it checks the largest of the last 10 debits.",
    schema({"txn_id": {"type": "string"}}),
)
def fraud_verification(ctx: Ctx, args: dict) -> dict:
    if args.get("txn_id"):
        txn = db.one(
            "SELECT * FROM transactions WHERE id = ? AND customer_id = ?",
            (args["txn_id"], ctx.customer_id),
        )
    else:
        txn = _riskiest_recent(ctx.customer_id)
    if not txn:
        return {"not_found": "No such transaction in your account"}
    verdict = assess_and_store(ctx.customer_id, txn, ctx.trace)
    advice = {
        "high": "offer to block the card and raise a dispute",
        "medium": "ask the customer to confirm; offer a dispute",
        "low": "looks normal",
    }[verdict["band"]]
    return {
        "txn_id": txn["id"],
        "amount": txn["amount"],
        "merchant": txn["merchant"],
        "time": txn["ts"],
        "city": txn["city"],
        "next_step": advice,
    } | verdict


@tool(
    "raise_dispute",
    "Raise a dispute for a transaction the customer says they didn't make. High-risk disputes go to "
    "the bank's review team.",
    schema({"txn_id": {"type": "string"}, "reason": {"type": "string"}}, ["txn_id"]),
    tier="write",
)
def raise_dispute(ctx: Ctx, args: dict) -> dict:
    txn = db.one(
        "SELECT * FROM transactions WHERE id = ? AND customer_id = ?",
        (args["txn_id"], ctx.customer_id),
    )
    if not txn:
        return {"not_found": "No such transaction in your account"}
    existing = db.one(
        "SELECT id, status FROM complaints WHERE txn_id = ? AND type = 'dispute'",
        (txn["id"],),
    )
    if existing:
        return {
            "dispute_id": existing["id"],
            "status": existing["status"],
            "already_raised": True,
        }
    verdict = assess_and_store(ctx.customer_id, txn, ctx.trace)
    escalated = verdict["band"] == "high"
    dispute_id = f"DSP-{db.next_counter('dispute'):06d}"
    db.execute(
        "INSERT INTO complaints VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            dispute_id,
            ctx.customer_id,
            txn["id"],
            "dispute",
            "escalated" if escalated else "open",
            datetime.now().isoformat(timespec="seconds"),
            None,
            None,
            args.get("reason", ""),
            ctx.trace.id,
        ),
    )
    return {
        "dispute_id": dispute_id,
        "status": "escalated" if escalated else "open",
        "escalated": escalated,
        "alert_id": verdict["alert_id"],
        "band": verdict["band"],
    }
