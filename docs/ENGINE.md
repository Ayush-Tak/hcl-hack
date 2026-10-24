# Engine interface

What teammate 2's routes call. The engine lives in `app/engine.py` (owner: Ayush); the response models live in `app/models.py`. The HTTP side is in `docs/API.md`. Change this file only by telling the team, and log the change in `docs/DECISIONS.md`.

Until the engine lands, stub these six functions with the example responses below and build against them.

## Rules for callers

- `customer_id` always comes from `auth.current_user`, never from the request body or the message.
- `handle_chat` calls `app/guard.py`'s `check(message: str) -> None` if the file exists. Raise `ValueError(reason)` to refuse a message; the engine returns `refused`.
- Role checks are the route's job. Officer-only routes check the role before calling the engine.
- The engine functions block (they call LLMs and SQLite), so declare these routes with `def`, not `async def`. FastAPI then runs them in its thread pool.
- The engine doesn't raise when an LLM fails: it falls back and still returns a `ChatResponse`. If it does raise, that's a bug; let it become a 500.

## Functions

### `handle_chat(customer_id: str, message: str, session_id: str) -> ChatResponse`

Runs one chat turn: Scam Guard and the Orchestrator in parallel, gated tool calls, the amount check and the Verifier, then the trace. Session flags (like the scam flag) are stored per `(customer_id, session_id)`, so a session ID from another customer gets nothing.

Possible `answer_type` values: `retrieved_fact`, `calculated`, `action_pending`, `clarification_needed`, `not_found`, `refused`, `escalated`. `action_pending` always comes with `pending_action`.

```json
{
  "trace_id": "BANK-20261024-000123",
  "answer": "Your bills this week total Rs 16,238, which leaves Rs 1,32,012 of your Rs 1,48,250 balance.",
  "answer_type": "calculated",
  "data": {"balance": 148250, "upcoming_total": 16238, "remaining": 132012},
  "citations": [],
  "pending_action": null,
  "warnings": [],
  "tools_invoked": [
    {"name": "get_balance", "ok": true, "ms": 4},
    {"name": "get_upcoming_payments", "ok": true, "ms": 6},
    {"name": "calculate", "ok": true, "ms": 0}
  ],
  "trace_url": "/ops/traces/BANK-20261024-000123",
  "latency_ms": 1840
}
```

### `confirm_action(customer_id: str, action_id: str, otp: str) -> ChatResponse`

Runs a pending action after the OTP check (6 digits, 5 minutes, 3 attempts).

- `action_completed`: `data` holds the outcome, e.g. `{"card": "XXXX 4821", "status": "BLOCKED", "replacement_id": "RPL-000004"}`. A transfer in cooling-off returns `{"status": "scheduled", "release_at": "..."}`.
- `escalated`: a transfer the confirm-time re-check held, because Scam Guard flagged the conversation after the transfer was proposed. `data.status` is `held`, with a `case_id`.
- `refused`: wrong OTP, expired, locked after 3 wrong codes, or the action isn't this customer's. Unknown IDs and other customers' IDs get the same message, so action IDs can't be probed.

### `ingest_transaction(customer_id: str, txn: dict) -> dict`

Stores a new transaction, scores it (rules + IsolationForest, then the Fraud Analyst for medium and high) and creates an alert for medium and high. The demo's "simulate a transaction" button calls it; in production a Pub/Sub push would.

Input `txn`: `amount` (int rupees, required), `merchant` (required), `channel` (`UPI`, `CARD`, `ATM`, `NEFT` or `IMPS`), `city`, and optionally `direction` (default `debit`), `ts` (ISO time, default now), `counterparty`, `status` (default `SUCCESS`).

```json
{
  "txn_id": "T00294",
  "score": 0.85,
  "band": "high",
  "reasons": ["Rs 48,999 is 9x your usual shopping spend", "first payment to this merchant", "made at 02:14", "made in Dubai, away from Jaipur"],
  "alert_id": "AL-000012"
}
```

`alert_id` is `null` for a low band. Raises `ValueError` if `amount` or `merchant` is missing.

### `get_trace(trace_id: str) -> dict | None`

The trace header and its spans, or `None` if there's no such trace (the route returns 404). The route must allow an officer, or the customer the trace belongs to (`customer_id` in the result).

