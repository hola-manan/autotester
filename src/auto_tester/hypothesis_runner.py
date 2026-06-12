"""
Property-based fuzz stage — runs the pipeline under Hypothesis.

This is the runtime-first core: Hypothesis draws payloads from a per-project
strategy module (``strategies_<name>.py``, LLM-generated once and reviewable,
like the checks module), runs the REAL pipeline on each, and asserts two
properties:

  * the pipeline does not crash, and
  * every deterministic invariant from the checks registry holds.

When a property fails, Hypothesis SHRINKS the input to a minimal reproducing
payload before reporting — so findings carry the smallest input that triggers
the bug, not the random blob that first hit it. Failing examples are stored in
a per-project database dir and replayed first on the next run.

The strategy module contract::

    from hypothesis import strategies as st

    def payload_strategy():
        return st.fixed_dictionaries({...})   # draws full input payloads

    SEEDS = [ {...}, ... ]   # optional adversarial payloads tried first
"""

from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.adapter import PipelineAdapter
from .core.checks import Registry, normalize_findings
from .core.models import Case, Finding, RunResult, Severity


class _FuzzFailure(Exception):
    """Raised inside the property when a payload violates a property."""

    def __init__(self, kind: str, run: RunResult, findings: Optional[List[Finding]] = None):
        super().__init__(kind)
        self.kind = kind
        self.run = run
        self.findings = findings or []


def load_strategies_module(path: str | Path):
    """Import a strategies module by file path; returns the module object."""
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"strategies_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategies module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "payload_strategy"):
        raise ImportError(f"{path.name} does not define payload_strategy().")
    return module


def _invariant_findings(registry: Optional[Registry], run: RunResult) -> List[Finding]:
    """Evaluate the registry's invariants against one fuzz run."""
    if registry is None:
        return []
    out: List[Finding] = []
    for spec in registry.invariants:
        try:
            raw = spec.fn(run)
        except Exception:
            continue  # a broken check is reported by the normal evaluator, not here
        out += normalize_findings(
            raw, check_id=spec.id, description=spec.description,
            severity=spec.severity, category=spec.category, case_id=run.case.id,
        )
    return out


def fuzz(
    adapter: PipelineAdapter,
    registry: Optional[Registry],
    strategies_module,
    *,
    max_examples: int = 25,
    database_dir: Optional[Path] = None,
) -> List[Finding]:
    """Run the no-crash + invariants property; return findings with minimal repro.

    Returns at most a handful of findings per call (Hypothesis stops at the
    first falsified property and shrinks it); run again after fixing to find
    the next one. Never raises on a target failure — only on misconfiguration
    (e.g. a strategy module that can't draw).
    """
    from hypothesis import HealthCheck, Phase, example, given
    from hypothesis import settings as hyp_settings
    from hypothesis.database import DirectoryBasedExampleDatabase

    # ``last`` always holds the most recently executed payload/run. Hypothesis
    # replays the SHRUNK minimal example last before the failure propagates,
    # so at catch time this is the minimal reproduction.
    last: Dict[str, Any] = {}

    def _property(payload):
        if not isinstance(payload, dict):
            payload = dict(payload)
        run = adapter.run(Case(payload=payload, origin="fuzz", label="hypothesis-fuzz",
                               rationale="drawn by Hypothesis from the strategy module"))
        last["payload"], last["run"] = payload, run
        if run.error:
            raise _FuzzFailure("crash", run)
        bad = _invariant_findings(registry, run)
        if bad:
            raise _FuzzFailure("invariant", run, bad)

    prop = _property
    for seed in list(getattr(strategies_module, "SEEDS", []) or [])[:20]:
        if isinstance(seed, dict):
            prop = example(seed)(prop)
    prop = given(strategies_module.payload_strategy())(prop)

    db = DirectoryBasedExampleDatabase(str(database_dir)) if database_dir else None
    prop = hyp_settings(
        max_examples=max_examples,
        deadline=None,  # real pipelines hit networks; timing is not a bug signal
        database=db,
        derandomize=db is None,
        report_multiple_bugs=False,
        suppress_health_check=list(HealthCheck),
        phases=(Phase.explicit, Phase.reuse, Phase.generate, Phase.shrink),
    )(prop)

    try:
        prop()
    except _FuzzFailure as e:
        return _failure_findings(e, last)
    except BaseException as e:  # Hypothesis internals (Flaky, InvalidArgument, …)
        # A flaky target (fails then passes on replay) is itself worth reporting.
        tail = traceback.format_exc().strip().splitlines()[-1]
        return [Finding(
            title="Property-based fuzzing could not complete",
            severity=Severity.LOW,
            category="robustness",
            intent_excerpt="The fuzz stage should run to completion.",
            observed=f"Hypothesis stopped: {tail}",
            evidence={"last_payload": last.get("payload")},
            check_id="hypothesis.internal",
            confidence=0.6,
        )]
    return []


def _failure_findings(e: _FuzzFailure, last: Dict[str, Any]) -> List[Finding]:
    minimal = last.get("payload")
    run: RunResult = last.get("run") or e.run
    if e.kind == "crash":
        tail = (run.error or "").strip().splitlines()
        return [Finding(
            title="Pipeline crashed during property-based fuzzing (minimal input attached)",
            severity=Severity.HIGH,
            category="robustness",
            intent_excerpt="The pipeline should handle any structurally valid input "
                           "without crashing (or fail in a controlled, reported way).",
            observed=f"Unhandled exception: {tail[-1] if tail else 'exception'}",
            evidence={"minimal_payload": minimal, "traceback_tail": tail[-5:]},
            repro_case_id=run.case.id,
            check_id="hypothesis.no_crash",
        )]
    # Invariant violation: keep the check's own findings, attach the shrunk input.
    findings = e.findings or _invariant_findings_from_last(last)
    for f in findings:
        f.check_id = f"hypothesis.{f.check_id}" if not f.check_id.startswith("hypothesis.") else f.check_id
        f.evidence.setdefault("minimal_payload", minimal)
        f.repro_case_id = run.case.id
    return findings


def _invariant_findings_from_last(last: Dict[str, Any]) -> List[Finding]:  # pragma: no cover
    return []
