"""
The evaluator — runs a registry of checks against pipeline runs and collects
:class:`CheckResult` / :class:`Finding` objects.

Handles the two deterministic oracle families:
  * **invariants**  — evaluated against each individual RunResult,
  * **metamorphic** — evaluated by re-running a transformed input through the
    adapter and comparing the two runs.

LLM-backed oracles (spot-check / contract / judge) live in ``llm_oracles`` and
are merged in by the orchestrator; keeping them out of here means the
deterministic evaluation needs no API key and no network.
"""

from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path
from typing import List

from .adapter import PipelineAdapter
from .checks import (
    Registry,
    normalize_findings,
    reset_registry,
    snapshot_registry,
)
from .models import CheckResult, Case, RunResult


def load_checks_module(path: str | Path) -> Registry:
    """Import a checks module by file path and return what it registered."""
    path = Path(path)
    reset_registry()
    spec = importlib.util.spec_from_file_location(f"checks_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load checks module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # decorators populate the global registry
    return snapshot_registry()


def evaluate_invariants(registry: Registry, runs: List[RunResult]) -> List[CheckResult]:
    results: List[CheckResult] = []
    for spec in registry.invariants:
        for run in runs:
            try:
                raw = spec.fn(run)
            except Exception:
                results.append(
                    CheckResult(
                        check_id=spec.id,
                        passed=None,
                        note=f"invariant raised on case {run.case.id}:\n{traceback.format_exc()}",
                    )
                )
                continue
            findings = normalize_findings(
                raw,
                check_id=spec.id,
                description=spec.description,
                severity=spec.severity,
                category=spec.category,
                case_id=run.case.id,
            )
            for f in findings:
                if not f.intent_excerpt:
                    f.intent_excerpt = spec.description
            results.append(
                CheckResult(check_id=spec.id, passed=(len(findings) == 0), findings=findings)
            )
    return results


def evaluate_metamorphic(
    registry: Registry, runs: List[RunResult], adapter: PipelineAdapter,
    base_limit: int | None = None,
) -> List[CheckResult]:
    results: List[CheckResult] = []
    bases = runs if base_limit is None else runs[:base_limit]
    for spec in registry.metamorphic:
        for base in bases:
            try:
                variant_payload = spec.transform(dict(base.case.payload))
            except Exception:
                results.append(
                    CheckResult(check_id=spec.id, passed=None,
                                note=f"transform raised on {base.case.id}: {traceback.format_exc()}")
                )
                continue
            variant_case = Case(
                payload=variant_payload,
                label=f"{base.case.label or base.case.id}+{spec.id}",
                origin="metamorphic",
                rationale=f"metamorphic variant for {spec.id}",
            )
            variant_run = adapter.run(variant_case)
            try:
                raw = spec.relation(base, variant_run)
            except Exception:
                results.append(
                    CheckResult(check_id=spec.id, passed=None,
                                note=f"relation raised on {base.case.id}: {traceback.format_exc()}")
                )
                continue
            findings = normalize_findings(
                raw,
                check_id=spec.id,
                description=spec.description,
                severity=spec.severity,
                category=spec.category,
                case_id=base.case.id,
            )
            for f in findings:
                if not f.intent_excerpt:
                    f.intent_excerpt = spec.description
                f.evidence.setdefault("variant_case_id", variant_run.case.id)
            results.append(
                CheckResult(check_id=spec.id, passed=(len(findings) == 0), findings=findings)
            )
    return results


def evaluate(
    registry: Registry, runs: List[RunResult], adapter: PipelineAdapter,
    metamorphic_base_limit: int | None = None,
) -> List[CheckResult]:
    """Run all deterministic checks and return every CheckResult.

    ``metamorphic_base_limit`` caps how many base runs metamorphic relations
    re-run against (each re-run executes the pipeline again — important when the
    target hits live external APIs).
    """
    return evaluate_invariants(registry, runs) + evaluate_metamorphic(
        registry, runs, adapter, base_limit=metamorphic_base_limit
    )
