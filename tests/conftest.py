import os
import tempfile
from pathlib import Path

# Settings are read once at import, so the test database and switches must be set first.
# Empty keys win over .env (load_dotenv never overrides), so no test can reach a real model.
os.environ.update(
    DB_PATH=str(Path(tempfile.mkdtemp()) / "test.db"),
    USE_LLM="0",
    GEMINI_API_KEY="",
    GROQ_API_KEY="",
    TOKEN_SECRET="test-secret",
    DEMO_MODE="1",
)

import pytest

from app import seed
from app.agents import llm


@pytest.fixture(autouse=True)
def fresh_bank() -> None:
    seed.seed(force=True)


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The JSON agents get whatever verdict a test puts here under their name, or None (skipped)."""
    canned: dict[str, object] = {}
    monkeypatch.setattr(
        llm,
        "generate_json",
        lambda name, system, prompt, schema, trace: canned.get(name),
    )
    return canned
