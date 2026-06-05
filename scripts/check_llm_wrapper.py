"""Verify the LLM wrapper end-to-end (Vertex routing + JSON parsing) with tiny calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auto_tester.config import load_settings
from auto_tester.llm import LLM

s = load_settings()
print(f"use_vertex={s.use_vertex}  disable_thinking={s.disable_thinking}")
llm = LLM(s)

# flash tier (thinking disabled) + JSON extraction
got = llm.json('Return a JSON object {"ok": true, "n": 42}.', tier="flash")
print("flash json ->", got)

# pro tier text (used by code-scan / oracle-gen)
txt = llm.text("Reply with exactly: PRO_OK", tier="pro", temperature=0)
print("pro text  ->", repr(txt.strip()[:40]))

print("WRAPPER OK" if isinstance(got, dict) and got.get("ok") is True else "CHECK OUTPUT")
