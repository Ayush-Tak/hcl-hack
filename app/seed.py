"""Deterministic demo data, dated relative to today so dues and deadlines always look current.

Planted scenarios (the demo script relies on these):
- C1001 card payment of Rs 48,999 at 02:14 in Dubai to a new merchant -> high fraud score
- C1001 UPI of Rs 2,500 to Ravi Kumar yesterday: debited, not credited (inside RBI T+1)
- C1001 UPI of Rs 1,200 to Sharma Kirana 5 days ago: debited, not credited (past T+1, compensation due)
- C1002 ATM withdrawal of Rs 5,000 two days ago: cash not dispensed (ATM rule is T+5)
"""

import json
import logging
import random
import sqlite3
from datetime import datetime, timedelta

from app import db
from app.auth import hash_pin

logger = logging.getLogger(__name__)

MERCHANTS = {
    "groceries": ["BigBasket", "Reliance Fresh", "DMart", "Sharma Kirana"],
    "food": ["Swiggy", "Zomato", "Domino's"],
    "fuel": ["Indian Oil", "HP Petrol Pump"],
    "shopping": ["Amazon", "Flipkart", "Myntra"],
    "travel": ["IRCTC", "Uber", "Ola"],
    "entertainment": ["BookMyShow", "Netflix", "Spotify"],
}

# category, chance on any given day, amount range, channel, hour range
HABITS = [
    ("groceries", 0.30, (400, 3200), "UPI", (8, 21)),
    ("food", 0.40, (180, 900), "UPI", (12, 22)),
    ("fuel", 0.12, (1200, 3000), "CARD", (7, 20)),
    ("shopping", 0.10, (800, 6500), "CARD", (10, 23)),
    ("travel", 0.08, (150, 2500), "UPI", (9, 21)),
    ("entertainment", 0.05, (199, 1200), "CARD", (18, 23)),
]

CUSTOMERS = [
    # id, name, home city, monthly income, segment, opening balance
    ("C1001", "Aarav Sharma", "Jaipur", 120_000, "salaried", 148_250),
    ("C1002", "Priya Iyer", "Bengaluru", 85_000, "salaried", 62_430),
    ("C1003", "Rohan Mehta", "Mumbai", 60_000, "self_employed", 23_910),
]
DEMO_PIN = "1234"
OFFICER = ("O9001", "Neha Verma", "9999")

# Re-seeding resets demo state but never traces or the audit chain: those are append-only.
RESET_TABLES = [
    "customers",
    "accounts",
    "transactions",
    "cards",
    "loans",
    "bills",
    "payees",
    "products",
    "complaints",
    "alerts",
    "cases",
    "pending_actions",
]


def _utr(rng: random.Random) -> str:
    return str(rng.randint(10**11, 10**12 - 1))


def _days_from(today: datetime, days: int) -> str:
    return (today + timedelta(days=days)).date().isoformat()


def _txn(
    rng: random.Random,
    cid: str,
    when: datetime,
    amount: int,
    direction: str,
    merchant: str,
    category: str,
    channel: str,
    city: str,
    status: str = "SUCCESS",
    counterparty: str | None = None,
) -> tuple:
    return (
        f"A{cid[1:]}",
        cid,
        when.isoformat(timespec="seconds"),
        amount,
        direction,
        merchant,
        category,
        channel,
        city,
        status,
        _utr(rng),
        counterparty,
        None,
        None,
    )


