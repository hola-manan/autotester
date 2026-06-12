"""
Strategy generator — uses Gemini to turn the intent + example payloads into a
reviewable Hypothesis strategies module (``strategies_<name>.py``).

Like the checks module, it is generated ONCE and reused across runs, lives next
to the project's other artifacts, and can be hand-corrected. The fuzz stage
(:mod:`auto_tester.hypothesis_runner`) draws payloads from it and shrinks any
failure to a minimal reproducing input.

Code-scan bug hypotheses are folded in as ``SEEDS`` — adversarial payloads
tried before random generation — so a white-box suspicion is only ever
confirmed by actually RUNNING the pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm import LLM

_SYSTEM = (
    "You write Hypothesis (property-based testing) strategies in Python. You "
    "produce input payloads that are STRUCTURALLY valid (the pipeline's "
    "contract) but explore the full legal value space: boundaries, extremes, "
    "unicode, empty collections, duplicates. You only emit valid Python."
)

_API_CONTRACT = '''
Produce a complete Python module with EXACTLY this contract:

    from hypothesis import strategies as st

    def payload_strategy():
        """Returns a Hypothesis strategy producing FULL input payload dicts."""
        return st.fixed_dictionaries({...})

    SEEDS = [
        {...},  # specific adversarial payloads to try FIRST (may be empty)
    ]

Rules:
  - payload_strategy() must produce dicts with the SAME keys as the example
    payloads (the pipeline is called with these as keyword arguments).
  - Values must be STRUCTURALLY valid (right types, parseable formats) — the
    point is to explore the legal input space, not to feed garbage types.
    Constrain ranges to what the docs/intent say is legal (e.g. lat in -90..90),
    but include the boundaries.
  - Keep strategies fast: no st.from_regex with huge alphabets, no recursive
    strategies, cap list sizes (max_size<=10) and string sizes (max_size<=50).
  - floats: use allow_nan=False, allow_infinity=False unless the intent says
    NaN/inf are legal inputs.
  - SEEDS: 3-8 concrete payloads targeting the suspected bugs / edge conditions
    described below. Each must be a plain JSON-style dict literal.
  - Use only the Python standard library + hypothesis.
'''


def _prompt(intent: str, example_payloads: List[Dict[str, Any]],
            hypotheses: Optional[List[Dict[str, Any]]], project: str) -> str:
    parts = [
        _API_CONTRACT,
        "# Pipeline intent",
        intent.strip(),
        "",
        "# Example input payload(s) — payload_strategy() must produce this shape",
        json.dumps(example_payloads, indent=2, default=repr)[:6000],
    ]
    if hypotheses:
        parts += [
            "",
            "# Suspected bugs from a code review — craft SEEDS that would expose each",
            json.dumps(hypotheses, indent=2, default=repr)[:6000],
        ]
    parts += [
        "",
        f"# Task: write the full strategies module for project '{project}'.",
        "Output ONLY the Python source of the module (no markdown fences, no prose).",
    ]
    return "\n".join(parts)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


def generate_strategies_module(
    llm: LLM,
    intent: str,
    example_payloads: List[Dict[str, Any]],
    project: str,
    *,
    hypotheses: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Return Python source for the strategies module (validated to compile)."""
    raw = llm.text(
        _prompt(intent, example_payloads, hypotheses, project),
        tier="pro",
        system=_SYSTEM,
        temperature=0.3,
    )
    source = _strip_fences(raw)
    compile(source, f"strategies_{project}.py", "exec")
    if "payload_strategy" not in source:
        raise ValueError("Generated strategies module does not define payload_strategy().")
    return source


def write_strategies_module(source: str, project_dir: str | Path, project: str) -> Path:
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"strategies_{project}.py"
    path.write_text(source, encoding="utf-8")
    return path
