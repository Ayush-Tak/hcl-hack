"""Verifier (D-14). Code checks every amount; Llama on Groq (a second provider) checks policy claims."""

import json
import logging
import re

import httpx

from app.config import settings
from app.formatting import format_inr
from app.prompts import VERIFIER_CLAIMS_SYSTEM
from app.trace import Trace

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Matches 2,500 / 1,48,250 / ₹30,000 / Rs. 400 / 12.5. Indian grouping is just commas in other places.
_NUMBER = re.compile(r"(?:₹|rs\.?)?\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
SMALL_NUMBER = 10  # days, counts and "T+1" are fine; we check amounts and references
SAFE_FIELDS = (
    "amount",
    "balance",
    "total_due",
    "value",
    "status",
    "state",
    "utr",
    "reversal_due_by",
    "compensation_due",
    "complaint_id",
    "dispute_id",
    "band",
    "score",
)


def _normalise(raw: str) -> str:
    number = raw.replace(",", "").rstrip(".")
    return number.removesuffix(".0")


def numbers_in(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(numbers_in(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(numbers_in(v) for v in value))
    if isinstance(value, bool) or value is None:
        return set()
    return {_normalise(m) for m in _NUMBER.findall(str(value))}


def unknown_amounts(answer: str, calls: list[dict], message: str) -> list[str]:
    """Numbers in the reply that no tool returned and the customer didn't say."""
    known = numbers_in([c["result"] for c in calls]) | numbers_in(message)
    found = numbers_in(answer)
    return sorted(n for n in found if float(n) >= SMALL_NUMBER and n not in known)


def template_answer(calls: list[dict]) -> str:
    """Used when the model's wording fails the check twice: plain facts straight from the tools."""
    lines = []
    for call in calls:
        facts = [
            f"{k.replace('_', ' ')}: {format_inr(v) if k in ('amount', 'balance', 'total_due', 'compensation_due') else v}"
            for k, v in call["result"].items()
            if k in SAFE_FIELDS and v is not None
        ]
        if facts:
            lines.append(f"{call['name'].replace('_', ' ')}: " + ", ".join(facts))
    if not lines:
        return "I couldn't put together a reliable answer. Please try asking in a different way."
    return "Here are the exact details from our systems.\n" + "\n".join(lines)


def check_claims(answer: str, calls: list[dict], trace: Trace) -> list[str] | None:
    """Problems found, [] if fine, None if skipped. Only runs when the reply drew on policy text."""
    policy = [
        h
        for c in calls
        if c["name"] == "search_policy"
        for h in c["result"].get("results", [])
    ]
    if not policy or not settings.groq_api_key:
        return None
    excerpt = "\n\n".join(f"[{h['doc']} / {h['section']}]\n{h['text']}" for h in policy)
    body = {
        "model": settings.groq_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": VERIFIER_CLAIMS_SYSTEM},
            {
                "role": "user",
                "content": f"Policy text:\n<<<\n{excerpt}\n>>>\n\nReply:\n<<<\n{answer}\n>>>",
            },
        ],
    }
    try:
        with trace.span("llm", "verifier_claims", model=settings.groq_model) as out:
            response = httpx.post(
                GROQ_URL,
                json=body,
                timeout=settings.llm_timeout_ms / 1000,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
            out["tokens_in"] = payload["usage"]["prompt_tokens"]
            out["tokens_out"] = payload["usage"]["completion_tokens"]
            verdict = json.loads(payload["choices"][0]["message"]["content"])
            out["problems"] = verdict.get("problems", [])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("verifier claims check skipped: %s", str(exc)[:200])
        return None
    return (
        []
        if verdict.get("ok")
        else list(verdict.get("problems") or ["unsupported claim"])
    )
