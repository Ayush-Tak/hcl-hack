# Decisions

Why we built it this way. Each entry: what we chose, what else we considered and why we didn't pick it, and one line we'd say to a judge.

| ID | Decision | Status |
| --- | --- | --- |
| D-01 | Banking over Library and Hospital | active |
| D-02 | A router workflow, not an autonomous or multi-agent system | active |
| D-03 | The LLM understands and explains; code calculates and acts | active |
| D-04 | Explainable rule-based fraud score | active |
| D-05 | Merchant rules first, LLM only for unknown merchants | active |
| D-06 | Hybrid policy search with a threshold and citations | active |
| D-07 | Identity from the token, never from the message | active |
| D-08 | Confirmation + OTP for actions, human queue for disputes | active |
| D-09 | SQLite + Chroma, all local | active |
| D-10 | Plain LLM SDK, no agent framework | active |

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