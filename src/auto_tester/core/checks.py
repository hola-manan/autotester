"""
The check registry — the vocabulary LLM-generated (or hand-written) oracle
modules use to declare checks.

A checks module looks like::

    from auto_tester.core.checks import invariant, metamorphic

    @invariant(id="no_dropped_orders", description="...", severity="critical")
    def _(run):
        missing = ...
        if missing:
            return {"observed": f"{len(missing)} orders dropped",
                    "evidence": {"missing_ids": missing}}
        return None  # pass

    @metamorphic(id="amount_scale", description="...",
                 transform=lambda p: {**p, "factor": p["factor"] * 2})
    def _(base, variant):
        ...

Each check function returns ``None`` (pass) or a problem described as a string
or ``{"observed", "evidence", "title", "intent_excerpt"}`` dict (fail). The
evaluator normalizes these into :class:`Finding` objects, filling severity /
category / intent from the decorator so generated functions stay terse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .models import Finding, RunResult, Severity


# --------------------------------------------------------------------------- #
# Spec dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class InvariantSpec:
    id: str
    description: str
    severity: str
    category: str
    fn: Callable[[RunResult], Any]


@dataclass
class MetamorphicSpec:
    id: str
    description: str
    severity: str
    category: str
    transform: Callable[[Dict[str, Any]], Dict[str, Any]]
    relation: Callable[[RunResult, RunResult], Any]


@dataclass
class Registry:
    invariants: List[InvariantSpec] = field(default_factory=list)
    metamorphic: List[MetamorphicSpec] = field(default_factory=list)


# A single module-global registry. The evaluator resets it before importing a
# checks module (one project per process), then snapshots what got registered.
_REGISTRY = Registry()


def reset_registry() -> None:
    _REGISTRY.invariants.clear()
    _REGISTRY.metamorphic.clear()


def snapshot_registry() -> Registry:
    return Registry(
        invariants=list(_REGISTRY.invariants),
        metamorphic=list(_REGISTRY.metamorphic),
    )


# --------------------------------------------------------------------------- #
# Decorators
# --------------------------------------------------------------------------- #
def invariant(
    *,
    id: str,
    description: str,
    severity: str = "high",
    category: str = "correctness",
) -> Callable:
    """Register a per-run invariant check."""

    def deco(fn: Callable[[RunResult], Any]) -> Callable:
        _REGISTRY.invariants.append(
            InvariantSpec(id=id, description=description, severity=severity,
                          category=category, fn=fn)
        )
        return fn

    return deco


def metamorphic(
    *,
    id: str,
    description: str,
    transform: Callable[[Dict[str, Any]], Dict[str, Any]],
    severity: str = "high",
    category: str = "correctness",
) -> Callable:
    """Register a metamorphic relation between a base run and a transformed run.

    ``transform`` maps the base Case payload to the variant payload; the
    decorated function receives ``(base_run, variant_run)``.
    """

    def deco(fn: Callable[[RunResult, RunResult], Any]) -> Callable:
        _REGISTRY.metamorphic.append(
            MetamorphicSpec(id=id, description=description, severity=severity,
                            category=category, transform=transform, relation=fn)
        )
        return fn

    return deco


# --------------------------------------------------------------------------- #
# Normalizing check returns -> Finding
# --------------------------------------------------------------------------- #
def normalize_findings(
    raw: Any,
    *,
    check_id: str,
    description: str,
    severity: str,
    category: str,
    case_id: str,
) -> List[Finding]:
    """Turn a check's return value into zero or more Findings."""
    if raw is None or raw is True or raw == []:
        return []
    items = raw if isinstance(raw, list) else [raw]
    findings: List[Finding] = []
    for item in items:
        if isinstance(item, Finding):
            findings.append(item)
            continue
        if isinstance(item, str):
            item = {"observed": item}
        if not isinstance(item, dict):
            item = {"observed": repr(item)}
        findings.append(
            Finding(
                title=item.get("title") or description,
                severity=Severity(item.get("severity", severity)),
                category=item.get("category", category),
                intent_excerpt=item.get("intent_excerpt", ""),
                observed=item.get("observed", ""),
                evidence=item.get("evidence", {}),
                repro_case_id=case_id,
                check_id=check_id,
                confidence=float(item.get("confidence", 1.0)),
            )
        )
    return findings
