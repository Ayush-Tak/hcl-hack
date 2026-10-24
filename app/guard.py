"""Input guard: length limits, prompt-injection block, and card number masking."""

import re

MAX_LENGTH = 1000

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
]

CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _mask_card(match: re.Match) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if not (13 <= len(digits) <= 19):
        return raw
    return f"XXXX-XXXX-XXXX-{digits[-4:]}"


def check(message: str) -> dict:
    """Check a user message for safety and clean sensitive information."""
    if len(message) > MAX_LENGTH:
        return {
            "blocked": True,
            "reason": f"Message exceeds {MAX_LENGTH} characters",
            "clean_message": message,
        }

    for pattern in INJECTION_PATTERNS:
        if pattern.search(message):
            return {
                "blocked": True,
                "reason": "Security violation: prompt injection attempt detected",
                "clean_message": message,
            }

    clean = CARD_PATTERN.sub(_mask_card, message)
    return {
        "blocked": False,
        "reason": None,
        "clean_message": clean,
    }
