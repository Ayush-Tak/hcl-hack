# API contract

Backend implements it, frontend consumes it. Change it only after telling the team, and log the change in docs/DECISIONS.md.

All endpoints except `/auth/login` and `/health` need `Authorization: Bearer <token>`.

```
POST /auth/login            {"customer_id": "C1001", "pin": "1234"} -> {"token": str, "customer": {"id", "name", "role"}}
POST /chat                  {"message": str, "session_id": str} -> ChatResponse
POST /actions/{action_id}/confirm  {"otp": "123456"} -> ChatResponse (answer_type action_completed or refused)
GET  /me/summary            -> {"balance": int, "account_masked": str, "recent": [Transaction]}
GET  /me/spending?period=current_month -> {"by_category": [{"category", "amount"}], "total": int, "previous_total": int}
GET  /ops/review-queue      (role officer) -> [{"id", "customer_id", "txn_id", "score", "reasons": [str], "status", "trace_id"}]
POST /ops/review-queue/{id}/resolve  {"resolution": str, "note": str} -> {"id", "status"}
GET  /ops/audit/{trace_id}  (role officer) -> AuditRecord
POST /ops/policies          (role officer, FR-18) multipart markdown -> {"doc", "chunks", "version"}
GET  /health                -> {"status": "ok"}
```

ChatResponse:

```
{
  "trace_id": "BANK-20261024-000123",
  "answer": str,
  "answer_type": "retrieved_fact" | "calculated" | "action_completed" | "clarification_needed" | "not_found" | "refused" | "escalated",
  "data": object | null,
  "citations": [{"doc": str, "section": str, "version": str, "effective_date": str}],
  "pending_action": {"action_id": str, "type": str, "summary": str, "requires_otp": bool} | null,
  "tools_invoked": [{"name": str, "ok": bool, "ms": int}],
  "latency_ms": int
}
```