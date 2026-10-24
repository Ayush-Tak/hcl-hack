# Notes for judges

Our study sheet. Read the pitch and the request walk-through first, then the section for the part you're asked about. Every section ends with a likely judge question and our answer. Decision numbers (D-xx) point to `docs/DECISIONS.md`.

## The pitch in four lines

- One chat assistant for a retail bank customer: balances, bills, failed payments, lost cards, unrecognised transactions, scam calls, bank policy.
- The LLM drives the conversation and picks the tools. Code guards everything that matters: who the customer is, what each tool may do, every rupee figure, and every action (which needs an OTP).
- It stops scams before the money leaves (Scam Guard + pre-payment check), and scores fraud after it happens (rules + an ML model + an LLM analyst that can only make us more careful).
- Every step of every request is traced, and the audit log is hash-chained, so nobody can quietly edit it.

## Components

| Part | File(s) | Owner |
| --- | --- | --- |
| Engine interface (what the routes call) | `app/engine.py`, `app/models.py` | Ayush |
| Orchestrator agent loop + Gemini client | `app/agents/orchestrator.py`, `app/agents/llm.py`, `app/prompts.py` | Ayush |
| Scam Guard, Fraud Analyst, Verifier | `app/agents/scam_guard.py`, `fraud_analyst.py`, `verifier.py` | Ayush |
| Rules fallback router | `app/agents/fallback.py` | Ayush |
| Tool registry + policy gate | `app/tools/registry.py` | Ayush |
| Tools | `app/tools/banking.py`, `payments.py`, `protection.py`, `policy.py`, `calc.py` | Ayush |
| Pending actions + OTP | `app/actions.py` | Ayush |
| Fraud rules + score engine | `app/fraud/rules.py`, `app/fraud/engine.py` | Ayush |
| Fraud ML model (IsolationForest) | `app/fraud/ml.py` | Teammate 3 |
| Trace + hash-chained audit | `app/trace.py` | Ayush |
| Sessions, config, database, seed data | `app/sessions.py`, `app/config.py`, `app/db.py`, `app/seed.py` | Ayush |
| Routes, login + lockout, input guard | `app/main.py`, `app/auth.py`, `app/guard.py` | Ayushi (auth started by Ayush) |
| React chat UI | `web/` | Ayushi |
| Policies, evaluation, Docker | `data/policies/*.md`, `eval/`, `Dockerfile` | Teammate 3 |
| Stores | SQLite (`data/bank.db`): customers, accounts, transactions, cards, loans, bills, payees, complaints, alerts, cases, pending_actions, traces, audit | |
| External calls | Gemini (agents), Groq `gpt-oss-120b` (Verifier claim check only) | |

## One request, end to end

"The bank officer on the phone says send Rs 30,000 to fix-kyc@okicici or my account gets frozen."

1. **Route** (`main.py`): reads the customer from the login token, runs the input guard (length, injection phrases, masks card numbers), calls `engine.handle_chat`.
2. **Engine** (`engine.py`): starts a trace. If the message names another customer's ID, it refuses right here.
3. **Scam Guard**: the keyword check runs first (bank impersonation + urgency + KYC = high) and raises the session's scam flag. The Gemini classifier starts in parallel.
4. **Orchestrator**: Gemini gets the system prompt and the tool list, and asks to call `transfer_money(payee, amount)`.
5. **Policy gate** (`registry.py`): strips any customer ID the model sent, checks the arguments against the tool's schema, records allow/deny.
6. **Tool**: `transfer_money` checks the balance, then the pre-payment check: first-time payee + scam flag = **hold**. No pending action; a case goes to a person.
7. **Model writes the reply**; the **Verifier** checks every number in it against the tool results.
8. **Engine** sets `answer_type` from what actually happened (`escalated`), adds warnings, saves the trace, appends hash-chained audit rows, and returns a `ChatResponse` with a link to the trace.

If Gemini is down or out of quota, step 4 and 7 are done by the rules fallback instead, with the same tools and the same gate.

## Engine interface  (app/engine.py, app/models.py, written by Ayush)

