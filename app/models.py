"""Shapes that cross the API boundary (documented in docs/API.md and docs/ENGINE.md)."""

from typing import Literal

from pydantic import BaseModel, Field

AnswerType = Literal[
    "retrieved_fact",
    "calculated",
    "action_pending",
    "action_completed",
    "clarification_needed",
    "not_found",
    "refused",
    "escalated",
]


class Citation(BaseModel):
    doc: str
    section: str
    version: str
    effective_date: str


class PendingAction(BaseModel):
    action_id: str
    type: str
    summary: str
    requires_otp: bool = True
    expires_at: str
    # Only filled when DEMO_MODE=1, because the demo has no SMS gateway.
    demo_otp: str | None = None


class Warning(BaseModel):
    source: Literal["scam_guard", "pre_payment", "verifier", "fallback"]
    level: Literal["info", "high"]
    message: str


class ToolInvoked(BaseModel):
    name: str
    ok: bool
    ms: int


class ChatResponse(BaseModel):
    trace_id: str
    answer: str
    answer_type: AnswerType
    data: dict | None = None
    citations: list[Citation] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    warnings: list[Warning] = Field(default_factory=list)
    tools_invoked: list[ToolInvoked] = Field(default_factory=list)
    trace_url: str
    latency_ms: int
