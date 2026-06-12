"""
Code-based suspicion pass — the white-box half of the strategy.

Gemini reads the target's source alongside the plain-English intent and returns
ranked hypotheses about where the implementation likely diverges from intent:
silently dropped inputs, hardcoded/placeholder values, swapped fields, swallowed
errors, off-by-one, wrong formulas, undisclosed fabrication, etc.

These hypotheses do two jobs: they are reported as low-confidence findings in
their own right, and they steer :mod:`input_gen` to craft inputs that confirm
them at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.models import Finding, Severity
from .llm import LLM

_SYSTEM = (
    "You are a senior engineer doing a correctness-focused code review. You are "
    "looking specifically for places where the code RUNS FINE and passes its own "
    "tests but produces subtly WRONG results versus the stated intent: silently "
    "dropped or filtered records, hardcoded/placeholder values used in place of "
    "real data, swapped or mislabeled fields, swallowed exceptions, off-by-one "
    "errors, incorrect formulas, and fabricated data that isn't disclosed. You "
    "report concrete, checkable hypotheses, not style nits."
)

_MAX_FILE = 16000


def _gather(paths: List[str | Path]) -> str:
    chunks = []
    for p in paths:
        p = Path(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(text) > _MAX_FILE:
            text = text[:_MAX_FILE] + f"\n# ...(truncated {len(text) - _MAX_FILE} chars)\n"
        chunks.append(f"# ===== FILE: {p} =====\n{text}")
    return "\n\n".join(chunks)


def _prompt(intent: str, code: str, focus: Optional[str]) -> str:
    focus_block = (
        ["", "# FOCUS — concentrate your review on this feature/concern",
         focus.strip(), ""]
        if focus else []
    )
    return "\n".join(
        focus_block
        + [
            "# Stated intent (what the pipeline SHOULD do)",
            intent.strip(),
            "",
            "# Source code under review",
            code,
            "",
            "# Task",
            "List concrete hypotheses where the code likely diverges from the "
            "intent in a way that RUNS without crashing but is WRONG. For each, "
            "explain how an input-based test could confirm it.",
            "",
            "# Output format",
            'JSON array of: {"id": short_snake_case_id, "title": str, '
            '"location": "file:line or function", "why": str, '
            '"suspected_symptom": str, "severity": "critical|high|medium|low", '
            '"probe_hint": "what input would expose this at runtime"}.',
        ]
    )


def scan_code(llm: LLM, intent: str, paths: List[str | Path],
              focus: Optional[str] = None) -> List[Dict[str, Any]]:
    code = _gather(paths)
    if not code.strip():
        return []
    raw = llm.json(_prompt(intent, code, focus), tier="pro", system=_SYSTEM, temperature=0.3)
    items = raw if isinstance(raw, list) else raw.get("hypotheses", [])
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("title"):
            it.setdefault("id", it["title"][:40].lower().replace(" ", "_"))
            out.append(it)
    return out


def hypotheses_to_findings(hyps: List[Dict[str, Any]]) -> List[Finding]:
    """Surface each hypothesis as a low-confidence code-review finding.

    Confidence is deliberately < 1 — these are *suspicions* from reading code,
    promoted to higher-confidence findings only when an input-based check
    confirms them.
    """
    findings = []
    for h in hyps:
        sev = h.get("severity", "medium")
        try:
            severity = Severity(sev)
        except ValueError:
            severity = Severity.MEDIUM
        findings.append(
            Finding(
                title=f"[code-review] {h.get('title')}",
                severity=severity,
                category="code-suspicion",
                intent_excerpt=h.get("why", ""),
                observed=h.get("suspected_symptom", ""),
                evidence={"location": h.get("location"), "probe_hint": h.get("probe_hint")},
                check_id=f"code_scan.{h.get('id')}",
                confidence=0.5,
            )
        )
    return findings
