"""Read-only banking tools. Names match the problem statement where it names them."""

from datetime import date, datetime, timedelta

from app import db
from app.tools.registry import Ctx, schema, tool

PERIODS = ["this_month", "last_month", "last_30_days", "last_90_days"]


def _period(name: str) -> tuple[str, str, str, str]:
    """Return (start, end, previous_start, previous_end) as ISO dates, end exclusive."""
    today = date.today()
    first = today.replace(day=1)
    if name == "this_month":
        prev_last = first - timedelta(days=1)
        prev_first = prev_last.replace(day=1)
        # Like with like (1st to today's date), capped so the 31st can't spill into this month.
        same_day_prev = min(prev_first + (today - first), prev_last)
        return (
            first.isoformat(),
            (today + timedelta(days=1)).isoformat(),
            prev_first.isoformat(),
            (same_day_prev + timedelta(days=1)).isoformat(),
        )
    if name == "last_month":
        last_first = (first - timedelta(days=1)).replace(day=1)
        before = (last_first - timedelta(days=1)).replace(day=1)
        return (
            last_first.isoformat(),
            first.isoformat(),
            before.isoformat(),
            last_first.isoformat(),
        )
    days = 30 if name == "last_30_days" else 90
    start = today - timedelta(days=days - 1)
    return (
        start.isoformat(),
        (today + timedelta(days=1)).isoformat(),
        (start - timedelta(days=days)).isoformat(),
        start.isoformat(),
    )


def _txn_view(t: dict) -> dict:
    return {
        "txn_id": t["id"],
        "time": t["ts"],
        "amount": t["amount"],
        "direction": t["direction"],
        "merchant": t["merchant"],
        "category": t["category"],
        "channel": t["channel"],
        "city": t["city"],
        "status": t["status"],
        "utr": t["utr"],
    }


@tool(
    "get_balance",
    "Current balance of the customer's accounts and the five most recent transactions.",
)
def get_balance(ctx: Ctx, args: dict) -> dict:
    accounts = db.query(
        "SELECT id, type, number_masked, balance FROM accounts WHERE customer_id = ?",
        (ctx.customer_id,),
    )
    recent = db.query(
        "SELECT * FROM transactions WHERE customer_id = ? ORDER BY ts DESC LIMIT 5",
        (ctx.customer_id,),
    )
    return {
        "as_of": datetime.now().isoformat(timespec="minutes"),
        "accounts": [
            {"account": a["number_masked"], "type": a["type"], "balance": a["balance"]}
            for a in accounts
        ],
        "recent_transactions": [_txn_view(t) for t in recent],
    }


@tool(
    "get_transactions",
    "Search the customer's transactions. Filter by days back, merchant text, category, direction or status.",
    schema(
        {
            "days": {
                "type": "integer",
                "description": "How many days back, default 30",
            },
            "merchant": {
                "type": "string",
                "description": "Part of the merchant or payee name",
            },
            "category": {"type": "string"},
            "direction": {"type": "string", "enum": ["debit", "credit"]},
            "min_amount": {"type": "integer"},
            "status": {
                "type": "string",
                "enum": ["SUCCESS", "PENDING", "FAILED_DEBITED", "REVERSED"],
            },
            "limit": {"type": "integer", "description": "Max rows, default 15"},
        }
    ),
)
def get_transactions(ctx: Ctx, args: dict) -> dict:
    since = (datetime.now() - timedelta(days=int(args.get("days") or 30))).isoformat()
    sql, params = (
        "SELECT * FROM transactions WHERE customer_id = ? AND ts >= ?",
        [ctx.customer_id, since],
    )
    if args.get("merchant"):
        sql += " AND (merchant LIKE ? OR counterparty LIKE ?)"
        params += [f"%{args['merchant']}%"] * 2
    for key in ("category", "direction", "status"):
        if args.get(key):
            sql += f" AND {key} = ?"
            params.append(args[key])
    if args.get("min_amount"):
        sql += " AND amount >= ?"
        params.append(args["min_amount"])
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(min(int(args.get("limit") or 15), 50))
    rows = db.query(sql, tuple(params))
    if not rows:
        return {"found": 0, "transactions": [], "note": "No matching transactions"}
    return {"found": len(rows), "transactions": [_txn_view(t) for t in rows]}


@tool(
    "analyze_expenses",
    "Spending by category for a period, with the total, the previous period's total and top merchants.",
    schema(
        {
            "period": {
                "type": "string",
                "enum": PERIODS,
                "description": "Default this_month",
            }
        }
    ),
)
def analyze_expenses(ctx: Ctx, args: dict) -> dict:
    period = args.get("period") or "this_month"
    start, end, pstart, pend = _period(period)
    base = (
        "FROM transactions WHERE customer_id = ? AND direction = 'debit' AND status = 'SUCCESS' "
        "AND category NOT IN ('transfer', 'cash') AND ts >= ? AND ts < ?"
    )
    cats = db.query(
        f"SELECT category, SUM(amount) AS amount, COUNT(*) AS count {base} "
        "GROUP BY category ORDER BY amount DESC",
        (ctx.customer_id, start, end),
    )
    total = sum(c["amount"] for c in cats)
    prev = db.one(
        f"SELECT COALESCE(SUM(amount), 0) AS t {base}", (ctx.customer_id, pstart, pend)
    )["t"]
    top = db.query(
        f"SELECT merchant, SUM(amount) AS amount {base} GROUP BY merchant ORDER BY amount DESC LIMIT 3",
        (ctx.customer_id, start, end),
    )
    change = round((total - prev) * 100 / prev, 1) if prev else None
    return {
        "period": period,
        "from": start,
        "to_exclusive": end,
        "total": total,
        "previous_period_total": prev,
        "change_percent": change,
        "by_category": cats,
        "top_merchants": top,
    }