def _daily_life(
    rng: random.Random, cid: str, city: str, income: int, today: datetime
) -> list[tuple]:
    rows = []
    for back in range(90, 0, -1):
        day = today - timedelta(days=back)
        if day.day == 1:
            rows.append(
                _txn(
                    rng,
                    cid,
                    day.replace(hour=9, minute=5),
                    income,
                    "credit",
                    "Salary - Employer",
                    "salary",
                    "NEFT",
                    city,
                )
            )
        if day.day == 5:
            rows.append(
                _txn(
                    rng,
                    cid,
                    day.replace(hour=10),
                    int(income * 0.22),
                    "debit",
                    "Rent - Landlord",
                    "rent",
                    "UPI",
                    city,
                )
            )
        if day.day == 12:
            rows.append(
                _txn(
                    rng,
                    cid,
                    day.replace(hour=11, minute=30),
                    rng.randint(1500, 3200),
                    "debit",
                    "JVVNL Electricity",
                    "utilities",
                    "UPI",
                    city,
                )
            )
        for category, chance, (low, high), channel, (first_hour, last_hour) in HABITS:
            if rng.random() < chance:
                when = day.replace(
                    hour=rng.randint(first_hour, last_hour), minute=rng.randint(0, 59)
                )
                rows.append(
                    _txn(
                        rng,
                        cid,
                        when,
                        rng.randint(low, high),
                        "debit",
                        rng.choice(MERCHANTS[category]),
                        category,
                        channel,
                        city,
                    )
                )
    return rows


def _planted(rng: random.Random, today: datetime) -> list[tuple]:
    yesterday = today - timedelta(days=1)
    return [
        _txn(
            rng,
            "C1001",
            yesterday.replace(hour=2, minute=14),
            48_999,
            "debit",
            "QuickGadgets Online",
            "shopping",
            "CARD",
            "Dubai",
        ),
        _txn(
            rng,
            "C1001",
            yesterday.replace(hour=18, minute=40),
            2_500,
            "debit",
            "UPI to Ravi Kumar",
            "transfer",
            "UPI",
            "Jaipur",
            status="FAILED_DEBITED",
            counterparty="ravi.k@okaxis",
        ),
        _txn(
            rng,
            "C1001",
            (today - timedelta(days=5)).replace(hour=13, minute=5),
            1_200,
            "debit",
            "Sharma Kirana",
            "groceries",
            "UPI",
            "Jaipur",
            status="FAILED_DEBITED",
            counterparty="sharmakirana@ybl",
        ),
        _txn(
            rng,
            "C1002",
            (today - timedelta(days=2)).replace(hour=20, minute=10),
            5_000,
            "debit",
            "ATM Koramangala",
            "cash",
            "ATM",
            "Bengaluru",
            status="FAILED_DEBITED",
        ),
    ]


def _insert_people(conn: sqlite3.Connection) -> None:
    officer_id, officer_name, officer_pin = OFFICER
    conn.execute(
        "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)",
        (
            officer_id,
            officer_name,
            hash_pin(officer_id, officer_pin),
            "officer",
            "Jaipur",
            0,
            "staff",
            "",
        ),
    )
    for cid, name, city, income, segment, balance in CUSTOMERS:
        conn.execute(
            "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)",
            (
                cid,
                name,
                hash_pin(cid, DEMO_PIN),
                "customer",
                city,
                income,
                segment,
                "+91 98xxxxx" + cid[-3:],
            ),
        )
        conn.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?)",
            (
                f"A{cid[1:]}",
                cid,
                "savings",
                "XXXX " + str(4000 + int(cid[1:]))[-4:],
                balance,
            ),
        )


def _insert_transactions(
    conn: sqlite3.Connection, rng: random.Random, today: datetime
) -> int:
    rows = []
    for cid, _name, city, income, _segment, _balance in CUSTOMERS:
        rows += _daily_life(rng, cid, city, income, today)
    rows += _planted(rng, today)
    conn.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(f"T{n:05d}", *row) for n, row in enumerate(rows, start=1)],
    )
    return len(rows)


