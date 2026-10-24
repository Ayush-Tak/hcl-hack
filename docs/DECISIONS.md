# Decisions

Why we built it this way. Each entry: what we chose, what else we considered and why we didn't pick it, and one line we'd say to a judge.

| ID | Decision | Status |
| --- | --- | --- |
| D-01 | Banking over Library and Hospital | active |
| D-02 | A router workflow, not an autonomous or multi-agent system | superseded by D-11 |
| D-03 | The LLM understands and explains; code calculates and acts | superseded by D-15 |
| D-04 | Explainable rule-based fraud score | superseded by D-16 |
| D-05 | Merchant rules first, LLM only for unknown merchants | active |
| D-06 | Hybrid policy search with a threshold and citations | active |
| D-07 | Identity from the token, never from the message | active |
| D-08 | Confirmation + OTP for actions, human queue for disputes | active |
| D-09 | SQLite + Chroma, all local | active |
| D-10 | Plain LLM SDK, no agent framework | active |
| D-11 | LLM agent loop with native function calling | active |
| D-12 | Rules router only as the fallback | active |
| D-13 | Four agents with narrow jobs | active |
| D-14 | Verifier on a second provider, with amounts checked in code | active |
| D-15 | Guardrails live in code, around the agent | active |
| D-16 | Fraud = rules + IsolationForest + an LLM analyst that can only raise | active |
| D-17 | Scam Guard classifier on every message | active |
| D-18 | Pre-payment check: hold, step-up or cooling-off | active |
| D-19 | "Debited but not received" with RBI deadlines in code | active |
| D-20 | Span-level trace with a hash-chained audit | active |
| D-21 | Stateless API on Cloud Run, production stores named but not built | active |
| D-22 | Engine as a Python interface, three owners | active |
| D-23 | Verifier model is gpt-oss-120b on Groq | active |

## D-01: Banking over Library and Hospital

- **When / who:** kickoff, 10:55, whole team
- **Context:** HCL offered three problems; we have about 5 hours and 3 people.
- **Decision:** Daily Banking Assistant.
- **Alternatives considered:**
  - Smart Library: safe and well specified, but its risk is mostly search quality; it doesn't exercise identity, money movement or human oversight.
  - Hospital Patient Services: the most ambitious, but responsible delivery needs real clinical policies and medical-safety review we can't produce in a day; a half-safe medical assistant is worse than none.
- **Trade-off accepted:** less retrieval-heavy than Library, so we add policy RAG and an explainable fraud score to keep real AI depth.
- **Judge line:** "We picked the problem where a wrong answer costs money, because that forces the full discipline of trustworthy enterprise AI."

## D-02: A router workflow, not an autonomous or multi-agent system

- **Context:** each request type needs a known, safe sequence of steps.
- **Decision:** one LLM call classifies the request; code runs a fixed, tested tool sequence per intent.
- **Alternatives considered:**
  - An autonomous tool-calling agent loop: flexible, but its path changes run to run, which is hard to test, audit or approve in a bank.
  - Specialised multi-agents (accounts, cards, fraud...): HCL lists this as maturity level 6; it adds coordination overhead without improving any of our 9 demo flows.
- **Trade-off accepted:** a new request type needs a new route in code. We'd add an agent only where steps genuinely vary.
- **Judge line:** "For money, predictable beats clever: the model chooses the route, our code walks it."

## D-03: The LLM understands and explains; code calculates and acts

- **Decision:** the LLM extracts intent, categorises unknown merchants, explains results and answers from retrieved policy. Balances, sums, dues, fees, eligibility, fraud decisions and actions are all code.
- **Alternatives considered:**
  - Let the LLM compute from raw data in its context: LLMs make arithmetic and reading errors, and HCL's R6 forbids invented amounts.
  - No LLM at all, with buttons and forms: safe, but loses natural-language access, which is the point of the assistant.
- **Trade-off accepted:** more code per intent. We also run a validator that checks every amount in the reply against tool outputs.
- **Judge line:** "The model never touches a number it could get wrong; it only explains numbers our code produced."

