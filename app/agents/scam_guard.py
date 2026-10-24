"""Scam Guard (D-17): a regex fast check before the agent runs, and a Gemini classifier in parallel."""

import re
from typing import Literal

from pydantic import BaseModel

from app import sessions
from app.agents import llm
from app.prompts import SCAM_GUARD_SYSTEM
from app.trace import Trace

# Each group is one social-engineering pattern. Two at once, or any "coaching" sign, is high risk.
SCAM_PATTERNS = {
    "bank or police impersonation": r"bank (officer|manager|staff|called|rang|phoned)|from the bank|police|cbi|customs|rbi officer",
    "urgency or threats": r"frozen|freeze|blocked today|immediately|urgent|arrest|last chance",
    "asks for an OTP or code": r"(share|tell|read|give|asked for|asking for|wants?).{0,20}(otp|code|pin|cvv)",
    "remote access app": r"anydesk|teamviewer|quick ?support|screen ?shar",
    "KYC update story": r"\bkyc\b",
}
COACHING_SIGNS = {"asks for an OTP or code", "remote access app"}


class ScamVerdict(BaseModel):
    risk: Literal["none", "low", "high"]
    patterns: list[str]
    reason: str


def fast_check(message: str) -> ScamVerdict:
    found = [
        name
        for name, pattern in SCAM_PATTERNS.items()
        if re.search(pattern, message, re.IGNORECASE)
    ]
    risk = (
        "high"
        if len(found) >= 2 or COACHING_SIGNS & set(found)
        else ("low" if found else "none")
    )
    return ScamVerdict(risk=risk, patterns=found, reason="keyword match")


def classify(message: str, trace: Trace) -> ScamVerdict | None:
    """None when the model is off or fails; the regex result still stands."""
    return llm.generate_json(
        "scam_guard",
        SCAM_GUARD_SYSTEM,
        f"Customer message:\n<<<\n{message}\n>>>",
        ScamVerdict,
        trace,
    )


def apply(
    session: dict, verdict: ScamVerdict | None, trace: Trace, source: str
) -> None:
    if verdict is None:
        return
    sessions.raise_scam_risk(session, verdict.risk, verdict.patterns)
    trace.add(
        "guard",
        f"scam_{source}",
        risk=verdict.risk,
        patterns=verdict.patterns,
        session_risk=session["scam_risk"],
    )
