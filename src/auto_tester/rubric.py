"""
Rubric extraction — the G-Eval pattern for the LLM oracles.

Instead of asking a judge "is this output correct?" against a wall of intent
prose, we extract a fixed list of concrete, checkable criteria from the intent
ONCE (pro tier), cache it as ``rubric.json`` next to the project's other
artifacts, and have every judging call evaluate the SAME criteria. This makes
LLM judgments more consistent across runs and across the three chained oracles,
and the rubric itself is reviewable/hand-editable like the checks module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .llm import LLM

_SYSTEM = (
    "You turn a plain-English software spec into a numbered list of concrete, "
    "independently checkable correctness criteria a QA reviewer scores one by "
    "one. Each criterion names the exact field/step/value to look at and what "
    "must be true of it."
)


def _prompt(intent: str) -> str:
    return "\n".join([
        "# Intent (plain-English spec)",
        intent.strip()[:12000],
        "",
        "# Task",
        "Extract 5-15 evaluation criteria for judging this pipeline's output. "
        "Each must be a single sentence, concrete and checkable from the output "
        "and traced steps alone (no source access). Cover: disclosure of "
        "fabricated/defaulted data, inputs actually flowing to outputs, value "
        "ranges, internal consistency, and no silent data loss — as applicable.",
        "",
        'Return JSON: {"criteria": ["...", ...]}',
    ])


def get_rubric(llm: LLM, intent: str, project_dir: str | Path,
               regenerate: bool = False) -> List[str]:
    """Load the cached rubric or extract it from the intent (and cache it)."""
    path = Path(project_dir) / "rubric.json"
    if path.exists() and not regenerate:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            criteria = data.get("criteria") if isinstance(data, dict) else data
            if isinstance(criteria, list) and criteria:
                return [str(c) for c in criteria]
        except Exception:
            pass  # fall through and regenerate
    raw = llm.json(_prompt(intent), tier="pro", system=_SYSTEM, temperature=0.2)
    criteria = raw.get("criteria") if isinstance(raw, dict) else raw
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("Rubric extraction returned no criteria.")
    criteria = [str(c) for c in criteria][:20]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"criteria": criteria}, indent=2), encoding="utf-8")
    return criteria


def rubric_block(criteria: List[str] | None) -> str:
    """Render the rubric for a judging prompt ('' when there is none)."""
    if not criteria:
        return ""
    lines = ["# Rubric — evaluate EACH criterion, in order; cite the criterion "
             "number in any finding's evidence:"]
    lines += [f"{i + 1}. {c}" for i, c in enumerate(criteria)]
    return "\n".join(lines)