## D-04: Explainable rule-based fraud score

- **Decision:** a weighted score from 0 to 1 using amount vs the customer's usual spend, first time at a merchant, unusual hour, a city different from home, and rapid repeats, returned with plain-language reasons.
- **Alternatives considered:**
  - A trained ML model (e.g. isolation forest or gradient boosting): no labelled fraud data, and its reasons are harder to explain to a customer or an auditor in a day's build.
  - Asking the LLM if it looks like fraud: not deterministic, not auditable, and easy to manipulate through the transaction description.
- **Trade-off accepted:** rules miss novel patterns. Next step: train a model on dispute outcomes and keep the rules as explanations.
- **Judge line:** "Every fraud flag comes with reasons a customer and an auditor can read, like 'three times your usual amount, first time at this merchant, at 2 am'."

## D-05: Merchant rules first, LLM only for unknown merchants

- **Decision:** a merchant-to-category table handles known merchants; the LLM categorises an unknown merchant once, into a fixed category list, and the result is cached.
- **Alternatives considered:**
  - The LLM categorises every transaction: slow, costly, and results can change between runs.
  - Rules only: anything unseen ends up as "Other", which makes the insights weaker.
- **Trade-off accepted:** a wrong LLM category stays cached until corrected; the category never changes any amount.
- **Judge line:** "Rules for what we know, the model for the long tail, and the totals are always code."

## D-06: Hybrid policy search with a threshold and citations

- **Decision:** policy documents are chunked by section; keyword (BM25) and vector search are combined; answers cite document, section, version and effective date; below the score threshold we return `not_found`.
- **Alternatives considered:**
  - Paste all policies into the prompt: works for 5 documents but doesn't scale, and makes citations unreliable.
  - Fine-tune a model on the policies: needs training data and time, and has to be redone every time a policy changes.
- **Trade-off accepted:** the threshold is tuned on our test set and could reject some answerable questions.
- **Judge line:** "If the policy doesn't say it, the assistant doesn't say it, and when it does answer, it shows the exact clause."

## D-07: Identity from the token, never from the message

- **Decision:** the customer ID comes only from the login token; tools take it from the session; asking about anyone else is `refused`.
- **Alternatives considered:**
  - Trust the ID mentioned in the message: anyone could type another customer's ID.
  - Let the LLM decide whether a request is allowed: the model can be talked out of rules.
- **Judge line:** "Who you are comes from authentication, not from what you type."

## D-08: Confirmation + OTP for actions, human queue for disputes

- **Decision:** blocking a card and raising a dispute create a pending action that runs only after the customer confirms and enters an OTP; disputes and high-risk cases go to an operations review queue.
- **Alternatives considered:**
  - Act immediately when asked: fast, but one misunderstood sentence could block the wrong card.
  - Route every action to a human: safe but slow, which defeats self-service.
- **Judge line:** "The assistant prepares the action, the customer approves it, and a human reviews anything high-risk."

## D-09: SQLite + Chroma, all local

- **Alternatives considered:**
  - Postgres + pgvector: production-grade, but setup time we don't have; our schema moves over unchanged.
  - FAISS: fast, but we'd hand-build the metadata storage and updates that Chroma gives us, and FR-18 needs live updates.
- **Judge line:** "Zero-setup local stores for the prototype, with a clean path to Postgres and a managed vector database."

## D-10: Plain LLM SDK, no agent framework

- **Alternatives considered:**
  - LangChain or LlamaIndex: fast prototypes, but extra layers we'd have to explain, and APIs that change often.
  - Raw HTTP calls: no structured-output support.
- **Trade-off accepted:** we write our own small router (about 150 lines), which we can explain line by line.
- **Judge line:** "Every step is our code, so we can show the judges exactly what happens."

## D-11: LLM agent loop with native function calling