What it does: the six functions the web routes call: `handle_chat`, `confirm_action`, `ingest_transaction`, `get_trace`, `list_alerts`, `metrics`, plus `get_summary`. The routes contain no banking logic.
How it works:
- `handle_chat`: trace → privacy check → Scam Guard (regex now, LLM in parallel) → orchestrator, or the fallback if the LLM fails → Verifier → answer type, warnings, citations, pending action → save trace.
- `answer_type` is decided by code from the tool results, in priority order: a pending action → `action_pending`; something escalated → `escalated`; a policy hit → `retrieved_fact`; `calculate` ran → `calculated`; only errors → `not_found`; no tools → `clarification_needed`.
- `confirm_action` checks the OTP and runs the action; a transfer the confirm-time re-check holds comes back `escalated`.
- `ingest_transaction` is the simulated transaction stream: store, score, open an alert for medium or high.
Why this design: one small Python interface between the AI engine and the web app let three people build in parallel (D-22).
Considered instead: routes calling tools directly: banking logic spread into the web layer; the engine as its own HTTP service: an extra hop and deploy for no gain.
Trade-off / limitation: any change to the interface has to go through `docs/ENGINE.md`.
Likely judge question: How do you decide what kind of answer it is? Doesn't the model decide?
Answer: No. The model writes the words; code looks at which tools ran and what they returned. If a pending action exists it's `action_pending`, if a case was escalated it's `escalated`. The model can't claim something was done.

## Orchestrator agent loop  (app/agents/orchestrator.py, app/agents/llm.py, app/prompts.py, written by Ayush)

