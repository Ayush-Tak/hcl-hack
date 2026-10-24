# <Project name>

<One line: what it does and for whom.>

## Problem
<Who has the problem, what it costs them today.>

## Our approach
<3 to 5 lines. Why this design.>

## Architecture
<Diagram image or Mermaid block. One line per component.>

## User stories implemented
- [x] Story 1: ...
- [ ] Story 2: ...

## How to run
```bash
uv sync
cp .env.example .env   # add your keys
uv run uvicorn app.main:app --reload
```

The demo seeds synthetic banking data on startup. Demo customer `C1001` uses PIN `1234`.

## Evaluation

Run the golden set with:

```bash
uv run python eval/run_eval.py
```

The runner uses a temporary database, logs in for each of the 20 cases, and compares the
exact set of invoked tools and the answer type with the expected values. It prints per-case
results, tool and answer-type accuracy, p50/p95 latency, and the status of the three designated
attack cases. It exits nonzero when any expected result does not match.

## Deploy and scale

The container listens on port `8080`, as required by Cloud Run. For a demo deployment:

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
  --set-env-vars DEMO_MODE=1,USE_LLM=0 \
  --set-secrets TOKEN_SECRET=token-secret:latest
```

Create `token-secret` in Secret Manager and grant the Cloud Run service account permission to
read it before deploying. Keep `TOKEN_SECRET` stable across instances so login tokens remain
valid when Cloud Run routes requests to another instance. The public demo URL still requires
the application's customer login; remove `--allow-unauthenticated` when Cloud Run IAM should
also restrict access.

Cloud Run can add instances to handle independent requests when traffic grows. A stateless
request handler can run on any instance without session affinity. The current demo is not
fully stateless yet: chat memory is process-local and SQLite is local to an instance, so
multi-instance sessions and writes are not shared. Treat this deployment as a demo; use shared
services before relying on scale-out or durable banking state.

Production swaps:

- Replace local SQLite and policy-vector storage with **Postgres + pgvector**.
- Move chat/session flags from process memory to **Redis**.
- Feed transaction events through **Pub/Sub** rather than the demo transaction endpoint.
- Keep **Gemini** as the primary chat model and configure **Groq** as the provider failover.

## Limitations and next steps
- ...

## Team
| Name | Owned |
| --- | --- |
