"""
Input generator — uses Gemini to produce diverse, edge, and adversarial test
inputs from the plain-English intent, mutating the adapter's example payloads.

It also accepts **bug hypotheses** from the code-scan pass and crafts inputs
specifically designed to expose each one — the "code-read -> targeted input"
half of the two-way strategy.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .core.models import Case
from .llm import LLM

_SYSTEM = (
    "You are a meticulous QA engineer who finds correctness bugs by RUNNING "
    "software with carefully chosen inputs. You design inputs that stress edge "
    "conditions, boundaries, and the exact spots where an implementation is "
    "likely to silently diverge from its stated intent. You do not look at "
    "outputs yet — only design the inputs."
)


def _prompt(intent: str, example_payloads: List[Dict[str, Any]], n: int,
            hypotheses: Optional[List[Dict[str, Any]]], focus: Optional[str]) -> str:
    parts = [
        "# Pipeline intent (plain English)",
        intent.strip(),
        "",
        "# Example input payload(s) — produce NEW payloads with the SAME shape/keys",
        json.dumps(example_payloads, indent=2, default=repr),
        "",
        f"# Task",
        f"Design {n} test input payloads that probe this pipeline for subtle "
        "correctness, accuracy, and data-handling bugs. Cover a spread of: "
        "normal/representative cases, boundary values, empty/missing fields, "
        "duplicates, malformed or out-of-range values, unicode, and conflicting "
        "or contradictory inputs. Each payload MUST match the example shape so "
        "it can be fed to the pipeline directly.",
    ]
    if focus:
        parts += [
            "",
            "# FOCUS — the user specifically wants this feature/behavior stress-tested",
            focus.strip(),
            "Devote the MAJORITY of your inputs to exercising this focus area "
            "thoroughly (normal, boundary, and adversarial cases for it).",
        ]
    if hypotheses:
        parts += [
            "",
            "# Suspected bugs from a code review — design at least one input to EXPOSE each",
            json.dumps(hypotheses, indent=2, default=repr),
        ]
    parts += [
        "",
        "# Output format",
        'Return a JSON array. Each item: {"label": str, "rationale": str, '
        '"tags": [str], "targets_hypothesis": str|null, "payload": {...}}. '
        '"rationale" says what bug class this input probes. "targets_hypothesis" '
        "is the id of the hypothesis it targets, or null.",
    ]
    return "\n".join(parts)


def generate_cases(
    llm: LLM,
    intent: str,
    example_payloads: List[Dict[str, Any]],
    *,
    n: int = 12,
    hypotheses: Optional[List[Dict[str, Any]]] = None,
    focus: Optional[str] = None,
) -> List[Case]:
    raw = llm.json(
        _prompt(intent, example_payloads, n, hypotheses, focus),
        tier="pro",
        system=_SYSTEM,
        temperature=0.6,  # diversity matters here
    )
    items = raw if isinstance(raw, list) else raw.get("cases", [])
    cases: List[Case] = []
    for it in items:
        if not isinstance(it, dict) or "payload" not in it:
            continue
        hyp = it.get("targets_hypothesis")
        cases.append(
            Case(
                payload=it["payload"],
                label=str(it.get("label", ""))[:120],
                rationale=str(it.get("rationale", "")),
                origin="hypothesis" if hyp else "generated",
                tags=[str(t) for t in it.get("tags", [])] + ([f"hyp:{hyp}"] if hyp else []),
            )
        )
    return cases