```json
{
  "trace_id": "BANK-20261024-000123", "customer_id": "C1001", "ts": "2026-10-24T14:20:05",
  "message": "...", "answer": "...", "answer_type": "calculated", "mode": "agent",
  "latency_ms": 1840, "llm_calls": 3, "tool_calls": 3, "tokens_in": 2210, "tokens_out": 140,
  "spans": [
    {"seq": 1, "ts": "...", "kind": "llm", "name": "scam_guard", "model": "gemini-2.5-flash", "tokens_in": 310, "tokens_out": 20, "ms": 420, "ok": true, "hash": "9f2c51a0be13"},
    {"seq": 2, "ts": "...", "kind": "gate", "name": "get_balance", "decision": "allow", "tier": "read", "hash": "..."},
    {"seq": 3, "ts": "...", "kind": "tool", "name": "get_balance", "args": {}, "result": {"...": "..."}, "ms": 4, "ok": true, "hash": "..."}
  ],
  "chain_ok": true
}
```

Span `kind` is one of `guard`, `llm`, `gate`, `tool`, `verifier`, `action`. `mode` is `agent` or `fallback`.

### `list_alerts(customer_id: str | None) -> list[dict]`

Alerts, newest first. A customer ID gives that customer's alerts; `None` gives everyone's, for the ops review queue (officer-only route).

```json
[{"id": "AL-000012", "customer_id": "C1001", "txn_id": "T00294", "score": 0.85, "band": "high",
  "reasons": ["..."], "analyst": "Looks like card-not-present fraud: ...", "status": "open",
  "created_at": "2026-10-24T14:20:05", "trace_id": "BANK-20261024-000124"}]
```

### `metrics() -> dict`

For the ops view. Counts are since the database was seeded.

```json
{
  "requests": 42, "latency_ms": {"p50": 1650, "p95": 4100},
  "llm_calls": 118, "tokens_in": 96000, "tokens_out": 5200,
  "fallback_rate": 0.05, "verifier_pass_rate": 0.97,
  "alerts": {"low": 0, "medium": 3, "high": 2}, "open_cases": 1,
  "audit_chain": {"ok": true, "rows": 512}
}
```

## ChatResponse fields

| Field | Type | Notes |
| --- | --- | --- |
| `trace_id` | str | `BANK-YYYYMMDD-NNNNNN` |
| `answer` | str | Plain text; amounts already formatted as Rs with Indian grouping |
| `answer_type` | str | The seven `handle_chat` values, plus `action_completed` from `confirm_action` |
| `data` | object or null | Raw numbers for cards and charts (integer rupees) |
| `citations` | list | `{"doc", "section", "version", "effective_date"}` |
| `pending_action` | object or null | `{"action_id", "type", "summary", "requires_otp", "expires_at"}` |
| `warnings` | list | **New.** `{"source": "scam_guard" \| "pre_payment" \| "verifier" \| "fallback", "level": "info" \| "high", "message"}`. Show `high` ones prominently. |
| `tools_invoked` | list | `{"name", "ok", "ms"}` |
| `trace_url` | str | **New.** API path of this request's trace; the UI links its trace viewer to it |
| `latency_ms` | int | Whole turn, measured by the engine |

## What the engine needs from teammate 3

- `app/fraud/ml.py`: `score(txn: dict, history: list[dict]) -> float`. The engine uses it only if the file exists. `history` is the customer's last 90 days of debits (the same keys as `txn`). Return how unusual `txn` is as a percentile from 0 to 1 against that history (0.95 means more unusual than 95% of it). The engine adds the +0.15 signal at 0.95 or above. No database access inside, so it's easy to test.
- `data/policies/*.md`: front matter with `doc`, `version`, `effective_date`; one `## ` heading per section, because we chunk on those headings.
- `eval/questions.jsonl`, one JSON object per line: `{"id", "customer_id", "question", "expected_answer_type", "expected_tools": [...], "expected_amounts": [...], "notes"}`.

### `get_summary(customer_id: str) -> dict`

For `GET /me/summary`: `{"balance", "account_masked", "recent": [5 transactions]}`. No LLM.

## Open questions (not in the interface yet)

1. `GET /me/spending` has no engine function yet. Proposal: `get_spending(customer_id, period)`, a thin wrapper over `analyze_expenses`.
2. The review queue's resolve button needs `resolve_alert(alert_id, resolution, note) -> dict`.
3. Cancelling a cooling-off transfer needs `cancel_action(customer_id, action_id) -> ChatResponse`.
