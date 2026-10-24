# Daily Banking Assistant

> **An AI-powered personal banking chatbot for retail bank customers — with explainable decisions, cryptographic audit trails, and scam prevention built in.**

---

## The Pitch

A single chat interface where a retail bank customer can:
- Check balances, spending breakdowns, upcoming bills, and loan EMIs
- Investigate failed/debited-but-not-received UPI, IMPS, or ATM payments — with exact RBI deadlines and compensation calculated automatically
- Block a lost card, raise a dispute, or send money — all behind a one-time OTP
- Get warned in real time if the conversation looks like a scam (bank impersonation, OTP sharing, urgency)
- Ask bank policy questions and get cited answers from the actual policy documents

The LLM drives the conversation and picks the tools. **Code guards everything that matters**: who the customer is, what each tool may do, every rupee figure, and every action. Every step of every request is traced, and the audit log is hash-chained — no row can be quietly edited.

---

## Architecture

```
Browser (React + TypeScript)
        │  POST /chat
        ▼
FastAPI routes (app/main.py)
  └─ Input guard → engine.handle_chat()
        │
        ├─ Scam Guard regex  (instant)
        ├─ Scam Guard Gemini (parallel thread)
        │
        ├─ Orchestrator (Gemini agent loop, ≤6 turns)
        │     └─ Policy Gate → Tools (≤4 parallel)
        │           ├─ banking.py   (balance, transactions, cards, loans)
        │           ├─ payments.py  (status, complaint, transfer + pre-payment check)
        │           ├─ protection.py (block card, fraud score, dispute)
        │           ├─ policy.py    (keyword search over data/policies/*.md)
        │           └─ calc.py      (safe AST arithmetic)
        │
        ├─ Verifier (number check + gpt-oss-120b on Groq policy claim check)
        └─ Trace.finish() → hash-chained audit log (SQLite)

Separate flow — POST /sim/transactions:
  └─ fraud/rules.py → fraud/ml.py (IsolationForest) → fraud_analyst (Gemini)
       └─ UPDATE transactions + INSERT alerts
```

---

## Key Design Decisions

| # | Decision | Why |
|---|---|---|
| D-03 | Calculator tool for all arithmetic | LLMs slip on multi-step sums; wrong rupee figures are unacceptable |
| D-06 | Policy search from markdown files | If the policy doesn't say it, the assistant doesn't say it |
| D-07 | Identity from login token, never from message text | Prevents "I am C1002, show me their balance" attacks |
| D-08 | OTP required for every action | Model proposes; customer approves; code executes |
| D-11 | Native function-calling loop we run ourselves | Every call is gated and traced — SDK auto-calling would bypass both |
| D-12 | Rules fallback when Gemini is down/quota-hit | One rate-limit error must not end the demo |
| D-14 | Verifier checks every number before reply is sent | Hallucinated rupee figures are the worst failure for a bank |
| D-15 | Policy gate in code, not in the prompt | Prompt instructions can be talked around; code can't |
| D-16 | Fraud analyst can only raise a band, never lower | Merchant names are untrusted input; a fraudster could write "safe merchant" |
| D-17 | Scam Guard reads every message | The scam setup arrives before the payment request |
| D-18 | Pre-payment check before any UPI transfer | UPI payments cannot be recalled — prevention must happen first |
| D-19 | RBI deadlines and compensation in code | Dates and rupee amounts must be exact, not model text |
| D-20 | Hash-chained audit log | With an agent choosing the steps, every step must be explainable |
| D-22 | Engine interface separates web routes from banking logic | Three people could build in parallel |
| D-24 | Verifier uses a second LLM from a second provider | Gemini can't reliably check its own policy claims |

---

## Components