- **When / where:** design v2 after the judges' review, 14:00, Ayush
- **Context:** judges found the v1 router too rigid: it can't handle "what's due, and can I still send Rs 20,000?" without a new route per combination. Supersedes D-02.
- **Decision:** Gemini Flash native function calling in a loop we run ourselves: at most 6 turns, several tool calls per turn run in parallel, each through the gate.
- **Alternatives considered:**
  - Keep the router workflow: predictable, but every multi-part question needs new code, and the judges marked it down.
  - The SDK's automatic function calling: less code, but tool calls would skip our gate and our trace.
  - A ReAct loop that parses tool calls out of plain text: fragile parsing and more tokens than native function calling.
- **Trade-off accepted:** the path can change between runs. The gate, the turn cap and the trace keep it safe and visible.
- **Evidence:** assumption, verify by tool accuracy on `eval/questions.jsonl`.
- **Judge line:** "The model chooses the steps, but every step goes through our gate and shows up in the trace."

## D-12: Rules router only as the fallback

- **When / where:** design v2, 14:00, Ayush
- **Context:** an agent loop with no LLM is dead, and quota errors are common at hackathons.
- **Decision:** a small keyword router (intent → fixed tools → template) runs when Gemini fails after one retry or `USE_LLM=0`.
- **Alternatives considered:**
  - No fallback: one rate-limit error during the demo and nothing works.
  - Groq as the backup Orchestrator: a second model's tool-calling behaviour needs its own prompt tuning, and Groq is already our independent Verifier. We name it as the production failover (D-21).
- **Trade-off accepted:** fallback replies are plainer and cover only the main intents.
- **Evidence:** assumption, verify by running the eval with `USE_LLM=0`.
- **Judge line:** "If the model goes down, the bank still answers, just more plainly."

## D-13: Four agents with narrow jobs

- **When / where:** design v2, 14:00, Ayush
- **Context:** one prompt that chats, spots scams, judges fraud and checks itself does all four jobs badly, and is easy to talk out of the safety parts.
- **Decision:** Orchestrator (tool calling), Scam Guard (classifier), Fraud Analyst (verdict + explanation), Verifier (checks the reply). Each has one prompt and one output schema.
- **Alternatives considered:**
  - One agent for everything: fewer calls, but the scam check sits inside the prompt it is supposed to guard.
  - One agent per banking area (accounts, cards, loans): they would share the same tools, so it adds hand-offs without adding safety.
- **Trade-off accepted:** more LLM calls per message. Scam Guard runs in parallel; the Analyst and Verifier run only when needed.
- **Evidence:** assumption, verify by p50/p95 latency in `run_eval.py` against NFR-04.
- **Judge line:** "Each agent has one job and one output we can check, and none of them can move money."

## D-14: Verifier on a second provider, with amounts checked in code

- **When / where:** design v2, 14:00, Ayush
- **Context:** a wrong amount or an invented policy claim is the worst failure for a bank (HCL R6).
- **Decision:** code first checks that every amount in the reply appears in a tool result. Then Llama 3.3 70B on Groq checks the claims against the tool results and the cited policy. On failure: one retry with the problems listed, then a safe templated reply.
- **Alternatives considered:**
  - Gemini checks its own answer: the same model tends to repeat the same mistake.
  - Code check only: catches amounts, but not "this fee is waived" when the policy says it isn't.
- **Trade-off accepted:** about 0.5 to 1 s more per checked reply, and a second API key.
- **Evidence:** assumption, verify by the citation-accuracy and validator-pass numbers in the eval.
- **Judge line:** "A different model from a different company checks every answer, and code checks every number."

## D-15: Guardrails live in code, around the agent

- **When / where:** design v2, 14:00, Ayush
- **Context:** giving the model the steering wheel (D-11) makes it the main target for prompt injection. Supersedes D-03, keeping its core idea.
- **Decision:** the customer ID comes only from the token; every tool call passes the policy gate; action tools only create pending actions that need an OTP; all maths goes through the `calculate` tool; `answer_type` is set by code.
- **Alternatives considered:**
  - Put the rules in the system prompt: a clever message can talk the model out of them.
  - Let the model do arithmetic and trust it: LLMs slip on multi-step sums, and R6 forbids invented amounts.
