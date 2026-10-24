"""Settings, read once from the environment (.env in development)."""

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

logger = logging.getLogger(__name__)


def _token_secret() -> str:
    secret = os.getenv("TOKEN_SECRET", "")
    if secret:
        return secret
    if os.getenv("DEMO_MODE", "1") != "1":
        raise RuntimeError("TOKEN_SECRET must be set when DEMO_MODE=0")
    # Never a fixed default: anyone who read it in the repo could forge tokens. A random secret
    # is safe, but it only lives as long as this process, so logins end on restart and tokens
    # don't work across several workers. Set TOKEN_SECRET for those.
    logger.warning("TOKEN_SECRET is not set; using a random secret for this run")
    return secrets.token_hex(32)


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL") or "gemini-3.5-flash"
    groq_model: str = os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile"
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("DB_PATH") or ROOT / "data" / "bank.db")
    )
    policies_dir: Path = ROOT / "data" / "policies"
    token_secret: str = field(default_factory=_token_secret)
    # Demo mode returns the OTP in the response so the demo needs no SMS gateway.
    demo_mode: bool = os.getenv("DEMO_MODE", "1") == "1"
    # Set USE_LLM=0 to force the rules fallback (offline demo, tests).
    use_llm: bool = os.getenv("USE_LLM", "1") == "1"
    max_agent_turns: int = 6
    llm_timeout_ms: int = 15_000


settings = Settings()
