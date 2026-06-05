"""
Oracle generator — uses Gemini to turn plain-English intent (+ a sample trace,
+ optionally the code) into a reviewable Python checks module that uses the
``@invariant`` / ``@metamorphic`` decorators from :mod:`auto_tester.core.checks`.

The module is written to ``projects/<name>/checks_<name>.py`` so you can read,
trust, and hand-correct it. It is generated ONCE and reused across runs (not
regenerated every run) for reproducibility and cost.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.models import RunResult
from .llm import LLM

_SYSTEM = (
    "You write precise, executable Python test oracles. You reconstruct the "
    "EXPECTED result from the INPUT and compare it to what the pipeline actually "
    "produced, so the checks work even when outputs are non-deterministic. You "
    "only emit valid Python."
)

_API_CONTRACT = '''
You must produce a complete Python module that imports:

    from auto_tester.core.checks import invariant, metamorphic

and declares checks using these decorators. Contract:

  @invariant(id="snake_id", description="plain english", severity="critical|high|medium|low", category="correctness|fabrication|contract|accuracy")
  def _(run):
      # run.case.payload  -> the INPUT dict that was fed to the pipeline
      # run.output        -> the pipeline's final output (may be dict/list/None)
      # run.trace.steps   -> ordered StepRecord list; each has .name, .args (dict), .result
      # Reconstruct the expected truth from run.case.payload and compare.
      # Return None to PASS, or a dict to FAIL:
      #   {"observed": "...what was wrong...", "evidence": {...concrete values...}}
      ...

  @metamorphic(id="snake_id", description="...",
               transform=lambda p: {**p, ...},   # returns a NEW input payload
               severity="...", category="...")
  def _(base, variant):
      # base.output vs variant.output for two runs whose inputs differ by transform
      # Return None to PASS or a dict to FAIL (same shape as above).
      ...

Rules:
  - ALWAYS guard against run.output being None (a crash) by returning None early.
  - Use only the Python standard library.
  - Helper functions are allowed at module level.
  - Do NOT call the pipeline yourself; the framework runs it.
  - Prefer many small, specific checks over a few broad ones, BUT do not emit
    multiple checks that re-flag the SAME root cause — one precise check per
    distinct property.
  - For "must be disclosed / listed / included" checks (e.g. a fabricated_fields
    list), flag ONLY missing REQUIRED items — the dangerous direction. NEVER flag
    EXTRA present items as a failure: over-disclosure is safe, not a bug. Do NOT
    use strict set-equality (set(actual) != set(expected)) on lists whose exact
    contents depend on runtime conditions (network failures, source fallbacks);
    use subset / required-membership checks instead.
  - Ignore differences within rounding tolerance (use math.isclose with a small
    tolerance); never fail a check on rounding noise like 99.95 vs 100.
  - Find steps with: [s for s in run.trace.steps if s.name == "..."]; s.args / s.result are JSON-safe summaries.
'''


def _trace_digest(run: RunResult, max_steps: int = 40) -> Dict[str, Any]:
    return {
        "input_payload": run.case.payload,
        "output": run.output,
        "steps": [
            {"name": s.name, "args": s.args, "result": s.result}
            for s in run.trace.steps[:max_steps]
        ],
    }


def _prompt(intent: str, sample: Dict[str, Any], code: Optional[str], project: str) -> str:
    parts = [
        _API_CONTRACT,
        "# Pipeline intent",
        intent.strip(),
        "",
        "# A sample run (one real input -> output + captured steps)",
        "Use this to learn the exact data shapes and step names.",
        "```json",
        json.dumps(sample, indent=2, default=repr)[:12000],
        "```",
    ]
    if code:
        parts += ["", "# Source code (for deriving correct expected values)", code[:14000]]
    parts += [
        "",
        f"# Task: write the full checks module for project '{project}'.",
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


def generate_checks_module(
    llm: LLM,
    intent: str,
    sample_run: RunResult,
    project: str,
    *,
    code: Optional[str] = None,
) -> str:
    """Return Python source for the checks module (validated to compile)."""
    raw = llm.text(
        _prompt(intent, _trace_digest(sample_run), code, project),
        tier="pro",
        system=_SYSTEM,
        temperature=0.2,
    )
    source = _strip_fences(raw)
    # Safety: must compile and must use the decorators.
    compile(source, f"checks_{project}.py", "exec")
    if "invariant" not in source:
        raise ValueError("Generated checks module does not register any invariants.")
    return source


def write_checks_module(source: str, project_dir: str | Path, project: str) -> Path:
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"checks_{project}.py"
    path.write_text(source, encoding="utf-8")
    return path