- **Trade-off accepted:** every new tool needs a schema, a tier and a gate test.
- **Evidence:** smoke test, 24 Oct 13:57: the gate strips `customer_id` and refuses unknown arguments. Injection cases go in the eval.
- **Judge line:** "Even if someone tricks the model, the most it can do is ask our code, and our code says no."

## D-16: Fraud = rules + IsolationForest + an LLM analyst that can only raise

- **When / where:** design v2, 14:00, Ayush (rules, analyst), teammate 3 (IsolationForest)
- **Context:** judges said rules alone miss new patterns. Supersedes D-04.
- **Decision:** weighted rules plus an IsolationForest anomaly signal worth +0.15, each with a plain reason. Medium and high go to the Fraud Analyst, which adds an explanation and can raise the band, never lower it.
- **Alternatives considered:**
  - A supervised model (gradient boosting): needs labelled fraud outcomes, which we don't have.
  - Let the LLM decide the band: a fraudster can write the merchant description to talk it down, so it may only push the band upward.
  - Rules only: easy to explain, but blind to patterns nobody wrote a rule for.
- **Trade-off accepted:** an IsolationForest score has no reason of its own, so we show it as "unusual compared with your last 90 days" at a fixed weight.
- **Evidence:** assumption, verify that the planted Dubai payment scores high and ordinary payments stay low.
- **Judge line:** "Rules give the reasons, the model catches the unusual, and the AI can make us more careful but never less."

## D-17: Scam Guard classifier on every message

- **When / where:** design v2, 14:00, Ayush
- **Context:** in authorised push payment scams the customer sends the money themselves, so fraud scoring after the payment is too late.
- **Decision:** a Gemini Flash classifier reads every message for social-engineering patterns (bank or police impersonation, urgency, remote-access apps, requests to share an OTP) and returns a risk level. Code stores it as a session flag. Keyword rules stand in when the LLM is down.
- **Alternatives considered:**
  - Keyword list only: misses paraphrases like "the man from the bank says to read him the code".
  - Check only messages that ask for a payment: the setup ("my account will be frozen") usually comes a few messages earlier.
- **Trade-off accepted:** one extra small call per message, run in parallel so the customer doesn't wait for it.
- **Evidence:** assumption, verify with scam cases in `eval/questions.jsonl`.
- **Judge line:** "We listen for the scam story, not just the payment."

## D-18: Pre-payment check: hold, step-up or cooling-off

- **When / where:** design v2, 14:00, Ayush
- **Context:** a UPI payment can't be recalled once it's sent, so prevention has to happen before it goes.
- **Decision:** every transfer is checked first for a first-time payee, an amount over 3x the usual transfer, and the scam flag. The answer is hold, cooling-off, step-up or a normal OTP (rules in CLAUDE.md).
- **Alternatives considered:**
  - Detect after payment only: the money is already gone.
  - Block every first-time payee: safe, but stops normal life, and customers would move to another app.
- **Trade-off accepted:** some genuine large first-time payments wait 30 minutes.
- **Evidence:** assumption; the thresholds are start values, verify with the demo cases.
- **Judge line:** "We slow down only the payments that look like scams, and only by as much as the risk calls for."

## D-19: "Debited but not received" with RBI deadlines in code

- **When / where:** design v2, 14:00, Ayush
- **Context:** this is one of the most common complaints at Indian banks, and RBI sets fixed deadlines and compensation for it.
- **Decision:** `check_transaction_status` returns state + UTR. Code applies the RBI turnaround times (UPI and IMPS T+1, ATM T+5, Rs 100 a day after that), raises a complaint ticket inside the deadline and escalates past it.
- **Alternatives considered:**
  - Let the LLM read the deadline from policy text: dates and compensation must be exact, so they're code.
  - Send every case to a human: most reverse on their own within T+1, and the queue would flood.