@tool(
    "get_credit_card_details",
    "The customer's cards: status, limits, outstanding, statement amount, minimum due and due date.",
)
def get_credit_card_details(ctx: Ctx, args: dict) -> dict:
    cards = db.query("SELECT * FROM cards WHERE customer_id = ?", (ctx.customer_id,))
    out = []
    for c in cards:
        item = {"card": c["number_masked"], "type": c["type"], "status": c["status"]}
        if c["type"] == "credit":
            item |= {
                "credit_limit": c["credit_limit"],
                "outstanding": c["outstanding"],
                "available_limit": c["credit_limit"] - c["outstanding"],
                "statement_amount": c["statement_amount"],
                "minimum_due": c["min_due"],
                "due_date": c["due_date"],
            }
        else:
            item["daily_limit"] = c["daily_limit"]
        out.append(item)
    return {"cards": out}


@tool(
    "get_upcoming_payments",
    "Bills, card dues and loan EMIs due in the next N days, with the total.",
    schema(
        {"days": {"type": "integer", "description": "Look-ahead window, default 14"}}
    ),
)
def get_upcoming_payments(ctx: Ctx, args: dict) -> dict:
    until = (date.today() + timedelta(days=int(args.get("days") or 14))).isoformat()
    items = [
        {
            "name": b["name"],
            "amount": b["amount"],
            "due_date": b["due_date"],
            "autopay": bool(b["autopay"]),
        }
        for b in db.query(
            "SELECT * FROM bills WHERE customer_id = ? AND status = 'DUE' AND due_date <= ?",
            (ctx.customer_id, until),
        )
    ]
    for c in db.query(
        "SELECT * FROM cards WHERE customer_id = ? AND type = 'credit' AND due_date <= ?",
        (ctx.customer_id, until),
    ):
        items.append(
            {
                "name": f"Credit card {c['number_masked']} statement",
                "amount": c["statement_amount"],
                "minimum_due": c["min_due"],
                "due_date": c["due_date"],
                "autopay": False,
            }
        )
    for loan in db.query(
        "SELECT * FROM loans WHERE customer_id = ? AND next_emi_date <= ?",
        (ctx.customer_id, until),
    ):
        items.append(
            {
                "name": f"{loan['type'].replace('_', ' ').title()} loan EMI",
                "amount": loan["emi"],
                "due_date": loan["next_emi_date"],
                "autopay": True,
            }
        )
    items.sort(key=lambda i: i["due_date"])
    return {
        "until": until,
        "payments": items,
        "total_due": sum(i["amount"] for i in items),
    }


@tool(
    "get_loan_details",
    "The customer's loans: outstanding amount, EMI, next EMI date, rate and months left.",
)
def get_loan_details(ctx: Ctx, args: dict) -> dict:
    loans = db.query(
        "SELECT type, principal, outstanding, emi, next_emi_date, rate, tenure_left "
        "FROM loans WHERE customer_id = ?",
        (ctx.customer_id,),
    )
    return {"loans": loans} if loans else {"loans": [], "note": "No active loans"}


@tool(
    "check_loan_eligibility",
    "Pre-approved personal loan eligibility from income and existing EMIs (EMI capacity is 50% of income "
    "minus current EMIs). Optionally test a requested amount.",
    schema(
        {
            "amount": {"type": "integer", "description": "Requested amount in rupees"},
            "tenure_months": {"type": "integer", "description": "Default 36"},
        }
    ),
)
def check_loan_eligibility(ctx: Ctx, args: dict) -> dict:
    cust = db.one(
        "SELECT monthly_income, segment FROM customers WHERE id = ?", (ctx.customer_id,)
    )
    emis = db.one(
        "SELECT COALESCE(SUM(emi), 0) AS e FROM loans WHERE customer_id = ?",
        (ctx.customer_id,),
    )["e"]
    open_alerts = db.one(
        "SELECT COUNT(*) AS n FROM alerts WHERE customer_id = ? AND band = 'high' AND status = 'open'",
        (ctx.customer_id,),
    )["n"]
    rate, n = 10.99, int(args.get("tenure_months") or 36)
    r = rate / 1200
    capacity = max(0, int(cust["monthly_income"] * 0.5 - emis))
    max_amount = min(500_000, int(capacity * (1 - (1 + r) ** -n) / r) // 1000 * 1000)
    reasons = []
    if cust["segment"] != "salaried":
        reasons.append("pre-approved offer is for salaried customers")
    if open_alerts:
        reasons.append("an open fraud alert must be resolved first")
    eligible = not reasons and max_amount > 0
    out = {
        "eligible": eligible,
        "reasons_not_eligible": reasons,
        "monthly_income": cust["monthly_income"],
        "existing_emis": emis,
        "emi_capacity": capacity,
        "rate_percent": rate,
        "tenure_months": n,
        "max_eligible_amount": max_amount if eligible else 0,
        "note": "Indicative only; final approval needs the bank's checks and your consent.",
    }
    if args.get("amount"):
        amt = int(args["amount"])
        out["requested_amount"] = amt
        out["requested_emi"] = round(amt * r / (1 - (1 + r) ** -n))
        out["requested_ok"] = eligible and amt <= max_amount
    return out
