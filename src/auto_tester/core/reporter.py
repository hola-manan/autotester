"""
Reporter — turns Findings into a report that reads like a human QA's bug notes:
grouped by severity, each with what was expected, what happened, the concrete
evidence, and how to reproduce it.

Emits both ``findings.md`` (for you to read) and ``findings.json`` (for tooling
/ diffing across runs).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .models import Finding, Severity, dumps

_SEV_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪", "info": "ℹ️"}


@dataclass
class ReportContext:
    project: str
    mode: str = "both"
    num_cases: int = 0
    num_runs: int = 0
    notes: List[str] = field(default_factory=list)
    partial: bool = False  # LLM checks were skipped (e.g. no API key)


def _group(findings: List[Finding]) -> List[Dict[str, Any]]:
    """Collapse identical findings (same check + title) across cases."""
    buckets: Dict[tuple, List[Finding]] = defaultdict(list)
    for f in findings:
        buckets[(f.check_id, f.title, f.severity.value)].append(f)
    groups = []
    for (check_id, title, sev), items in buckets.items():
        rep = items[0]
        groups.append(
            {
                "check_id": check_id,
                "title": title,
                "severity": sev,
                "category": rep.category,
                "intent_excerpt": rep.intent_excerpt,
                "observed": rep.observed,
                "evidence": rep.evidence,
                "confidence": min(f.confidence for f in items),
                "count": len(items),
                "repro_case_ids": [f.repro_case_id for f in items if f.repro_case_id][:5],
            }
        )
    groups.sort(key=lambda g: (Severity(g["severity"]).rank, -g["count"]))
    return groups


def render_markdown(findings: List[Finding], ctx: ReportContext) -> str:
    groups = _group(findings)
    lines: List[str] = []
    lines.append(f"# Auto-Tester report — `{ctx.project}`\n")
    if ctx.partial:
        lines.append("> ⚠️ **PARTIAL RUN — LLM checks were disabled** (no GEMINI_API_KEY). "
                     "Code-scan, generated inputs, and LLM judging did NOT run; only "
                     "deterministic checks and fuzzing did. A clean result here is NOT "
                     "a full pass.\n")
    counts = {s.value: 0 for s in _SEV_ORDER}
    for g in groups:
        counts[g["severity"]] += 1
    summary = "  ".join(f"{_EMOJI[s.value]} {counts[s.value]} {s.value}" for s in _SEV_ORDER if counts[s.value])
    lines.append(f"**{len(groups)} distinct issue(s)** across {ctx.num_runs} runs "
                 f"({ctx.num_cases} input cases, mode=`{ctx.mode}`).\n")
    if summary:
        lines.append(summary + "\n")
    for note in ctx.notes:
        lines.append(f"> {note}\n")

    if not groups:
        lines.append("\n✅ No discrepancies found.\n")
        return "\n".join(lines)

    current_sev = None
    for g in groups:
        if g["severity"] != current_sev:
            current_sev = g["severity"]
            lines.append(f"\n## {_EMOJI[current_sev]} {current_sev.upper()}\n")
        times = f" ×{g['count']}" if g["count"] > 1 else ""
        conf = "" if g["confidence"] >= 0.999 else f"  _(confidence {g['confidence']:.0%})_"
        lines.append(f"### {g['title']}{times}{conf}")
        lines.append(f"*check:* `{g['check_id']}`  ·  *category:* {g['category']}")
        if g["intent_excerpt"]:
            lines.append(f"\n**Intent:** {g['intent_excerpt']}")
        lines.append(f"\n**Observed:** {g['observed']}")
        if g["evidence"]:
            ev = dumps(g["evidence"])
            if len(ev) > 1500:
                ev = ev[:1500] + "\n  ...(truncated)"
            lines.append(f"\n**Evidence:**\n```json\n{ev}\n```")
        if g["repro_case_ids"]:
            lines.append(f"\n**Reproduce with case(s):** {', '.join(g['repro_case_ids'])}")
        lines.append("")
    return "\n".join(lines)


def write_report(
    findings: List[Finding],
    ctx: ReportContext,
    out_dir: str | Path,
    cases: List[Any] | None = None,
) -> Dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(findings, ctx)
    md_path = out_dir / "findings.md"
    md_path.write_text(md, encoding="utf-8")

    payload = {
        "project": ctx.project,
        "partial": ctx.partial,
        "mode": ctx.mode,
        "num_cases": ctx.num_cases,
        "num_runs": ctx.num_runs,
        "notes": ctx.notes,
        "findings": [f.to_dict() for f in findings],
        "grouped": _group(findings),
    }
    if cases is not None:
        payload["cases"] = [c.to_dict() for c in cases]
    json_path = out_dir / "findings.json"
    json_path.write_text(json.dumps(payload, indent=2, default=lambda o: repr(o)), encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}
