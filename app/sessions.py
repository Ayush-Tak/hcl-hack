"""Per-session memory and flags, shared by the engine and by actions that re-check at confirm time.

In memory for the demo, so a restart clears them; production keeps this in Redis (design.md section 10).
"""

_SESSIONS: dict[tuple[str, str], dict] = {}


def get(customer_id: str, session_id: str) -> dict:
    # Keyed by customer too, so another customer's session ID gets an empty session, not theirs.
    return _SESSIONS.setdefault(
        (customer_id, session_id),
        {"id": session_id, "memory": [], "scam_risk": "none", "scam_patterns": []},
    )


def raise_scam_risk(session: dict, risk: str, patterns: list[str]) -> None:
    """The flag only goes up within a session: a calm follow-up message doesn't clear it."""
    order = ["none", "low", "high"]
    if order.index(risk) > order.index(session["scam_risk"]):
        session["scam_risk"] = risk
    session["scam_patterns"] = sorted(set(session["scam_patterns"]) | set(patterns))
