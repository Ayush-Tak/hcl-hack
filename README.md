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
uv run uvicorn app.api.main:app --reload
uv run streamlit run ui/app.py
```

## Evaluation
<Golden-set size, hit rate @5, faithfulness, p95 latency.>

## Limitations and next steps
- ...

## Team
| Name | Owned |
| --- | --- |
