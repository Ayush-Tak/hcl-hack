# API contract

Teammate 2 implements the routes, the React app consumes them, and each route calls one engine function from `docs/ENGINE.md`. Change it only after telling the team, and log the change in docs/DECISIONS.md.

All endpoints except `/auth/login` and `/health` need `Authorization: Bearer <token>`.

```
POST /auth/login            {"customer_id": "C1001", "pin": "1234"} -> {"token": str, "customer": {"id", "name", "role"}}
POST /chat                  {"message": str, "session_id": str} -> ChatResponse                    handle_chat
POST /actions/{action_id}/confirm  {"otp": "123456"} -> ChatResponse (action_completed or refused)  confirm_action
GET  /me/summary            -> {"balance": int, "account_masked": str, "recent": [Transaction]}    open question 1 in ENGINE.md
GET  /me/spending?period=this_month -> {"by_category": [{"category", "amount"}], "total": int, "previous_total": int}   open question 1
GET  /me/alerts             -> [Alert]                                                             list_alerts(own id)
POST /sim/transactions      {"amount", "merchant", "channel", "city", ...} -> {"txn_id", "score", "band", "reasons", "alert_id"}   ingest_transaction (demo stream)
GET  /ops/review-queue      (role officer) -> [Alert]                                              list_alerts(None)
POST /ops/review-queue/{id}/resolve  (role officer) {"resolution": str, "note": str} -> {"id", "status"}   open question 2
GET  /ops/traces/{trace_id} (officer, or the trace's own customer) -> Trace                        get_trace
GET  /ops/metrics           (role officer) -> Metrics                                              metrics
POST /ops/policies          (role officer, FR-18) multipart markdown -> {"doc", "chunks", "version"}
GET  /health                -> {"status": "ok"}
```

`/ops/traces/{trace_id}` replaces v1's `/ops/audit/{trace_id}`. Shapes of Alert, Trace and Metrics are in docs/ENGINE.md.

ChatResponse:

```
{
  "trace_id": "BANK-20261024-000123",
  "answer": str,
  "answer_type": "retrieved_fact" | "calculated" | "action_pending" | "action_completed" | "clarification_needed" | "not_found" | "refused" | "escalated",
  "data": object | null,
  "citations": [{"doc": str, "section": str, "version": str, "effective_date": str}],
  "pending_action": {"action_id": str, "type": str, "summary": str, "requires_otp": bool, "expires_at": str} | null,
  "warnings": [{"source": "scam_guard" | "pre_payment" | "verifier" | "fallback", "level": "info" | "high", "message": str}],
  "tools_invoked": [{"name": str, "ok": bool, "ms": int}],
  "trace_url": str,
  "latency_ms": int
}
```
