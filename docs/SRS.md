# Software Requirements Specification: Daily Banking Assistant

Version 1.0 · 24 Oct 2026 · Team hcl-hack

## 1. Introduction

**Purpose.** This document lists what the Daily Banking Assistant must do, how well it must do it, and how we'll prove it. Every requirement has an ID that our code, tests and demo refer to.

**Scope.** A conversational assistant for retail banking customers covering accounts, spending, cards, UPI payments, loans, bills, lost cards, suspicious transactions and bank policy questions, plus an operations view for bank staff. It runs on synthetic data and simulated banking systems.

**Design principle.** The language model understands requests and explains results. Deterministic tools retrieve data, calculate amounts, check eligibility and take actions. Risky actions need confirmation and human oversight.

**Terms.** *Tool*: a plain Python function over bank data, with fixed inputs and outputs. *Intent*: the type of request, extracted by the LLM. *Trace ID*: a unique ID for each request, used to audit it. *Grounded*: backed by a tool result or a retrieved policy passage.

**Reference.** HCLTech "AI-Powered Daily Banking Assistant" use case guide v1.0 (requirements R1 to R8, use cases 1 to 10).

## 2. Overall description

**User classes.**
- *Customer*: asks about their own accounts and requests actions. Sees only their own data.
- *Operations officer*: reviews escalated disputes and audit trails. Cannot use customer chat actions.

**Operating environment.** A local web app: FastAPI backend, SQLite database, local vector index, a hosted LLM API (Gemini, with Groq as fallback), a browser UI.

**Constraints.** Built in about 5 hours by 3 people. Synthetic data only. No real payments, SMS or core-banking integration.

**Assumptions.** Single currency (INR). Demo login uses customer ID + PIN. OTP is simulated and shown in the demo UI.

## 3. Functional requirements

Priority: **MVP** (must be in the demo), **Should**, **Stretch**.

| ID | Requirement | Priority | Acceptance criteria |
| --- | --- | --- | --- |
| FR-01 | Customer login and session | MVP | Login with customer ID + PIN returns a token. Requests without a valid token get 401. The customer is always taken from the token, never from message text. |
| FR-02 | Understand requests | MVP | The LLM returns a validated intent + details for every supported request type. Unsupported or unclear requests return `clarification_needed`. At least 90% intent accuracy on our test set. |
| FR-03 | Balance and account summary (UC1) | MVP | Shows available balance, masked account number, and recent credits and debits from the accounts tool. |
| FR-04 | Transaction history (UC1) | MVP | Lists transactions filtered by count, date range, merchant or amount. |
| FR-05 | Expense analysis (UC2) | MVP | Totals by category for a period, with change vs the previous period. Known merchants use fixed rules; unknown merchants are categorised once by the LLM and cached. Totals always computed in code. |
| FR-06 | Credit card details (UC3) | MVP | Statement due date, total due, minimum due, and repayment options cited from policy. |
| FR-07 | UPI payment status (UC4) | MVP | Finds the payment by merchant, date or amount; shows status, reference number and failure reason when one exists. Several matches → asks which one. |
| FR-08 | Loans and EMI (UC5) | MVP | Next EMI amount and date, outstanding balance, schedule. |
| FR-09 | Upcoming payments and funds after bills (UC7) | MVP | Lists card dues, EMIs, bills and subscriptions due in the period; remaining funds = balance minus total dues, calculated in code; `answer_type: calculated`. |
| FR-10 | Savings observations (UC6) | Should | Non-binding observations computed in code (e.g. subscriptions, categories above their 3-month average), clearly marked as not financial advice. |
| FR-11 | Lost or stolen card (UC8) | MVP | Creates a pending action; executes only after the customer confirms and enters a valid OTP; blocks the card and creates a replacement request; returns `action_completed` with a reference. |
| FR-12 | Unrecognised transaction (UC9) | MVP | Runs the fraud risk scorer; shows the score with plain-language reasons; opens a dispute (after confirmation); high-risk cases are `escalated` to the review queue and the customer is offered a card block. |
| FR-13 | Bank policy questions | MVP | Answers only from retrieved policy passages, citing document, section, version and effective date. Retrieval below the threshold returns `not_found`. |
| FR-14 | Pre-approved offers (UC10) | Stretch | Eligibility decided by a rules tool over income, existing EMIs and repayment history; the LLM only explains. |
| FR-15 | Privacy refusal | MVP | Any request about another customer's data returns `refused` and is logged. |
| FR-16 | Operations review queue (R7) | MVP | Operations officers list escalated cases with score, reasons and trace ID, and resolve them with a note. |
| FR-17 | Audit trail (R8) | MVP | Every request gets a trace ID and an audit row: customer, intent, tools invoked with inputs and outcomes, sources, answer type, latency, errors. Viewable by trace ID. |
| FR-18 | Live policy updates | Should | An officer uploads a new or revised policy document; it is searchable immediately without a restart; the newest effective version is cited. |
| FR-19 | No invented data (R6) | MVP | Missing records return `not_found` with what was searched; the assistant never invents balances, transactions, charges or eligibility. |

## 4. Non-functional requirements

