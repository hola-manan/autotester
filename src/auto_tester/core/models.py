"""
Shared data model for the whole pipeline-tester.

Everything is a plain ``dataclass`` (no pydantic) so the package has a light
dependency footprint and works on new Python versions. ``to_dict`` /
``from_dict`` helpers keep these JSON-round-trippable for traces, reports, and
LLM prompts.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
@dataclass
class Case:
    """A single test input fed to the target pipeline.

    ``payload`` is whatever the adapter's ``run`` expects (e.g. an HTTP request
    body, CLI args, a dict of function kwargs). ``rationale`` records *why* the
    input generator produced this case (edge condition, bug hypothesis, etc.)
    so findings can explain their provenance.
    """

    payload: Dict[str, Any]
    id: str = field(default_factory=lambda: _new_id("case"))
    label: str = ""
    rationale: str = ""
    origin: str = "generated"  # "default" | "generated" | "hypothesis" | "metamorphic"
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Execution trace
# --------------------------------------------------------------------------- #
@dataclass
class StepRecord:
    """One captured step (a wrapped function call) inside a pipeline run."""

    name: str  # dotted step name, e.g. "domain.irrigation.calculate_et0"
    args: Dict[str, Any]  # captured (repr-safe) positional+keyword inputs
    result: Any  # captured (repr-safe) return value
    started_at: float = 0.0
    duration_ms: float = 0.0
    error: Optional[str] = None  # traceback string if the step raised
    order: int = 0  # monotonic call index within the run

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Trace:
    """The ordered sequence of steps captured during one run."""

    steps: List[StepRecord] = field(default_factory=list)

    def add(self, step: StepRecord) -> None:
        step.order = len(self.steps)
        self.steps.append(step)

    def by_name(self, name: str) -> List[StepRecord]:
        return [s for s in self.steps if s.name == name]

    def names(self) -> List[str]:
        return [s.name for s in self.steps]

    def to_dict(self) -> Dict[str, Any]:
        return {"steps": [s.to_dict() for s in self.steps]}


@dataclass
class RunResult:
    """The full record of running one Case: output, trace, logs, timing."""

    case: Case
    output: Any = None  # the pipeline's final result (response body, etc.)
    trace: Trace = field(default_factory=Trace)
    logs: str = ""  # captured stdout/stderr or tapped log file content
    error: Optional[str] = None  # top-level failure (crash), if any
    duration_ms: float = 0.0
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "output": self.output,
            "trace": self.trace.to_dict(),
            "logs": self.logs,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
        }


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
class Severity(str, Enum):
    CRITICAL = "critical"  # wrong/fabricated output a user would act on
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[self.value]


@dataclass
class Finding:
    """A single discrepancy between intent and observed behavior.

    Mirrors how a human QA writes a bug note: what was expected, what actually
    happened, the concrete evidence, and how to reproduce it.
    """

    title: str
    severity: Severity
    category: str  # "correctness" | "fabrication" | "contract" | "robustness" | ...
    intent_excerpt: str  # what the plain-English intent said should happen
    observed: str  # what actually happened
    evidence: Dict[str, Any] = field(default_factory=dict)  # record/step/values
    repro_case_id: str = ""  # Case.id that triggers it
    check_id: str = ""  # which oracle raised it
    confidence: float = 1.0  # 0..1; LLM judgments < 1
    id: str = field(default_factory=lambda: _new_id("find"))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


# --------------------------------------------------------------------------- #
# Oracle results
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    """Outcome of evaluating one oracle against one RunResult.

    ``passed=None`` means the check could not be evaluated (e.g. the step it
    targets was absent), which is distinct from a clean pass.
    """

    check_id: str
    passed: Optional[bool]
    findings: List[Finding] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
            "note": self.note,
        }


def dumps(obj: Any) -> str:
    """JSON dump that tolerates non-serializable values via ``repr`` fallback."""
    return json.dumps(obj, default=lambda o: repr(o), indent=2, ensure_ascii=False)
