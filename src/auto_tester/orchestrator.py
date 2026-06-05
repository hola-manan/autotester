"""
Orchestrator — runs the full test session for one project and writes a report.

Flow (mode="both"):
    code_scan (white-box)  ->  hypotheses
    input_gen              ->  cases (default + generated + hypothesis-targeted)
    runner                 ->  runs (+ crash findings)
    oracle_gen / load      ->  deterministic check suite
    evaluator              ->  invariant + metamorphic findings
    llm_oracles            ->  spot-check + final-judge findings
    reporter               ->  findings.md / findings.json

``mode`` selects which halves run:
    input  -> input-based only (no code_scan)
    code   -> code_scan only (hypotheses reported; no generated inputs run)
    both   -> the full loop (default)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .code_scan import hypotheses_to_findings, scan_code
from .core.adapter import PipelineAdapter
from .core.evaluator import evaluate, load_checks_module
from .core.models import Case, Finding, RunResult
from .core.reporter import ReportContext, write_report
from .core.runner import run_and_collect
from .input_gen import generate_cases
from .llm import LLM
from .llm_oracles import final_judge, focused_check, spot_check
from .oracle_gen import generate_checks_module, write_checks_module


@dataclass
class ProjectSpec:
    name: str
    make_adapter: Callable[[], PipelineAdapter]
    project_dir: Path
    code_paths: Sequence[Path] = field(default_factory=list)

    @property
    def checks_path(self) -> Path:
        return self.project_dir / f"checks_{self.name}.py"

    @property
    def reports_dir(self) -> Path:
        return self.project_dir / "reports"


@dataclass
class SessionOptions:
    mode: str = "both"  # input | code | both
    num_generated: int = 12
    regenerate_checks: bool = False
    llm_judge_limit: int = 6  # cap LLM-judged runs to control cost
    metamorphic_base_limit: int = 3  # cap pipeline re-runs (gentle on live APIs)
    spot_check: bool = True
    final_judge: bool = True
    focus: str = ""  # a specific feature/concern to concentrate the whole session on


def run_session(
    spec: ProjectSpec,
    llm: LLM,
    opts: Optional[SessionOptions] = None,
) -> dict:
    opts = opts or SessionOptions()
    adapter = spec.make_adapter()
    intent = adapter.intent
    notes: List[str] = []
    all_findings: List[Finding] = []

    # 1) Code-based suspicion pass --------------------------------------- #
    if opts.focus:
        notes.append(f"Focus: {opts.focus.strip()[:200]}")

    hypotheses = []
    if opts.mode in ("code", "both") and spec.code_paths:
        try:
            hypotheses = scan_code(llm, intent, list(spec.code_paths), focus=opts.focus or None)
            all_findings += hypotheses_to_findings(hypotheses)
            notes.append(f"Code-scan produced {len(hypotheses)} hypotheses.")
        except Exception as e:
            notes.append(f"Code-scan failed ({e}); continuing without hypotheses.")

    if opts.mode == "code":
        ctx = ReportContext(project=spec.name, mode=opts.mode, num_cases=0, num_runs=0, notes=notes)
        paths = write_report(all_findings, ctx, spec.reports_dir)
        return {"findings": all_findings, "report": paths, "runs": []}

    # 2) Inputs: defaults + generated (+ hypothesis-targeted) ------------ #
    cases: List[Case] = list(adapter.default_cases())
    example_payloads = [c.payload for c in cases] or [{}]
    try:
        cases += generate_cases(
            llm, intent, example_payloads,
            n=opts.num_generated, hypotheses=hypotheses or None,
            focus=opts.focus or None,
        )
    except Exception as e:
        notes.append(f"Input generation failed ({e}); ran default cases only.")
    if not cases:
        notes.append("No cases to run.")

    # 3) Run -------------------------------------------------------------- #
    runs, crash_findings = run_and_collect(adapter, cases)
    all_findings += crash_findings

    # 4) Oracle suite: load existing or generate ------------------------- #
    registry = None
    if spec.checks_path.exists() and not opts.regenerate_checks:
        registry = load_checks_module(spec.checks_path)
        notes.append(f"Loaded existing checks from {spec.checks_path.name}.")
    else:
        sample = next((r for r in runs if r.output is not None), runs[0] if runs else None)
        if sample is not None:
            code_text = _read_code(spec.code_paths)
            try:
                source = generate_checks_module(llm, intent, sample, spec.name, code=code_text)
                write_checks_module(source, spec.project_dir, spec.name)
                registry = load_checks_module(spec.checks_path)
                notes.append(f"Generated checks suite ({len(registry.invariants)} invariants, "
                             f"{len(registry.metamorphic)} metamorphic).")
            except Exception as e:
                notes.append(f"Oracle generation failed: {e}")

    # 5) Deterministic evaluation ---------------------------------------- #
    if registry is not None:
        for cr in evaluate(registry, runs, adapter,
                           metamorphic_base_limit=opts.metamorphic_base_limit):
            all_findings += cr.findings

    # 6) LLM oracles (capped) -------------------------------------------- #
    judged = [r for r in runs if r.output is not None][: opts.llm_judge_limit]
    for r in judged:
        try:
            if opts.spot_check:
                all_findings += spot_check(llm, intent, r)
            if opts.final_judge:
                all_findings += final_judge(llm, intent, r)
            if opts.focus:
                all_findings += focused_check(llm, intent, opts.focus, r)
        except Exception as e:
            notes.append(f"LLM oracle failed on {r.case.id}: {e}")

    # 7) Report ----------------------------------------------------------- #
    ctx = ReportContext(project=spec.name, mode=opts.mode,
                        num_cases=len(cases), num_runs=len(runs), notes=notes)
    paths = write_report(all_findings, ctx, spec.reports_dir, cases=cases)
    return {"findings": all_findings, "report": paths, "runs": runs, "hypotheses": hypotheses}


def _read_code(paths: Sequence[Path]) -> Optional[str]:
    chunks = []
    for p in paths:
        try:
            chunks.append(f"# FILE {p}\n" + Path(p).read_text(encoding="utf-8", errors="replace")[:12000])
        except Exception:
            continue
    return "\n\n".join(chunks) if chunks else None