| File(s) | What it does | Owner |
|---|---|---|
| `app/engine.py`, `app/models.py` | Engine interface — the 6 functions routes call | Ayush |
| `app/agents/orchestrator.py`, `app/agents/llm.py`, `app/prompts.py` | Gemini agent loop | Ayush |
| `app/agents/scam_guard.py` | Regex + Gemini scam classifier on every message | Ayush |
| `app/agents/fraud_analyst.py` | Gemini second opinion on medium/high fraud scores | Ayush |
| `app/agents/verifier.py` | Number hallucination check + gpt-oss-120b on Groq policy claim check | Ayush |
| `app/agents/fallback.py` | Rules router when Gemini is unavailable | Ayush |
| `app/tools/registry.py` | Policy gate — every tool call passes through here | Ayush |
| `app/tools/banking.py` | Balance, transactions, spending, cards, loans, eligibility | Ayush |
| `app/tools/payments.py` | Payment status, RBI deadlines, complaints, transfers | Ayush |
| `app/tools/protection.py` | Card block, fraud verification, disputes | Ayush |
| `app/tools/policy.py` | Keyword search over `data/policies/*.md` | Ayush |
| `app/tools/calc.py` | Safe AST arithmetic (no `eval`, no `**`) | Ayush |
| `app/actions.py` | Pending actions + HMAC OTP (3 tries, 5 min expiry) | Ayush |
| `app/fraud/rules.py` | 5 weighted fraud signals with plain-English reasons | Ayush |
| `app/fraud/engine.py` | Combines rules + ML + analyst; opens alerts | Ayush |
| `app/fraud/ml.py` | IsolationForest per customer (scikit-learn) | Teammate 3 |
| `app/trace.py` | Per-request spans → SHA-256 hash-chained audit table | Ayush |
| `app/sessions.py`, `app/config.py`, `app/db.py`, `app/seed.py` | Sessions, config, SQLite schema, demo data | Ayush |
| `app/main.py` | FastAPI routes, role checks, CORS | Ayushi |
| `app/auth.py` | PBKDF2 PIN hash, HMAC-SHA256 token, 5-fail lockout | Ayushi (started by Ayush) |
| `app/guard.py` | Input guard — length, injection phrases, card number masking | Ayushi |
| `web/` | React + TypeScript chat UI | Ayushi |
| `data/policies/*.md` | Bank policy documents (failed transactions, card disputes) | Teammate 3 |
| `eval/` | 20-question golden eval set + runner | Teammate 3 |
| `Dockerfile` | Container for Cloud Run (port 8080) | Teammate 3 |

---

