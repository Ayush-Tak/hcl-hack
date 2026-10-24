"""The only place that talks to Gemini. Every call becomes a trace span (model, tokens, ms)."""

import logging

from google import genai
from google.genai import types

from app.config import settings
from app.tools.registry import Tool
from app.trace import Trace

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


def llm_available() -> bool:
    return settings.use_llm and bool(settings.gemini_api_key)


def _declaration(tool: Tool) -> types.FunctionDeclaration:
    # Gemini rejects an object schema with no properties, so argument-free tools send none.
    params = tool.params if tool.params.get("properties") else None
    return types.FunctionDeclaration(
        name=tool.name, description=tool.description, parameters_json_schema=params
    )


class GeminiSession:
    """One conversation turn with tools. Holds the history, including the model's own
    returned Content, because Gemini needs its function-call parts (and thought signatures) back."""

    def __init__(
        self,
        system_prompt: str,
        tools: list[Tool],
        trace: Trace,
        history: list[types.Content],
    ) -> None:
        self.client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=settings.llm_timeout_ms),
        )
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
            tools=[types.Tool(function_declarations=[_declaration(t) for t in tools])],
            # We run the loop ourselves, so every tool call goes through our gate and trace.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        self.trace = trace
        self.history = list(history)

    def send(self, parts: list[types.Part]) -> types.GenerateContentResponse:
        self.history.append(types.Content(role="user", parts=parts))
        with self.trace.span("llm", "orchestrator", model=settings.llm_model) as out:
            response = self.client.models.generate_content(
                model=settings.llm_model, contents=self.history, config=self.config
            )
            usage = response.usage_metadata
            out["tokens_in"] = usage.prompt_token_count if usage else 0
            out["tokens_out"] = usage.candidates_token_count if usage else 0
        if not response.candidates or not response.candidates[0].content:
            raise LLMError(
                f"empty response (finish reason: {response.prompt_feedback})"
            )
        self.history.append(response.candidates[0].content)
        logger.info("gemini turn: %d tool calls", len(response.function_calls or []))
        return response
