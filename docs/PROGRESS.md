# Progress

Our live log for the day. Newest entries at the bottom. Written by us, for us and for the judges: what changed, what it means, what's next.

## Story board

Requirement IDs refer to `docs/SRS.md`.

| Story | Requirements | Owner | Status | Notes |
| --- | --- | --- | --- | --- |
| Login, session, privacy refusal | FR-01, FR-15 | Teammate 2 | todo | Security review: add a login lockout in `auth.py` (5 failed PINs → that customer ID locked 15 minutes, counted in a DB table so it works across workers; same message for a wrong PIN, an unknown ID and a locked account). |
| Request understanding (router) | FR-02 | Backend | todo | |
| Balance and transactions | FR-03, FR-04 | Backend | todo | |
| Spending by category | FR-05 | Backend + Data | todo | |
| Card dues, UPI status, loans/EMI | FR-06, FR-07, FR-08 | Backend | todo | |
| Upcoming payments and funds after bills | FR-09 | Backend | todo | |
| Lost card: confirm + OTP + block | FR-11 | Backend + Frontend | todo | |
| Fraud score, dispute, review queue | FR-12, FR-16 | Backend + Frontend | todo | |
| Policy Q&A with citations, not_found | FR-13, FR-19 | Backend + Data | todo | |
| Audit trail | FR-17 | Backend + Frontend | todo | |
| Synthetic data + policy documents | Section 6 | Data | todo | |
| 30-question test set + evaluation | Section 7 | Data | todo | |
| Customer chat UI | Section 5 | Frontend | todo | |
| Operations dashboard | FR-16, FR-17 | Frontend | todo | |
| Savings observations | FR-10 | Backend | todo | Should |
| Live policy updates | FR-18 | Backend | todo | Should |
| Pre-approved offers | FR-14 | Backend | todo | Stretch |

Status: `todo`, `doing`, `done`, `blocked`, `cut`.

## Log

One line per working slice: `HH:MM · Name · what happened, in plain words. Next: what's next.`

- 09:50 · Ayush · Repo, Python environment and doc templates set up.
- 10:55 · Team · Chose the Daily Banking Assistant over Library and Hospital (see D-01). Next: design, SRS and the slides.
- 11:10 · Ayush · Design, SRS and decision log written; code skeleton created. Next: design presentation, then build from 12:00.
- 14:00 · Ayush · Design v2 after the judges' review: LLM agent loop, four agents, scam prevention, debited-but-not-received, span traces (D-11 to D-22). Engine interface in docs/ENGINE.md. Reviewed the first app/ files: kept all, fixed the seed crash, plain-text PINs, the hash-chain race and a month-end date bug. Next: llm.py and the Orchestrator loop.
- 14:31 · Ayush · Security review fix: the token secret no longer has a default anyone could read in the repo. Without TOKEN_SECRET the demo uses a random one per run, and with DEMO_MODE=0 the app refuses to start. Login lockout handed to teammate 2 (story board). Next: llm.py and the Orchestrator loop.
- 15:04 · Assistant · Added an isolated golden-set runner that logs in and records engine tools, answer types and latencies; fallback evaluation exposed answer and prompt-injection gaps. Next: review failures with the engine owner.
- 15:04 · Assistant · Added the demo ASGI entrypoint, Docker image and Cloud Run instructions; documented process-local sessions and SQLite as scale-out limitations. Next: move shared state to managed services before production.
- 15:04 · Assistant · Decision: keep IsolationForest unsupervised rather than train a supervised classifier because this demo has no labelled fraud outcomes to train or validate against.
- Ayush · Engine built: actions with OTP, fraud rules + engine, payment/card/policy tools, Gemini orchestrator loop, rules fallback, engine.py. Then the agents: Scam Guard (regex + Gemini, in parallel), Fraud Analyst (band only rises), Verifier (amount check, one repair turn, template; Groq claim check on policy answers). Fixed three review findings: OTP attempt race, overdraft, transfers not re-checking the scam flag at confirm. 8 pytest tests pass with a fake LLM. Gemini free tier (5 requests a minute) still forces fallback often. Next: policy docs from teammate 3, then a live run with a paid key.

## Blockers

- none