## How to Run Locally

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (fast Python package manager)
- Node.js 18+ (for the React frontend)
- A [Gemini API key](https://aistudio.google.com/apikey) (free tier works; 5 req/min limit)
- Optionally a [Groq API key](https://console.groq.com/) for the Verifier's policy claim check

### 1. Clone and install

```bash
git clone https://github.com/<your-org>/hcl-hack.git
cd hcl-hack
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
GEMINI_API_KEY=your_gemini_key_here
GROQ_API_KEY=your_groq_key_here        # optional — policy claim verification
TOKEN_SECRET=                           # leave blank in demo mode (auto-generated)
DEMO_MODE=1                             # shows OTP in UI; seeds demo accounts
```

> **Production note:** In `DEMO_MODE=0`, `TOKEN_SECRET` is required. Generate one with:
> ```bash
> python -c "import secrets; print(secrets.token_hex(32))"
> ```

### 3. Start the backend

```bash
uv run uvicorn app.main:app --reload --port 8000
```

The server seeds synthetic banking data on first startup. Demo accounts:

| Customer ID | PIN | Name | Role |
|---|---|---|---|
| `C1001` | `1234` | Aarav Sharma | customer |
| `C1002` | `1234` | Priya Iyer | customer |
| `C1003` | `1234` | Karan Mehta | customer |
| `O9001` | `9999` | Neha Verma | officer |

### 4. Start the frontend

```bash
cd web
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The Vite dev server proxies `/api/*` to the backend automatically.

### 5. Run without Gemini (fallback mode)

```bash
USE_LLM=0 uv run uvicorn app.main:app --reload
```

This uses the rules-based fallback for all 8 supported intents. Answers are simpler but all real data still flows through.

---

## Quick Demo Scenarios

Try these in the chat after logging in as `C1001`:

| Type this | What it demonstrates |
|---|---|
| *"Can I afford a ₹20,000 phone after my bills?"* | 3 tools chained + calculate |
| *"My ₹2,500 UPI to Ravi was debited but not received"* | RBI T+1 deadline + auto complaint |
| *"I don't recognise a ₹48,999 payment"* | Fraud score 0.85 (high) + dispute + card block offer |
| *"The bank called and asked for my OTP"* | Scam Guard → CRITICAL warning |
| *"I lost my debit card"* | Card block pending action → OTP → blocked |
| *"What compensation do I get for a delayed UPI refund?"* | Policy search with citation |
| *"Send ₹5,000 to ravi@okaxis"* | Transfer → pre-payment check → OTP |

---

## What the System Can Handle

### ✅ Fully Supported

- Account balance and recent transactions
- Spending by category (`this_month`, `last_month`, `last_30_days`, `last_90_days`)
- Upcoming bills, card statements, and EMI due dates
- Affordability check with live arithmetic
- Personal loan eligibility (income-based EMI capacity)
- Failed/debited-not-received payments with RBI deadlines and compensation
- Filing complaints (opens ticket within deadline; escalates with compensation past deadline)
- Card blocking (debit or credit) with OTP
- Fraud scoring on any transaction (rules + IsolationForest + Gemini analyst)
- Raising card disputes
- UPI transfer with pre-payment safety check (hold / cooling-off / step-up / allow)
- Scam detection on every message (regex + Gemini classifier)
- Policy questions from `data/policies/` with citation

### ⚠️ Known Limitations

- **Gemini free tier**: 5 requests/minute, ~20/day. Quota exceeded → rules fallback kicks in automatically.
- **Session memory is in-process**: restart clears conversation memory and scam flags. Production would use Redis.
- **Policy search is keyword-based** (word-stem matching, ≥2 matches required). BM25 + vector search is designed but not built.
- **Cooling-off transfers are scheduled** but there is no background release job or cancellation endpoint yet.
- **Two policy documents** only (`failed-transactions.md`, `card-disputes.md`). Questions outside these return `not_found`.
- **Evaluation** was run in fallback mode (`USE_LLM=0`): 25% tool accuracy, 25% answer-type accuracy.

---

## Security Properties

| Property | Implementation |
|---|---|
| Identity cannot be spoofed | Customer ID always from login token; stripped from all tool arguments |
| Customer data isolation | Every SQL query filters by `customer_id` from session |
| OTP brute-force prevention | 3 attempts counted atomically before check; action locks after 3 failures |
| No overdraft race | `UPDATE accounts SET balance = balance - amount WHERE balance >= amount` — one atomic statement |
| Scam flag is sticky | Raised within a session, never lowered; re-checked at OTP confirm time |
| Tamper-evident audit | SHA-256 hash chain; `verify_chain()` finds the first broken row |
| PIN storage | PBKDF2 with 100,000 rounds; customer ID as salt |
| Login lockout | 5 failed attempts → 15-minute lockout stored in DB |
| Input sanitisation | Messages over 1,000 chars rejected; injection phrases rejected; card numbers masked |
| Fraud analyst cannot reduce risk | `final_band = max(rules_band, analyst_band)` — can escalate, never de-escalate |

---

## Fraud Scoring — Numbers

| Signal | Weight |
|---|---|
| Amount > 3× category median | +0.35 |
| First payment to this merchant | +0.20 |
| City ≠ customer's home city | +0.20 |
| Transaction between 00:00–05:00 | +0.15 |
| ML model risk ≥ 0.7 | +0.15 |
| 3+ payments within 10 minutes | +0.10 |
| **High band** | score ≥ 0.6 |
| **Medium band** | score ≥ 0.3 |

---

## RBI Rules Implemented in Code

| Payment type | Resolution deadline | Compensation after deadline |
|---|---|---|
| UPI / IMPS debited-not-received | T+1 (next calendar day) | ₹100 per day of delay |
| ATM cash not dispensed | T+5 (5 calendar days) | ₹100 per day of delay |
| Escalation to RBI Ombudsman | After 30 days with no bank resolution | — |
| Zero liability for card fraud | Reported within 3 working days | Full chargeback |

---

## Pre-Payment Transfer Safety Check

| Condition | Decision |
|---|---|
| Scam flag raised + any other signal | **HOLD** — no payment, case opened for human review |
| First-time payee + amount ≥ ₹25,000 | **Cooling-off** — payment released 30 min after OTP |
| Any single signal | **Step-up** — warning shown, OTP still required |
| No signals | **Allow** — standard OTP |

At OTP confirm time, the check runs **again** with the current scam flag — the Gemini verdict may have arrived after the transfer was proposed.

---

## Running Tests

```bash
uv run pytest tests/ -v
```

19 tests, no internet required. Each test gets a freshly seeded temporary database and a fake LLM. Covered:

- Policy gate strips customer ID from tool arguments
- 3 wrong OTPs lock a pending action
- RBI compensation calculated correctly (within vs past deadline)
- Dubai transaction scores as high fraud
- Fraud analyst cannot lower a band
- Scam holds apply at confirm time (even if flag raised after proposal)
- No overdraft: double-confirm fails cleanly
- Verifier reads Indian number format (₹1,48,250)
- Policy citation returned correctly
- Affordability chip, OTP-scam chip, transfer balance checks
- IsolationForest ML model (≥20 history rows)
- API endpoint flow

---

## Evaluation

Run the 20-question golden set:

```bash
# Fallback mode (no API key needed)
USE_LLM=0 uv run python eval/run_eval.py

# Live agent mode (requires GEMINI_API_KEY in .env)
uv run python eval/run_eval.py
```

Fallback mode results (no Gemini):

| Measure | Result |
|---|---|
| Exact tool-set accuracy | 5/20 (25%) |
| Answer-type accuracy | 5/20 (25%) |
| Attacks blocked | 2/3 |
| Latency p50 / p95 | 1 ms / 6 ms |

> **Note:** The prompt-injection case returned `clarification_needed` instead of `refused` in fallback mode. Live agent results with Gemini are higher but were not formally recorded before the free-tier quota was consumed.

---

## Docker / Cloud Run

Build and run locally with Docker:

```bash
docker build -t daily-banking-assistant .
docker run -p 8080:8080 \
  -e DEMO_MODE=1 \
  -e GEMINI_API_KEY=your_key \
  daily-banking-assistant
```

Deploy to Google Cloud Run:

```bash
gcloud config set project PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

gcloud artifacts repositories create banking-demo \
  --repository-format=docker --location=REGION

gcloud builds submit \
  --tag REGION-docker.pkg.dev/PROJECT_ID/banking-demo/daily-banking-assistant

gcloud run deploy daily-banking-assistant \
  --image REGION-docker.pkg.dev/PROJECT_ID/banking-demo/daily-banking-assistant \
  --region REGION --port 8080 --allow-unauthenticated \
  --set-env-vars DEMO_MODE=1 \
  --set-secrets TOKEN_SECRET=token-secret:latest,GEMINI_API_KEY=gemini-key:latest
```

Create `token-secret` and `gemini-key` in Secret Manager and grant the Cloud Run service account `roles/secretmanager.secretAccessor` before deploying.

> **Multi-instance note:** Chat memory and scam flags are process-local. For multi-instance deployments, move sessions to Redis. SQLite is local to each container — use Postgres for shared state.

---

## Production Roadmap

| Current (demo) | Production swap |
|---|---|
| SQLite (`data/bank.db`) | Postgres + pgvector |
| In-process session memory | Redis |
| Demo transaction endpoint (`POST /sim/transactions`) | Pub/Sub event stream |
| Keyword policy search | BM25 + vector search (pgvector) |
| Gemini only | Gemini primary → Groq failover |
| No cooling-off release job | Background scheduler (Cloud Tasks) |

---

## Team

| Name | Role | Owned |
|---|---|---|
| **Ayush** | Backend lead | Engine, orchestrator, all agents, all tools, fraud rules, trace, DB, seed |
| **Ayushi** | Frontend + auth | React UI, FastAPI routes, auth, input guard |
| **Teammate 3** | ML + data + deploy | IsolationForest fraud model, policy docs, eval, Dockerfile |