def _insert_cards_and_loans(conn: sqlite3.Connection, today: datetime) -> None:
    def due(days: int) -> str:
        return _days_from(today, days)

    conn.executemany(
        "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "K1001D",
                "C1001",
                "debit",
                "XXXX 4821",
                "ACTIVE",
                None,
                None,
                None,
                None,
                None,
                50_000,
            ),
            (
                "K1001C",
                "C1001",
                "credit",
                "XXXX 9934",
                "ACTIVE",
                200_000,
                32_450,
                12_300,
                615,
                due(4),
                None,
            ),
            (
                "K1002D",
                "C1002",
                "debit",
                "XXXX 1177",
                "ACTIVE",
                None,
                None,
                None,
                None,
                None,
                40_000,
            ),
            (
                "K1002C",
                "C1002",
                "credit",
                "XXXX 5560",
                "ACTIVE",
                150_000,
                18_900,
                9_870,
                494,
                due(7),
                None,
            ),
            (
                "K1003D",
                "C1003",
                "debit",
                "XXXX 3088",
                "ACTIVE",
                None,
                None,
                None,
                None,
                None,
                25_000,
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO loans VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("L1001", "C1001", "personal", 300_000, 210_000, 8_750, due(9), 11.5, 26),
            ("L1003", "C1003", "two_wheeler", 90_000, 41_000, 3_150, due(12), 13.0, 14),
        ],
    )


def _insert_bills_and_payees(conn: sqlite3.Connection, today: datetime) -> None:
    def due(days: int) -> str:
        return _days_from(today, days)

    conn.executemany(
        "INSERT INTO bills VALUES (?,?,?,?,?,?,?)",
        [
            ("B1", "C1001", "JVVNL Electricity", 2_340, due(2), 0, "DUE"),
            ("B2", "C1001", "Airtel Postpaid", 599, due(3), 0, "DUE"),
            ("B3", "C1001", "Jio Fiber", 999, due(6), 1, "DUE"),
            ("B4", "C1001", "Mutual fund SIP", 5_000, due(8), 1, "DUE"),
            ("B5", "C1002", "BESCOM Electricity", 1_870, due(3), 0, "DUE"),
            ("B6", "C1003", "Adani Electricity", 2_960, due(1), 0, "DUE"),
        ],
    )
    conn.executemany(
        "INSERT INTO payees(customer_id, name, upi_id, first_paid) VALUES (?,?,?,?)",
        [
            ("C1001", "Ravi Kumar", "ravi.k@okaxis", due(-60)),
            ("C1001", "Mom", "sunita.sharma@oksbi", due(-300)),
            ("C1002", "Arjun Iyer", "arjun.iyer@okhdfc", due(-200)),
        ],
    )


def _insert_products(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?)",
        [
            (
                "P-PL",
                "Pre-approved personal loan",
                "loan",
                "Up to Rs 5,00,000 at 10.99% for salaried customers",
                json.dumps(
                    {
                        "min_income": 50_000,
                        "max_amount": 500_000,
                        "rate": 10.99,
                        "segments": ["salaried"],
                    }
                ),
            ),
            (
                "P-FD",
                "Fixed deposit, 1 year",
                "deposit",
                "7.10% a year, from Rs 10,000",
                json.dumps({"min_balance": 50_000, "rate": 7.10}),
            ),
            (
                "P-CC",
                "Platinum credit card upgrade",
                "card",
                "5% back on travel, no annual fee in year one",
                json.dumps({"min_income": 100_000, "needs_credit_card": True}),
            ),
            (
                "P-RD",
                "Recurring deposit",
                "deposit",
                "6.8% a year, from Rs 500 a month",
                json.dumps({"min_income": 0, "rate": 6.8}),
            ),
        ],
    )


def seed(force: bool = False) -> None:
    db.init_db()
    if db.one("SELECT id FROM customers LIMIT 1") and not force:
        return
    rng = random.Random(42)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    with db.connect() as conn:
        # One transaction, so the ~2,000 inserts commit once instead of one by one.
        conn.execute("BEGIN")
        for table in RESET_TABLES:
            conn.execute(f"DELETE FROM {table}")
        _insert_people(conn)
        count = _insert_transactions(conn, rng, today)
        _insert_cards_and_loans(conn, today)
        _insert_bills_and_payees(conn, today)
        _insert_products(conn)
        conn.execute("COMMIT")
    logger.info("seeded %d transactions for %d customers", count, len(CUSTOMERS))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed(force=True)