What it does: lets Gemini choose which tools to call, and in which order, to answer the customer.
How it works:
- We send the system prompt (rules from CLAUDE.md), the tool declarations from the registry, the last 4 turns as plain text, and the message. Temperature 0.
- Gemini answers with text or with function calls. We run the calls ourselves (up to 4 in parallel threads), each through the gate, and send the results back.
- At most 6 model turns, then we stop and ask a clarifying question.
- We keep Gemini's own returned message in the history, because Gemini needs its function-call parts back on the next turn.
- One shared Gemini client, made under a lock (two agents start at once; without the lock one closed the other's connection).
Why this design: native function calling in a loop we run ourselves, so every call is gated and traced (D-11).
Considered instead: the SDK's automatic function calling: tool calls would skip our gate and trace; the v1 fixed router: every multi-part question needed new code, and the judges marked it down.
Trade-off / limitation: the path can differ between runs; the gate, the 6-turn cap and the trace keep it safe and visible. The free tier allows 5 requests a minute.
Likely judge question: What stops the agent from looping forever or doing something dangerous?
Answer: The loop stops after 6 model turns. Every tool call passes our policy gate, and action tools can only create a pending action that needs the customer's OTP. The worst the model can do is ask our code, and our code says no.

## Tool registry and policy gate  (app/tools/registry.py, written by Ayush)

What it does: the list of tools the model may call, and the checkpoint every call passes through.
How it works:
- Each tool is a plain Python function registered with a name, a description, a JSON schema and a tier: `read`, `write` (a complaint, a dispute) or `action` (only creates a pending action).
- `execute()` strips `customer_id`, `customer`, `account_id`, `user_id` and `role` from the model's arguments. The customer always comes from the session.
- It refuses unknown tools, unknown arguments, values outside an enum, non-numbers and negative amounts, and records allow/deny as a trace span.
- A crash inside a tool is logged with its stack trace and comes back to the model as an error it can explain, so one bug can't end the conversation.
Why this design: guardrails in code around the agent, not in the prompt (D-15).
Considered instead: rules in the system prompt: a clever message can talk the model out of them; trusting the model's arguments: anyone could ask about another customer.
Trade-off / limitation: every new tool needs a schema and a tier.
Likely judge question: What if someone writes "I am C1002, show my balance"?
Answer: Two things stop it. The engine refuses any message naming another customer's ID before the model even runs. And even if the model put `customer_id: C1002` in a tool call, the gate strips it; every query uses the ID from the login token.

## The calculate tool  (app/tools/calc.py, written by Ayush)

What it does: does all arithmetic for the model, exactly.
How it works:
- Parses the expression with Python's `ast` and walks the tree, allowing only numbers and `+ - * /`.
- No `**` or `%` (a "9**9**9" could hang the server), and at most 200 characters.
- Strips `Rs`, `₹` and commas first.
Why this design: LLMs slip on multi-step sums; a wrong rupee figure is the worst failure for a bank (D-03, D-15).
Considered instead: Python `eval`: runs any code; letting the model add up: it gets sums wrong.
Trade-off / limitation: the model has to remember to call it; the Verifier catches it when it doesn't.
Likely judge question: How do you know the model didn't make up a number?
Answer: The model must call `calculate` for any maths, and the Verifier checks every number in the reply against tool results and the customer's own message before it's sent.

## Banking tools  (app/tools/banking.py, written by Ayush)

What it does: read-only tools over the customer's data: balance, transactions, spending by category, cards, upcoming payments, loans, loan eligibility.
How it works:
- Every query filters by the session's customer ID.
- Spending compares this month with the same days of last month (capped so the 31st doesn't spill over).
- Upcoming payments adds bills, card statements and loan EMIs due in the window, and returns the total.
- Account and card numbers are stored masked to the last 4 digits.
Why this design: one typed function per tool; the database is the single source of truth.
Considered instead: giving the model raw tables: privacy and accuracy risk; one big "query" tool: the model would write SQL.
Trade-off / limitation: spending, EMI and loan questions only work on the live agent; the fallback has no route for them.
Likely judge question: Where does the data come from?
Answer: Synthetic data in SQLite, about 90 days per customer, dated relative to today so dues always look current. The tools are the only way the model sees it.

## Debited but not received  (app/tools/payments.py, written by Ayush)

What it does: finds a failed payment, gives the UTR and the RBI deadline, raises a complaint, and escalates with compensation if the deadline has passed.
How it works:
- `check_transaction_status` finds the payment and returns its state and UTR.
- RBI turnaround times in code: UPI and IMPS by T+1, ATM by T+5 (T = transaction date), then Rs 100 per day of delay.
- `raise_complaint` opens a ticket inside the deadline; past it, it opens a case for a person with the compensation worked out. Raising it twice returns the same ticket.
Why this design: dates and compensation must be exact, so they're code, not model text (D-19).
Considered instead: the LLM reads the deadline from policy text: it could get the date or the amount wrong; send every case to a person: most reverse on their own within T+1.
Trade-off / limitation: the deadline table is hard-coded per channel.
Likely judge question: Why not just tell the customer to wait?
Answer: Because RBI says exactly how long they should wait and what they're owed after that. We give the UTR, the date, and the rupee amount, and escalate automatically once it's late.

## Pending actions and OTP  (app/actions.py, app/tools/protection.py, written by Ayush)

What it does: makes sure nothing changes (card block, transfer) until the customer confirms with a one-time code.
How it works:
- An action tool only creates a pending action with a random 6-digit OTP. We store an HMAC of it, never the OTP itself. It expires in 5 minutes. In demo mode the OTP is shown in the UI.
- On confirm, the attempt is counted in one database statement before the code is compared, so parallel guesses can't beat the limit; after 3 wrong codes it locks.
- The action moves from pending to executing in one statement, so a double click can't run it twice; confirming again returns the stored result.
- Each executor re-checks its own conditions at confirm time (card still active, balance still there, scam flag now raised).
Why this design: the model proposes, the customer approves (D-08, D-15).
Considered instead: act immediately: one misunderstood sentence could block the wrong card; a human for every action: too slow for self-service.
Trade-off / limitation: a scheduled (cooling-off) transfer can't be cancelled from chat yet.
Likely judge question: Can someone brute-force the OTP?
Answer: No. Three tries, counted atomically before the check, then the action locks. It expires in 5 minutes, and an unknown action ID gets the same answer as someone else's, so IDs can't be probed.

## Transfers and the pre-payment check  (app/tools/payments.py, app/sessions.py, written by Ayush)

What it does: checks every transfer for scam signs before any money moves.
How it works:
- Refuses an amount above the balance straight away.
- Signals: first payment to this payee, amount over 3x the customer's usual UPI payment (90-day median), scam flag in the session.
- Decision: scam flag + any other signal = **hold** (no payment, case to a person); first-time payee and Rs 25,000 or more = **cooling-off** (released 30 minutes after confirm); any one signal = **step-up** (shown as a warning); none = normal OTP.
- At confirm we run the check again with the scam flag as it is now, because Scam Guard's model verdict may arrive after the transfer was proposed.
- The debit is one statement, `UPDATE ... WHERE balance >= amount`, so two confirms at once can't overdraw.
Why this design: a UPI payment can't be recalled, so prevention must happen before it goes (D-18).
Considered instead: detect after payment: the money is gone; block every new payee: stops normal life.
Trade-off / limitation: some genuine large first-time payments wait 30 minutes. Sessions are kept in memory, so a restart clears the scam flag (Redis in production).
Likely judge question: The customer typed it themselves. Why would you hold it?
Answer: That's exactly how authorised push payment scams work: the victim sends the money. When the conversation shows coaching signs and the payee is new, we hold it and a person calls them. Normal payments to known payees go through with just the OTP.

## Scam Guard  (app/agents/scam_guard.py, written by Ayush)

What it does: reads every message for social engineering and raises a session scam flag.
How it works:
- A keyword check runs first, before any tool: bank or police impersonation (including "the bank called"), urgency or threats, requests for an OTP/PIN/CVV, remote-access apps, KYC stories. Two patterns, or any "coaching" sign, means high.
- A Gemini classifier runs in parallel with the orchestrator and returns risk, patterns and a reason as JSON.
- The flag only goes up within a session; a calm follow-up doesn't clear it.
- If the model errors or times out (5 seconds), it's skipped, recorded in the trace, and the keyword result stands.
Why this design: the scam setup comes before the payment request, so we listen to every message (D-17).
Considered instead: keywords only: misses paraphrases; only check payment messages: misses "my account will be frozen" said earlier.
Trade-off / limitation: one extra model call per message, run in parallel so the customer doesn't wait.
Likely judge question: What if the model is down?
Answer: The keyword check still runs on every message, and the pre-payment check uses whatever flag is set. The model makes detection smarter; it isn't the only line.

## Fraud scoring: rules, ML model, analyst  (app/fraud/rules.py, app/fraud/engine.py, app/agents/fraud_analyst.py by Ayush; app/fraud/ml.py by teammate 3)

What it does: scores a transaction from 0 to 1 with plain-language reasons, and opens an alert for medium or high.
How it works:
- Rules, each with a reason: amount over 3x the 90-day median for that category +0.35, first time at the merchant +0.20, between 00:00 and 05:00 +0.15, a city away from home +0.20, 3+ debits in 10 minutes +0.10.
- ML (teammate 3): five features (amount vs median, hour, new merchant, new city, recent debits). An IsolationForest is fitted on the customer's own history when there are 20+ debits; with less, a robust z-score. If its risk is 0.7 or more we add +0.15 and its own "Model:" reasons.
- Bands: 0.6 and up is high (alert + offer a card block), 0.3 to 0.6 medium, below 0.3 low.
- For medium and high, the Gemini Fraud Analyst gives its own band and a two-sentence explanation. Final band = the higher of the two: it can raise, never lower.
Why this design: rules give the reasons, the model catches the unusual, the AI can only make us more careful (D-16).
Considered instead: a supervised model: we have no labelled fraud data; the LLM decides: a fraudster could write the merchant name to talk it down.
Trade-off / limitation: weights are start values, not tuned on real outcomes.
Likely judge question: Why can the LLM only raise the band?
Answer: The merchant name is written by outsiders, so it's untrusted input to the model. If the model could lower a score, a fraudster could write "verified safe merchant" and get waved through. Letting it only raise means its worst mistake is a false alarm.

## Verifier  (app/agents/verifier.py, written by Ayush)

What it does: checks the reply before the customer sees it.
How it works:
- Code check: every number of 10 or more in the reply must appear in a tool result or the customer's message. It understands Indian grouping (1,48,250), ₹ and "Rs.".
- If a number fails, the model gets one repair turn; if it still fails, we send a plain list of the tool figures instead.
- When the reply used policy text, a second model from a second provider (`gpt-oss-120b` on Groq) checks each claim against the cited sections. Problems become a warning.
Why this design: a different model from a different company checks the answer, and code checks the numbers (D-14, D-24).
Considered instead: Gemini checks itself: same blind spots; code only: can't catch "this fee is waived" when the policy says it isn't.
Trade-off / limitation: about 1 second more on policy answers, and a second API key.
Likely judge question: What happens if the model invents a number?
Answer: The reply never reaches the customer as written. We ask the model to fix it once, and if it still can't, the customer gets the exact figures from our tools instead.

## Policy search  (app/tools/policy.py, written by Ayush; documents by teammate 3)

What it does: answers bank policy questions only from our policy documents, with a citation.
How it works:
- Splits each markdown file into sections on `## ` headings; reads doc, version and effective date from the header.
- Matches question words to section words, with simple word-form matching ("delayed" finds "delay").
- A section must share at least 2 different words with the question; among those, words in the section title count twice.
- Returns up to 3 sections with document, section, version and date; nothing matching gives `not_found`.
Why this design: if the policy doesn't say it, the assistant doesn't say it (D-06).
Considered instead: pasting all policies into the prompt: no reliable citation; fine-tuning: redo it every time a policy changes.
Trade-off / limitation: keyword matching today; the planned BM25 + vector search (D-06) isn't built.
Likely judge question: What if I ask something the policies don't cover?
Answer: It says the policy doesn't cover it. Try "What is the fee for a duplicate debit card?": we have no fee document, so it answers `not_found` instead of guessing.

## Rules fallback  (app/agents/fallback.py, written by Ayush)

What it does: keeps the assistant working when the LLM is off, out of quota or failing.
How it works:
- Keyword patterns pick an intent: not received, lost card, fraud, scam report, affordability, transfer, policy, balance.
- Each intent runs fixed tools through the same gate and fills a template with their results. Affordability still does its maths with `calculate`.
- Anything else gets a clarifying question. Every reply shows a "basic mode" warning.
Why this design: one rate-limit error must not end the demo or the service (D-12).
Considered instead: no fallback: the assistant dies with the API; a second LLM: same quota risk.
Trade-off / limitation: plainer answers, and it covers only the main scenarios. Our 20-question eval scored 25% in this mode.
Likely judge question: Are these answers hardcoded?
Answer: The wording is a template; the facts aren't. The same question for three customers gives three different balances, bills and results, because every figure comes from the tools.

## Trace and hash-chained audit  (app/trace.py, written by Ayush)

What it does: records every step of every request, in a log nobody can quietly edit.
How it works:
- Each request gets a trace ID like BANK-20261024-000021.
- Spans: guard checks, each LLM call (model, tokens, ms), each gate decision, each tool call (arguments, result, ms), the Verifier result, actions.
- On finish, each span is written as an audit row whose hash is SHA-256 of the previous row's hash plus this row. Written under a database write lock, so two requests can't chain onto the same row.
- `verify_chain` recomputes the whole chain; editing or deleting any row breaks it. The UI shows "Chain: VERIFIED OK".
Why this design: with an agent choosing the steps, we need to show why each step happened (D-20).
Considered instead: one audit row per request: too little to explain an agent; a hosted tracing tool: setup time, and customer data leaves our system.
Trade-off / limitation: checking the chain reads every row; fine for a demo, needs checkpoints at scale.
Likely judge question: How would an auditor know the log wasn't changed?
Answer: Each row's hash includes the previous row's hash. Change one character anywhere and every hash after it stops matching; `verify_chain` points to the first broken row. We tested exactly that.

## Login, tokens and the input guard  (app/auth.py, app/config.py by Ayush and Ayushi; app/guard.py by Ayushi)

What it does: decides who you are, and filters what you type.
How it works:
- PINs are stored as PBKDF2 hashes (100,000 rounds, customer ID as salt).
- After login you get a signed token: customer ID, role and expiry, signed with HMAC-SHA256. Every route reads the customer from it.
- The signing secret comes from `TOKEN_SECRET`; with none, demo mode makes a random one, and production (`DEMO_MODE=0`) refuses to start.
- Ayushi's lockout: 5 failed PINs lock that ID for 15 minutes, stored in the database; every failure gets the same message, so nobody can learn which IDs exist.
- Ayushi's input guard: messages over 1,000 characters and prompt-injection phrases are refused; card numbers are masked before the engine sees them.
Why this design: identity comes from authentication, never from what you type (D-07, D-23).
Considered instead: a default secret: anyone reading the repo could forge tokens (we removed it after a security review); in-memory lockout: resets on restart.
Trade-off / limitation: demo accounts have known PINs, so they're only created when `DEMO_MODE=1`.
Likely judge question: What did the security reviews find, and what did you fix?
Answer: A default token secret, an OTP attempt race, a possible overdraft, transfers not re-checking the scam flag at confirm, and demo accounts created everywhere. Each is fixed, and most have a test.

## Data and seed  (app/seed.py, app/db.py, written by Ayush)

What it does: creates a believable bank for the demo.
How it works:
- 3 customers and an officer, about 90 days of everyday spending each, cards, loans, bills, payees and products.
- Planted cases: a Rs 48,999 card payment at 02:14 in Dubai; a Rs 2,500 UPI to Ravi failed yesterday (inside T+1); a Rs 1,200 UPI to Sharma Kirana failed 5 days ago (past T+1); a failed ATM withdrawal.
- Everything is dated relative to today, so dues and deadlines always look current. Re-seeding never touches traces or the audit chain.
Why this design: a fixed random seed gives the same data every time; relative dates keep the demo fresh.
Considered instead: real data: not allowed; hand-written CSV: dates go stale by the next day.
Trade-off / limitation: account balances aren't reconciled with the transaction history.
Likely judge question: Is any of this real customer data?
Answer: No. All of it is generated, and there's no real payment, SMS or core-banking connection.

## Tests  (tests/, Ayush; tests/test_ml.py teammate 3; tests/test_api_endpoints.py Ayushi)

What it does: 19 automated tests that run in a few seconds with no internet.
How it works:
- A fake LLM replaces every model call, and each test gets a freshly seeded temporary database.
- They cover: the gate stripping a customer ID, 3 wrong OTPs locking an action, RBI compensation, the Dubai payment scoring high, the analyst not lowering a band, scam holds (including a late verdict held at confirm), no overdraft, the Verifier reading Indian numbers, policy citations, the affordability and OTP-scam chips, transfer amounts and balance checks, the ML model, and the API flow.
Why this design: the risky rules are the ones we test.
Considered instead: only live testing: slow, uses quota, not repeatable.
Trade-off / limitation: no automated test of the live Gemini path; we checked it by hand.
Likely judge question: How do you test an LLM system?
Answer: We test everything around the model: the gate, the maths, the OTP, the fraud rules, the holds, the Verifier, with a fake model that returns set answers. The live model we check by hand and through the trace.

## Teammates' parts, in one line each

- **Ayushi, web app** (`web/`): React + TypeScript. Login, 5 quick-scenario chips, answer-type badges, warnings, the OTP dialog, the trace viewer, and alerts.
- **Ayushi, routes** (`app/main.py`): FastAPI routes over the engine, role checks for officer routes, input guard before chat.
- **Teammate 3, ML** (`app/fraud/ml.py`): unsupervised IsolationForest per customer, with plain-English `explain()` reasons.
- **Teammate 3, policies and eval** (`data/policies/`, `eval/`): two policy documents; a 20-question golden set and a runner that logs in, asks, and measures tools, answer types and latency.
- **Teammate 3, deploy** (`Dockerfile`): container for Cloud Run.

## Honest limitations (say them before a judge finds them)

- The live agent runs on Gemini's free tier: 5 requests a minute, about 20 a day. Over that, it falls back to rules.
- Sessions (memory, scam flag) live in server memory; production would use Redis.
- Policy search is keyword matching, not the planned BM25 + vectors.
- Cooling-off transfers are scheduled but there's no release job or cancel yet.
- The 20-question eval scored 25% in fallback mode; we haven't run it with the live agent.
- The production design (Postgres + pgvector, Redis, Pub/Sub, Gemini → Groq failover) is a design, not built.

## Numbers to remember

- 6 model turns at most; 4 tool calls in parallel; last 4 turns of memory.
- OTP: 6 digits, 5 minutes, 3 tries.
- Fraud: +0.35 / +0.20 / +0.15 / +0.20 / +0.10, +0.15 ML; high at 0.6, medium at 0.3.
- RBI: UPI/IMPS T+1, ATM T+5, Rs 100 a day after.
- Pre-payment: cooling-off at Rs 25,000 for a new payee, 30 minutes.
- 19 tests; 24 logged decisions.