| ID | Area | Requirement | Target / how we check |
| --- | --- | --- | --- |
| NFR-01 | Security | Token auth on every endpoint; PINs stored hashed; OTP valid 5 minutes, 3 attempts; secrets only in `.env`. | Auth tests; code review |
| NFR-02 | Privacy | Tools receive the customer ID only from the session; account and card numbers masked to the last 4 digits; PINs, OTPs and full numbers never logged. | Privacy tests in the test set |
| NFR-03 | Accuracy | Every amount in a reply matches a tool output (checked by a validator before sending); zero invented transactions. | 100% validator pass on the test set |
| NFR-04 | Latency | Chat p50 under 2.5 s, p95 under 6 s on the demo laptop; tool-only endpoints p95 under 300 ms. | Measured by the evaluation script |
| NFR-05 | Reliability | LLM calls time out at 15 s with 1 retry, then fall back to Groq, then to keyword intent rules; failures return a friendly message with the trace ID. | Fallback test with the key removed |
| NFR-06 | Explainability | Every answer shows its evidence: tools invoked, policy citations, or fraud reasons. | Visible in every demo response |
| NFR-07 | Untrusted content | Policy text and transaction descriptions are passed to the LLM as delimited data and never followed as instructions. | 3 prompt-injection cases in the test set |
| NFR-08 | Maintainability | One typed function per tool; tests for every tool and the fraud scorer; formatted with ruff. | pytest + ruff |
| NFR-09 | Repeatability | Fresh setup with `uv sync`, one seed command and one run command. | README steps work from a fresh clone |

## 5. External interfaces

**UI.** Customer chat (answer-type badge, amounts, citations, a confirmation + OTP dialog, a spending chart) and an operations dashboard (review queue, audit lookup by trace ID).

**API.** `POST /auth/login`, `POST /chat`, `POST /actions/{id}/confirm`, `GET /me/summary`, `GET /me/spending`, `GET /ops/review-queue`, `POST /ops/review-queue/{id}/resolve`, `GET /ops/audit/{trace_id}`, `POST /ops/policies` (FR-18), `GET /health`.

**Chat response shape** (every reply):

```json
{
  "trace_id": "BANK-20261024-000123",
  "answer": "Your balance is Rs 1,25,000. Bills due this week total Rs 35,000, leaving about Rs 90,000.",
  "answer_type": "calculated",
  "data": {"balance": 125000, "upcoming_total": 35000, "remaining": 90000},
  "citations": [],
  "pending_action": null,
  "tools_invoked": [
    {"name": "get_balance", "ok": true, "ms": 4},
    {"name": "get_upcoming_payments", "ok": true, "ms": 6}
  ],
  "latency_ms": 1840
}
```

`pending_action`, when present: `{"action_id": "...", "type": "block_card", "summary": "Block debit card ending 4417", "requires_otp": true}`.

## 6. Data requirements

Synthetic, realistic and internally consistent: 3 customers; savings accounts; about 3 months of transactions each (salary, rent, food, transport, shopping, bills, subscriptions) with merchant, channel, city and timestamp; at least 3 planted anomalies per customer for fraud demos; UPI payments with success, failure and pending states and failure reasons; debit and credit cards; loans with EMI schedules; bills and subscriptions; a merchant-to-category rules table; 5 policy documents (fees and charges, cards, UPI, disputes, loans), each with a version and effective date, one of them with an older superseded version.

## 7. Evaluation plan

A 30-question test set (`eval/golden.jsonl`): 20 answerable questions across all intents, 4 that should give `not_found`, 3 privacy refusals, 3 prompt-injection attempts.

| Metric | Definition | Target |
| --- | --- | --- |
| Intent accuracy | Correct intent / all questions | 90% or more |
| Tool accuracy | Expected tools called with correct values | 95% or more |
| Policy retrieval hit@3 | Correct policy section in the top 3 | 90% or more |
| Citation accuracy | Cited section actually supports the answer | 100% |
| Abstention and refusal | Correct `not_found` / `refused` | 100% |
| Injection resistance | Injection attempts that change behaviour | 0 |
| Latency | Chat p50 / p95 | under 2.5 s / 6 s |

## 8. Traceability to HCL requirements

| HCL requirement | Our requirements | Where it lives | How it's tested |
| --- | --- | --- | --- |
| R1 Secure authentication | FR-01, FR-11, FR-15, NFR-01 | Auth, policy gate, OTP | Auth and privacy tests |
| R2 Real-time banking data | FR-03 to FR-09 | Banking tools over SQLite | Tool tests, tool accuracy |
| R3 Financial insights | FR-05, FR-09, FR-10 | Expense analysis | Test set |
| R4 Card and payment actions | FR-11, FR-12 | Pending actions, confirm endpoint | Action tests |
| R5 Personalised banking | FR-14 | Offers rules tool | Stretch |
| R6 No hallucinations | FR-13, FR-19, NFR-03 | Grounding check, amount validator | Abstention metrics |
| R7 Human oversight | FR-12, FR-16 | Review queue | Escalation tests |
| R8 Auditability | FR-17 | Audit log, ops audit view | Audit row check per request |

## 9. Out of scope

Real core-banking, payment or SMS integration; moving real money; investment or tax advice; multiple currencies; production deployment and scaling (covered as next steps in our design slides).