"""Per-request trace: every gate decision, model call, tool call and check becomes a span.

Spans are stored in a hash-chained audit table, so a changed or deleted row breaks the chain.
"""

import hashlib
import json
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from app import db

GENESIS = "GENESIS"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_trace_id() -> str:
    return f"BANK-{datetime.now():%Y%m%d}-{db.next_counter('trace'):06d}"


def _shorten(value: object, limit: int = 1500) -> object:
    text = db.dumps(value)
    return value if len(text) <= limit else text[:limit] + "...(truncated)"


def _chain_hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()


class Trace:
    def __init__(self, customer_id: str, message: str) -> None:
        self.id = new_trace_id()
        self.customer_id = customer_id
        self.message = message
        self.spans: list[dict] = []
        self.started = time.perf_counter()
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        # Parallel tool calls add spans from several threads at once.
        self._lock = threading.Lock()

    def add(self, kind: str, name: str, **fields: Any) -> dict:
        with self._lock:
            span = {
                "seq": len(self.spans) + 1,
                "ts": _now_iso(),
                "kind": kind,
                "name": name,
            }
            span.update({key: _shorten(value) for key, value in fields.items()})
            self.spans.append(span)
            if kind == "llm":
                self.llm_calls += 1
                self.tokens_in += int(fields.get("tokens_in") or 0)
                self.tokens_out += int(fields.get("tokens_out") or 0)
            if kind == "tool":
                self.tool_calls += 1
            return span

    @contextmanager
    def span(self, kind: str, name: str, **fields: Any) -> Generator[dict]:
        """Times a block; the caller fills the yielded dict with whatever should be recorded."""
        out: dict = {}
        start = time.perf_counter()
        try:
            yield out
            out.setdefault("ok", True)
        except Exception as exc:
            out.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            ms = int((time.perf_counter() - start) * 1000)
            self.add(kind, name, **(fields | out | {"ms": ms}))

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def finish(self, answer: str, answer_type: str, mode: str) -> None:
        db.execute(
            "INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.id,
                self.customer_id,
                _now_iso(),
                self.message,
                answer,
                answer_type,
                mode,
                self.elapsed_ms(),
                self.llm_calls,
                self.tool_calls,
                self.tokens_in,
                self.tokens_out,
            ),
        )
        self._append_to_chain()

    def _append_to_chain(self) -> None:
        with db.connect() as conn:
            # BEGIN IMMEDIATE takes the write lock before we read the last hash, so two requests
            # (even in two worker processes) can never chain onto the same row.
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute(
                "SELECT hash FROM audit ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev = last[0] if last else GENESIS
            for span in self.spans:
                payload = db.dumps(span)
                digest = _chain_hash(prev, payload)
                conn.execute(
                    "INSERT INTO audit(trace_id, seq, ts, kind, name, payload, prev_hash, hash) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        self.id,
                        span["seq"],
                        span["ts"],
                        span["kind"],
                        span["name"],
                        payload,
                        prev,
                        digest,
                    ),
                )
                prev = digest
            conn.execute("COMMIT")


def load(trace_id: str) -> dict | None:
    head = db.one("SELECT * FROM traces WHERE trace_id = ?", (trace_id,))
    if not head:
        return None
    rows = db.query(
        "SELECT payload, hash FROM audit WHERE trace_id = ? ORDER BY seq", (trace_id,)
    )
    head["spans"] = [
        json.loads(row["payload"]) | {"hash": row["hash"][:12]} for row in rows
    ]
    return head


def verify_chain() -> dict:
    """Recompute the whole chain; returns the first broken row if there is one."""
    prev = GENESIS
    rows = db.query("SELECT id, payload, prev_hash, hash FROM audit ORDER BY id")
    for row in rows:
        if row["prev_hash"] != prev or row["hash"] != _chain_hash(prev, row["payload"]):
            return {"ok": False, "broken_at": row["id"], "rows": len(rows)}
        prev = row["hash"]
    return {"ok": True, "rows": len(rows)}
