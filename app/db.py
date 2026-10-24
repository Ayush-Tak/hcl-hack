"""SQLite access. One short-lived connection per unit of work keeps threads independent."""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY, name TEXT, pin_hash TEXT, role TEXT, home_city TEXT,
    monthly_income INTEGER, segment TEXT, phone_masked TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, customer_id TEXT, type TEXT, number_masked TEXT, balance INTEGER
);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY, account_id TEXT, customer_id TEXT, ts TEXT, amount INTEGER,
    direction TEXT, merchant TEXT, category TEXT, channel TEXT, city TEXT,
    status TEXT, utr TEXT, counterparty TEXT, fraud_score REAL, fraud_band TEXT
);
CREATE INDEX IF NOT EXISTS ix_txn_customer_ts ON transactions(customer_id, ts);
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY, customer_id TEXT, type TEXT, number_masked TEXT, status TEXT,
    credit_limit INTEGER, outstanding INTEGER, statement_amount INTEGER, min_due INTEGER,
    due_date TEXT, daily_limit INTEGER
);
CREATE TABLE IF NOT EXISTS loans (
    id TEXT PRIMARY KEY, customer_id TEXT, type TEXT, principal INTEGER, outstanding INTEGER,
    emi INTEGER, next_emi_date TEXT, rate REAL, tenure_left INTEGER
);
CREATE TABLE IF NOT EXISTS bills (
    id TEXT PRIMARY KEY, customer_id TEXT, name TEXT, amount INTEGER, due_date TEXT,
    autopay INTEGER, status TEXT
);
CREATE TABLE IF NOT EXISTS payees (
    id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT, name TEXT, upi_id TEXT, first_paid TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY, name TEXT, type TEXT, description TEXT, rule TEXT
);
CREATE TABLE IF NOT EXISTS complaints (
    id TEXT PRIMARY KEY, customer_id TEXT, txn_id TEXT, type TEXT, status TEXT, created_at TEXT,
    tat_date TEXT, compensation_per_day INTEGER, note TEXT, trace_id TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, customer_id TEXT, txn_id TEXT, score REAL, band TEXT, reasons TEXT,
    analyst TEXT, status TEXT, created_at TEXT, trace_id TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY, customer_id TEXT, topic TEXT, summary TEXT, status TEXT,
    created_at TEXT, trace_id TEXT
);
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY, customer_id TEXT, type TEXT, args TEXT, summary TEXT, otp_hash TEXT,
    expires_at TEXT, attempts INTEGER, status TEXT, result TEXT, risk TEXT, trace_id TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS traces (
    trace_id TEXT PRIMARY KEY, customer_id TEXT, ts TEXT, message TEXT, answer TEXT,
    answer_type TEXT, mode TEXT, latency_ms INTEGER, llm_calls INTEGER, tool_calls INTEGER,
    tokens_in INTEGER, tokens_out INTEGER
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, seq INTEGER, ts TEXT, kind TEXT,
    name TEXT, payload TEXT, prev_hash TEXT, hash TEXT
);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER);
"""


def _connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def connect() -> Generator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> None:
    with connect() as conn:
        conn.execute(sql, params)


def next_counter(name: str) -> int:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO counters(name, value) VALUES (?, 0)", (name,)
        )
        conn.execute("UPDATE counters SET value = value + 1 WHERE name = ?", (name,))
        value = conn.execute(
            "SELECT value FROM counters WHERE name = ?", (name,)
        ).fetchone()[0]
        conn.execute("COMMIT")
        return value


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