- **Trade-off accepted:** the deadline table is hard-coded per channel and has to change if RBI changes it.
- **Evidence:** the seed has two planted UPI cases, one inside T+1 and one past it, and an ATM case.
- **Judge line:** "The customer gets the RBI deadline and their compensation worked out, not a 'please wait'."

## D-20: Span-level trace with a hash-chained audit

- **When / where:** design v2, 14:00, Ayush
- **Context:** with an agent choosing the steps, one audit row per request (v1) can't show why something happened.
- **Decision:** every guard check, LLM call (tokens, ms), gate decision, tool call and Verifier result is a span. Spans go to an audit table where each row's hash covers the previous row, and an endpoint shows any trace.
- **Alternatives considered:**
  - One audit row per request: too little to debug or defend an agent's path.
  - A hosted tracing tool (LangSmith, an OpenTelemetry collector): setup time, and customer data would leave our system. OpenTelemetry is the production path.
- **Trade-off accepted:** checking the chain reads every row, which is fine for a demo and needs checkpoints at scale.
- **Evidence:** smoke test, 24 Oct 13:57: an edited audit row is caught by `verify_chain`.
- **Judge line:** "For any answer we can show every step the AI took, and prove nobody edited the log afterwards."

## D-21: Stateless API on Cloud Run, production stores named but not built

- **When / where:** design v2, 14:00, Ayush
- **Context:** judges asked how this scales. D-09 (SQLite + Chroma for the demo) stays.
- **Decision:** a Dockerfile and no in-memory state, so the API runs on Cloud Run as is. The production design names Postgres + pgvector, Redis, Pub/Sub for the transaction stream and Gemini → Groq failover, but we don't build them today.
- **Alternatives considered:**
  - Kubernetes: more operations work than three people can justify for one service.
  - SQLite in production: one writer at a time, so it can't scale out.
- **Trade-off accepted:** the scale story is a design, not a running system, and we say so.
- **Evidence:** assumption, verify that the container runs locally before the demo.
- **Judge line:** "Nothing lives in the server's memory, so scaling is a setting, and each local store has a named managed replacement."

## D-22: Engine as a Python interface, three owners

- **When / where:** design v2, 14:00, whole team
- **Context:** in v1 the backend owner had to build everything, which made them the bottleneck.
- **Decision:** `app/engine.py` exposes six functions (docs/ENGINE.md). Teammate 2 builds routes, auth, the input guard and React on top of it. Teammate 3 builds the IsolationForest, the policies and the eval. Ayush builds the engine.
- **Alternatives considered:**
  - Ayush writes the routes too: the frontend waits on the busiest person.
  - The engine as a separate HTTP service: an extra network hop and an extra deploy for no gain at this size.
- **Trade-off accepted:** any change to the interface has to go through ENGINE.md and the team.
- **Evidence:** assumption, verify by teammate 2 running the routes against an engine stub by 14:45.
- **Judge line:** "One small interface between the AI engine and the web app, so three people could build in parallel."

## D-23: Verifier model is gpt-oss-120b on Groq, not Llama 3.3 70B

- **When / where:** story 9, Verifier build, Ayush
- **Context:** Groq returned 404 for `llama-3.3-70b-versatile`; the key's model list has no general Llama chat model left. Changes the model named in D-14, not its idea.
- **Decision:** `openai/gpt-oss-120b` on Groq for the claim check, set by `GROQ_MODEL`.
- **Alternatives considered:**
  - Gemini checks its own claims: the same model tends to repeat the same mistake, which D-14 exists to avoid.
  - `qwen/qwen3.8-27b` on Groq: also independent, but smaller; we took the largest general model on the list.
- **Trade-off accepted:** the judge line in D-14 names Llama, so we say "a second model from a second provider" instead.
- **Evidence:** live check, 24 Oct: it passed a correct policy answer and flagged all three claims in an invented one.
- **Judge line:** "The answer is written by Google's model and checked by a different company's model, so one model's blind spot doesn't pass through."