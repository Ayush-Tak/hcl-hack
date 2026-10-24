"""Rules router for when the LLM is off, has no key, or fails: keywords -> tools -> templates.

It covers only the demo scenarios, on purpose: anything else gets a clarifying question.
"""

import re

from app.formatting import format_inr
from app.tools import registry
from app.tools.registry import Ctx

_AMOUNT = re.compile(r"(?:rs\.?|₹|inr)?\s*([\d,]{3,})", re.IGNORECASE)
_UPI_ID = re.compile(r"[\w.\-]{2,}@[a-zA-Z]{2,}")

INTENTS = [
    (
        "not_received",
        r"not (been )?(received|credited)|didn'?t (get|receive)|debited but|money left",
    ),
    ("lost_card", r"\b(lost|stolen|missing)\b.*\bcard\b|\bblock\b.*\bcard\b"),
    ("fraud", r"(don'?t|do not) recogni[sz]e|not me|suspicious|fraud|unauthori[sz]ed"),
    # Both come before "transfer": "still pay my bills" and "asked me to share" aren't payments.
    ("scam_report", r"(asked|asking|wants?|called|share).{0,40}\b(otp|cvv|pin)\b"),
    (
        "affordability",
        r"\bafford\b|can i buy|still pay (my )?bills|left after|how much will i have",
    ),
    ("transfer", r"\b(send|transfer|pay)\b"),
    (
        "policy",
        r"\b(fees?|charges?|policy|penalty|interest rate|compensation|ombudsman|liability|how long|timelines?)\b",
    ),
    ("balance", r"\bbalance\b"),
]


def detect_intent(message: str) -> str | None:
    for intent, pattern in INTENTS:
        if re.search(pattern, message, re.IGNORECASE):
            return intent
    return None


def _not_received(ctx: Ctx, message: str) -> str:
    status = registry.execute(ctx, "check_transaction_status", {})
    if not status.get("payments"):
        return "I couldn't find a failed payment in your account. Could you tell me the payee or amount?"
    words = set(re.findall(r"[a-z]{3,}", message.lower())) - {"upi", "payment", "the"}
    # Prefer the payment whose payee the customer named; otherwise the most recent one.
    named = [
        p
        for p in status["payments"]
        if words & set(re.findall(r"[a-z]{3,}", p["to"].lower()))
    ]
    payment = (named or status["payments"])[0]
    complaint = registry.execute(ctx, "raise_complaint", {"txn_id": payment["txn_id"]})
    text = (
        f"Your {format_inr(payment['amount'])} payment to {payment['to']} (UTR {payment['utr']}) "
        f"{payment['state']}. "
    )
    if complaint.get("escalated"):
        return text + (
            f"It is {payment['days_past_tat']} day(s) past the RBI deadline of {payment['reversal_due_by']}, "
            f"so complaint {complaint['complaint_id']} has gone to our team, with "
            f"{format_inr(payment['compensation_due'])} compensation due."
        )
    return text + (
        f"Under RBI rules it should be reversed by {payment['reversal_due_by']}. "
        f"I've raised complaint {complaint.get('complaint_id')} to track it."
    )


def _lost_card(ctx: Ctx, message: str) -> str:
    card_type = "credit" if "credit" in message.lower() else "debit"
    result = registry.execute(ctx, "block_card", {"card_type": card_type})
    if result.get("pending_action"):
        return f"{result['pending_action']['summary']}. Please confirm with the one-time code."
    return "I couldn't find an active card to block. Which card is it?"


def _fraud(ctx: Ctx, message: str) -> str:
    check = registry.execute(ctx, "fraud_verification", {})
    if check.get("not_found"):
        return (
            "I couldn't find that transaction. Could you give the amount or merchant?"
        )
    # The model's own "Model: ..." reasons repeat the rules in other words; they stay in the alert for reviewers.
    reasons = [r for r in check["reasons"] if not r.startswith("Model:")]
    text = (
        f"The {format_inr(check['amount'])} payment to {check['merchant']} has a {check['band']} fraud risk "
        f"(score {check['score']}): {'; '.join(reasons) or 'nothing unusual'}."
    )
    if check["band"] == "low":
        return text
    dispute = registry.execute(
        ctx, "raise_dispute", {"txn_id": check["txn_id"], "reason": message}
    )
    text += f" I've raised dispute {dispute.get('dispute_id')}."
    if check["band"] == "high":
        text += " Our review team has it, and I'd suggest blocking your card: just say 'block my card'."
    return text


