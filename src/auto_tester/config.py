"""
Configuration: loads the Gemini key + model names from the environment / .env.

Kept tiny and import-safe — importing this never raises even if the key is
missing, so the package can be imported (and non-LLM unit tests can run) before
a key is configured. The actual key requirement is enforced lazily in ``llm``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    # Load the tester's own .env by ABSOLUTE path so the key is found no matter
    # which directory `autotester` is launched from, then fall back to a .env in
    # the current directory.
    _repo_env = Path(__file__).resolve().parents[2] / ".env"
    if _repo_env.exists():
        load_dotenv(_repo_env)
    load_dotenv()
except Exception:  # python-dotenv not installed yet — env vars still work
    pass


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    model_pro: str  # reasoning-heavy: oracle generation, code scan
    model_flash: str  # high-volume: per-case judging, spot-checks
    use_vertex: bool  # route through Vertex AI (Express) instead of the Developer API
    disable_thinking: bool  # zero the thinking budget on flash judging calls

    @property
    def has_key(self) -> bool:
        return bool(self.gemini_api_key)


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    key = os.environ.get("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    # Vertex AI Express keys start with "AQ."; Developer API keys start with "AIza".
    # Allow an explicit override via GOOGLE_GENAI_USE_VERTEXAI.
    override = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "")
    use_vertex = _truthy(override) if override else key.startswith("AQ.")
    return Settings(
        gemini_api_key=key,
        model_pro=os.environ.get("AUTO_TESTER_MODEL_PRO", "gemini-2.5-pro").strip(),
        model_flash=os.environ.get("AUTO_TESTER_MODEL_FLASH", "gemini-2.5-flash").strip(),
        use_vertex=use_vertex,
        disable_thinking=_truthy(os.environ.get("AUTO_TESTER_DISABLE_THINKING", "true")),
    )
