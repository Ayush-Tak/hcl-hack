"""Bounded agent loop: the model picks tools, our gate runs them, at most 6 model turns."""

from concurrent.futures import ThreadPoolExecutor

from google.genai import types

from app.agents.llm import GeminiSession
from app.config import settings
from app.prompts import ORCHESTRATOR_SYSTEM
from app.tools import registry
from app.tools.registry import Ctx

MEMORY_TURNS = 4


def _memory_contents(memory: list[dict]) -> list[types.Content]:
    """Past turns as plain text only; old tool results aren't replayed, they could be stale."""
    contents = []
    for turn in memory[-MEMORY_TURNS:]:
        contents.append(
            types.Content(role="user", parts=[types.Part.from_text(text=turn["user"])])
        )
        contents.append(
            types.Content(
                role="model", parts=[types.Part.from_text(text=turn["assistant"])]
            )
        )
    return contents


def _run_calls(ctx: Ctx, calls: list[types.FunctionCall]) -> list[types.Part]:
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda c: registry.execute(ctx, c.name, dict(c.args or {})), calls)
        )
    return [
        types.Part.from_function_response(name=c.name, response=r)
        for c, r in zip(calls, results)
    ]


def run(ctx: Ctx, message: str, memory: list[dict]) -> str | None:
    """The reply text, or None if the model still wanted tools after the last turn."""
    session = GeminiSession(
        ORCHESTRATOR_SYSTEM,
        registry.declarations(),
        ctx.trace,
        _memory_contents(memory),
    )
    parts = [types.Part.from_text(text=message)]
    for _ in range(settings.max_agent_turns):
        response = session.send(parts)
        calls = response.function_calls or []
        if not calls:
            return response.text or ""
        parts = _run_calls(ctx, calls)
    return None
