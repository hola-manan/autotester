"""
Runner — executes a batch of Cases through an adapter and turns hard crashes
into robustness Findings.

A crash (the pipeline raising instead of returning) is itself a reportable
issue: AI-generated pipelines "shouldn't break", so when one does on a
particular input, that's a finding with a ready-made reproduction.
"""

from __future__ import annotations

from typing import List, Tuple

from .adapter import PipelineAdapter
from .models import Case, Finding, RunResult, Severity


def run_cases(adapter: PipelineAdapter, cases: List[Case]) -> List[RunResult]:
    return [adapter.run(c) for c in cases]


def crash_findings(runs: List[RunResult]) -> List[Finding]:
    """One Finding per run that raised at the top level."""
    findings: List[Finding] = []
    for run in runs:
        if run.error:
            last_line = run.error.strip().splitlines()[-1] if run.error.strip() else "exception"
            findings.append(
                Finding(
                    title="Pipeline raised on this input",
                    severity=Severity.HIGH,
                    category="robustness",
                    intent_excerpt="The pipeline should handle this input without crashing (or fail in a controlled, reported way).",
                    observed=f"Unhandled exception: {last_line}",
                    evidence={"traceback_tail": run.error.strip().splitlines()[-5:],
                              "case_label": run.case.label},
                    repro_case_id=run.case.id,
                    check_id="robustness.no_crash",
                )
            )
    return findings


def run_and_collect(
    adapter: PipelineAdapter, cases: List[Case]
) -> Tuple[List[RunResult], List[Finding]]:
    runs = run_cases(adapter, cases)
    return runs, crash_findings(runs)