def _scam_report(ctx: Ctx, message: str) -> str:
    cards = registry.execute(ctx, "get_credit_card_details", {})["cards"]
    active = [f"{c['type']} card {c['card']}" for c in cards if c["status"] == "ACTIVE"]
    text = (
        "Please don't share it. The bank never calls to ask for your OTP, PIN or CVV, so this is "
        "almost certainly a scam."
    )
    if not active:
        return text
    return text + (
        f" If you've already shared it, block your card now. Your active cards: {', '.join(active)}."
        " Just say 'block my debit card'."
    )


def _affordability(ctx: Ctx, message: str) -> str:
    balance = registry.execute(ctx, "get_balance", {})["accounts"][0]["balance"]
    dues = registry.execute(ctx, "get_upcoming_payments", {})
    amount = _AMOUNT.search(message)
    spend = int(amount.group(1).replace(",", "")) if amount else 0
    left = registry.execute(
        ctx,
        "calculate",
        {
            "expression": f"{balance} - {dues['total_due']} - {spend}",
            "label": "left after bills and the purchase",
        },
    )["value"]
    text = (
        f"Your balance is {format_inr(balance)}, and bills, card dues and EMIs due by {dues['until']} "
        f"come to {format_inr(dues['total_due'])}."
    )
    if not spend:
        return text + f" That leaves {format_inr(int(left))}."
    if left >= 0:
        return (
            text
            + f" After a {format_inr(spend)} purchase you'd still have {format_inr(int(left))} left."
        )
    return (
        text
        + f" A {format_inr(spend)} purchase would leave you {format_inr(int(-left))} short."
    )


def _transfer(ctx: Ctx, message: str) -> str:
    payee = _UPI_ID.search(message)
    # Look for the amount outside the UPI ID only: in "8949071335@paytm" the digits are a phone number.
    amount = _AMOUNT.search(message.replace(payee.group(), " ")) if payee else None
    if not payee or not amount:
        return "Who should I send it to (UPI ID) and how much?"
    result = registry.execute(
        ctx,
        "transfer_money",
        {"payee": payee.group(), "amount": int(amount.group(1).replace(",", ""))},
    )
    if result.get("held"):
        return (
            f"I've held this {format_inr(result['amount'])} payment for your safety: {'; '.join(result['signals'])}. "
            "A member of our team will call you. The bank never asks you to move money or share an OTP over the phone."
        )
    if result.get("pending_action"):
        warning = (
            f" Please check: {'; '.join(result['signals'])}."
            if result["signals"]
            else ""
        )
        return f"{result['pending_action']['summary']}.{warning} Confirm with the one-time code."
    return result.get("error", "I couldn't set up that payment.")


def _policy(ctx: Ctx, message: str) -> str:
    result = registry.execute(ctx, "search_policy", {"query": message})
    if result.get("not_found"):
        return "Our policy documents don't cover that, so I can't answer it reliably."
    top = result["results"][0]
    return f"{top['text']}\n\n(Source: {top['doc']}, {top['section']}, version {top['version']})"


def _balance(ctx: Ctx, message: str) -> str:
    result = registry.execute(ctx, "get_balance", {})
    account = result["accounts"][0]
    return f"Your {account['type']} account {account['account']} has {format_inr(account['balance'])}."


HANDLERS = {
    "not_received": _not_received,
    "lost_card": _lost_card,
    "fraud": _fraud,
    "scam_report": _scam_report,
    "affordability": _affordability,
    "transfer": _transfer,
    "policy": _policy,
    "balance": _balance,
}


def run(ctx: Ctx, message: str) -> str | None:
    """The reply, or None when no intent matched."""
    intent = detect_intent(message)
    ctx.trace.add("fallback", "intent", intent=intent)
    return HANDLERS[intent](ctx, message) if intent else None
