"""
LLM-backed oracles — the judgment calls that deterministic Python checks can't
make: "is this specific output actually CORRECT and sensible given the intent?"

Two oracles, both running on the cheap ``flash`` tier per run:

  * ``spot_check``  — trace a few sampled steps and judge whether each step's
    input->output transformation matches intent (how a human QA spot-checks).
  * ``final_judge`` — judge the whole final output against the intent on a
    rubric, flagging inaccuracies, contradictions, and undisclosed fabrication.

Both return :class:`Finding` objects with confidence < 1 (they are judgments).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .core.models import Finding, RunResult, Severity
from .llm import LLM

_SYSTEM = (
    "You are a rigorous QA reviewer. You judge whether software output is "
    "actually CORRECT given the stated intent — not whether it merely ran. You "
    "flag silent data drops, swapped/mislabeled fields, hardcoded or fabricated "
    "values presented as real, out-of-range numbers, internal contradictions, "
    "and results that don't follow from the inputs. You are specific and cite "
    "the exact values. You do NOT invent problems; if it looks correct, say so. "
    "Ignore differences within rounding tolerance (e.g. a texture sum of 99.95 "
    "vs 100, or 2.33 vs 2.31) — never report rounding as a discrepancy. If "
    "several symptoms share ONE root cause, report a SINGLE finding for that "
    "root cause, not one finding per symptom."
)

_FINDING_FORMAT = (
    'Return JSON: {"findings": [{"title": str, "severity": '
    '"critical|high|medium|low", "category": "correctness|accuracy|fabrication|contract", '
    '"observed": str, "evidence": {...}, "confidence": 0.0-1.0}]}. '
    "Empty findings list means everything looked correct. "
    "NOTE: the data below may be abbreviated for length — you may see "
    '"__omitted__" markers or "...(+N chars)". Do NOT report truncation, '
    "missing brackets, or incomplete/cut-off entries as findings; judge only "
    "values that are fully present."
)

_MAX_STR = 1500
_MAX_ITEMS = 60


def _clip(value: Any, max_chars: int, _depth: int = 0) -> Any:
    """Shrink a value to fit a budget while keeping it well-formed JSON.

    Unlike a raw ``json.dumps(...)[:N]`` slice (which cuts mid-structure and
    makes the judge think real data is truncated), this drops list tails and
    deep sub-trees, inserting explicit ``__omitted__`` markers. The result is
    always valid JSON so the model never mistakes our abbreviation for a bug.
    """
    # Fast path: if it already fits, return as-is.
    try:
        if len(json.dumps(value, default=repr)) <= max_chars:
            return value
    except Exception:
        pass

    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + f"...(+{len(value) - _MAX_STR} chars)"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if _depth >= 8:
        return {"__omitted__": f"{type(value).__name__} hidden for length"}

    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        budget = max_chars
        for i, (k, v) in enumerate(value.items()):
            if budget <= 0:
                out["__omitted__"] = f"{len(value) - i} more keys hidden for length"
                break
            cv = _clip(v, max(200, budget // 2), _depth + 1)
            out[str(k)] = cv
            budget -= len(json.dumps(cv, default=repr))
        return out
    if isinstance(value, (list, tuple)):
        seq = list(value)
        out_list = []
        budget = max_chars
        for i, v in enumerate(seq):
            if budget <= 0:
                out_list.append({"__omitted__": f"{len(seq) - i} more items hidden for length"})
                break
            cv = _clip(v, max(200, budget // 2), _depth + 1)
            out_list.append(cv)
            budget -= len(json.dumps(cv, default=repr))
        return out_list
    # Fallback: short repr
    r = repr(value)
    return r if len(r) <= _MAX_STR else r[:_MAX_STR] + "..."


def _dump(value: Any, max_chars: int) -> str:
    """Clip then serialize to a JSON string for a prompt."""
    return json.dumps(_clip(value, max_chars), indent=2, default=repr)


def _to_findings(raw: Any, run: RunResult, check_id: str) -> List[Finding]:
    items = raw.get("findings", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    out: List[Finding] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            sev = Severity(it.get("severity", "medium"))
        except ValueError:
            sev = Severity.MEDIUM
        out.append(
            Finding(
                title=it.get("title", "LLM-judged discrepancy"),
                severity=sev,
                category=it.get("category", "correctness"),
                intent_excerpt=it.get("intent_excerpt", ""),
                observed=it.get("observed", ""),
                evidence=it.get("evidence", {}),
                repro_case_id=run.case.id,
                check_id=check_id,
                confidence=float(it.get("confidence", 0.7)),
            )
        )
    return out


def final_judge(llm: LLM, intent: str, run: RunResult) -> List[Finding]:
    if run.output is None:
        return []  # crash handled by runner
    prompt = "\n".join(
        [
            "# Intent",
            intent.strip(),
            "",
            "# Input that was given to the pipeline",
            _dump(run.case.payload, 6000),
            "",
            "# Final output the pipeline produced",
            _dump(run.output, 16000),
            "",
            "# Task",
            "Judge whether this output is correct and consistent given the input "
            "and intent. Flag anything wrong, fabricated, contradictory, or out of "
            "range. " + _FINDING_FORMAT,
        ]
    )
    raw = llm.json(prompt, tier="flash", system=_SYSTEM, temperature=0.1)
    return _to_findings(raw, run, "llm.final_judge")


def focused_check(llm: LLM, intent: str, focus: str, run: RunResult, max_steps: int = 12) -> List[Finding]:
    """Judge specifically whether the user's FOCUS feature behaves correctly.

    Unlike ``final_judge`` (which looks at everything), this concentrates the
    model's attention on the one feature/concern the user flagged, using both
    the final output and the captured steps relevant to it.
    """
    if run.output is None or not focus.strip():
        return []
    steps = [
        {"name": s.name, "args": s.args, "result": s.result, "error": s.error}
        for s in run.trace.steps[:max_steps]
    ]
    prompt = "\n".join(
        [
            "# Overall intent",
            intent.strip(),
            "",
            "# FOCUS — verify ONLY this feature/behavior in depth",
            focus.strip(),
            "",
            "# Input",
            _dump(run.case.payload, 4000),
            "",
            "# Captured steps",
            _dump(steps, 10000),
            "",
            "# Final output",
            _dump(run.output, 9000),
            "",
            "# Task",
            "Decide whether the FOCUS feature works correctly for this input. Trace "
            "the relevant values through the steps and the output. Flag any way it is "
            "wrong, incomplete, or diverges from the intended behavior of THIS feature. "
            + _FINDING_FORMAT,
        ]
    )
    raw = llm.json(prompt, tier="flash", system=_SYSTEM, temperature=0.1)
    return _to_findings(raw, run, "llm.focused_check")


def spot_check(llm: LLM, intent: str, run: RunResult, max_steps: int = 8) -> List[Finding]:
    if not run.trace.steps:
        return []
    steps = [
        {"name": s.name, "args": s.args, "result": s.result, "error": s.error}
        for s in run.trace.steps[:max_steps]
    ]
    prompt = "\n".join(
        [
            "# Intent",
            intent.strip(),
            "",
            "# Original input",
            _dump(run.case.payload, 4000),
            "",
            "# Captured pipeline steps (name + input args + returned result)",
            _dump(steps, 11000),
            "",
            "# Task",
            "For each step, verify by hand that its result correctly follows from "
            "its inputs per the intent. Flag any step whose output is wrong, uses "
            "a hardcoded/placeholder value instead of the real input, drops data, "
            "swaps fields, or swallows an error. Reference the step name and the "
            "exact values. " + _FINDING_FORMAT,
        ]
    )
    raw = llm.json(prompt, tier="flash", system=_SYSTEM, temperature=0.1)
    return _to_findings(raw, run, "llm.spot_check")
