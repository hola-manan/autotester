"""
Thin Gemini wrapper used by every LLM-backed oracle.

Responsibilities:
  * lazy client creation (so importing the package never needs a key),
  * model routing (``pro`` for reasoning, ``flash`` for volume),
  * robust JSON extraction (models wrap JSON in prose / ``json`` fences),
  * light retry on transient errors.

All higher-level modules (oracle_gen, code_scan, evaluator's LLM checks,
input_gen) call :meth:`LLM.json` or :meth:`LLM.text` rather than touching the
SDK directly, so swapping providers later is a one-file change.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from .config import Settings, load_settings


class LLMError(RuntimeError):
    pass


class MissingKeyError(LLMError):
    pass


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model response.

    Handles three common shapes: a bare JSON document, a ```json fenced block,
    or JSON embedded in surrounding prose.
    """
    text = text.strip()
    # 1) straight parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2) fenced block
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3) first balanced {...} or [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except Exception:
                        break
    raise LLMError(f"Could not extract JSON from model response:\n{text[:500]}")


class LLM:
    """Gemini client with model routing and JSON helpers."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or load_settings()
        self._client = None

    # -- internals -------------------------------------------------------- #
    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self.settings.has_key:
            raise MissingKeyError(
                "GEMINI_API_KEY is not set. Add it to .env (see .env.example) "
                "before running LLM-backed steps."
            )
        try:
            from google import genai  # imported lazily
        except Exception as e:  # pragma: no cover
            raise LLMError(
                "google-genai is not installed. Run: pip install google-genai"
            ) from e
        if self.settings.use_vertex:
            # Vertex AI Express mode: api_key auth, no project/location needed.
            self._client = genai.Client(vertexai=True, api_key=self.settings.gemini_api_key)
        else:
            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    def _model(self, tier: str) -> str:
        return self.settings.model_pro if tier == "pro" else self.settings.model_flash

    # -- public API ------------------------------------------------------- #
    def text(
        self,
        prompt: str,
        *,
        tier: str = "flash",
        system: Optional[str] = None,
        temperature: float = 0.2,
        retries: int = 3,
    ) -> str:
        """Return raw model text for ``prompt``."""
        client = self._ensure_client()
        from google.genai import types  # type: ignore

        cfg_kwargs = dict(temperature=temperature, system_instruction=system)
        # On the flash tier, zero the thinking budget to cut cost/latency for
        # high-volume judging (the reasoning-heavy work runs on the pro tier).
        if tier != "pro" and self.settings.disable_thinking:
            try:
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            except Exception:
                pass
        cfg = types.GenerateContentConfig(**cfg_kwargs)
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                resp = client.models.generate_content(
                    model=self._model(tier),
                    contents=prompt,
                    config=cfg,
                )
                return (resp.text or "").strip()
            except Exception as e:  # transient: backoff and retry
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"Gemini call failed after {retries} attempts: {last_err}")

    def json(
        self,
        prompt: str,
        *,
        tier: str = "flash",
        system: Optional[str] = None,
        temperature: float = 0.1,
        retries: int = 3,
    ) -> Any:
        """Return parsed JSON from the model. Appends a strictness reminder."""
        full = (
            prompt
            + "\n\nRespond with ONLY valid JSON. No prose, no markdown fences."
        )
        raw = self.text(
            full, tier=tier, system=system, temperature=temperature, retries=retries
        )
        return _extract_json(raw)
