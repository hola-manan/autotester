"""
Orchestrator — runs the full test session for one project and writes a report.

Flow (mode="both"):
    code_scan (white-box)  ->  hypotheses
    input_gen              ->  cases (default + generated + hypothesis-targeted)
    runner                 ->  runs (+ crash findings)
    oracle_gen / load      ->  deterministic check suite
    evaluator              ->  invariant + metamorphic findings
    hypothesis fuzz        ->  property-based runs with shrunk minimal repros
    llm_oracles            ->  rubric-based spot-check + final-judge findings
    reporter               ->  findings.md / findings.json

``mode`` selects which halves run:
    input  -> input-based only (no code_scan)
    code   -> code_scan only (hypotheses reported; no generated inputs run)
    both   -> the full loop (default)

Degradation is LOUD, never silent: with no API key the session still runs the
deterministic spine (existing checks + fuzzing) but the report is marked
PARTIAL. Findings are persisted to disk incrementally so a flaky LLM call late
in the session can never wipe what earlier stages already found.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
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
from .rubric import get_rubric


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
    def strategies_path(self) -> Path:
        return self.project_dir / f"strategies_{self.name}.py"

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
    fuzz_examples: int = 25  # Hypothesis examples per session (0 disables fuzzing)


def run_session(
    spec: ProjectSpec,
    llm: Optional[LLM],
    opts: Optional[SessionOptions] = None,
) -> dict:
    opts = opts or SessionOptions()
    adapter = spec.make_adapter()
    intent = adapter.intent
    notes: List[str] = []
    all_findings: List[Finding] = []

    llm_ok = llm is not None and llm.settings.has_key
    if not llm_ok:
        notes.append("PARTIAL RUN — GEMINI_API_KEY is not set: code-scan, input "
                     "generation, oracle/strategy generation, and LLM judging were "
                     "SKIPPED. Only pre-existing deterministic checks and fuzzing ran.")

    # Report folder is created up front so every stage can checkpoint findings
    # into it — a crash or flaky LLM call late in the session loses nothing.
    out_dir = _timestamped_dir(spec.reports_dir)

    # 1) Code-based suspicion pass --------------------------------------- #
    if opts.focus:
        notes.append(f"Focus: {opts.focus.strip()[:200]}")

    hypotheses = []
    if opts.mode in ("code", "both"):
        if not spec.code_paths:
            notes.append("WARNING: no source files resolved for the code-scan — "
                         "white-box hypotheses were skipped. Check the profile's "
                         "root/src_roots/instrument_targets.")
        elif llm_ok:
            try:
                hypotheses = scan_code(llm, intent, list(spec.code_paths), focus=opts.focus or None)
                # In mode "code" the hypotheses ARE the report; in "both" they only
                # steer input generation and fuzz seeds — a suspicion becomes a
                # finding by being confirmed at runtime, never by reading alone.
                if opts.mode == "code":
                    all_findings += hypotheses_to_findings(hypotheses)
                notes.append(f"Code-scan produced {len(hypotheses)} hypotheses "
                             + ("(reported)." if opts.mode == "code"
                                else "(steering inputs/seeds; runtime-confirmed only)."))
            except Exception as e:
                notes.append(f"Code-scan failed ({e}); continuing without hypotheses.")

    if opts.mode == "code":
        ctx = _ctx(spec, opts, notes, llm_ok, num_cases=0, num_runs=0)
        paths = write_report(all_findings, ctx, out_dir)
        return {"findings": all_findings, "report": paths, "runs": []}

    # 2) Inputs: defaults + generated (+ hypothesis-targeted) ------------ #
    cases: List[Case] = list(adapter.default_cases())
    example_payloads = [c.payload for c in cases] or [{}]
    if llm_ok:
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
    _checkpoint(out_dir, all_findings)

    # 4) Oracle suite: load existing or generate ------------------------- #
    registry = None
    if spec.checks_path.exists() and not opts.regenerate_checks:
        registry = load_checks_module(spec.checks_path)
        notes.append(f"Loaded existing checks from {spec.checks_path.name}.")
    elif llm_ok:
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
    else:
        notes.append("No checks module exists and none can be generated without a key — "
                     "deterministic checks were skipped.")

    # 5) Deterministic evaluation ---------------------------------------- #
    if registry is not None:
        for cr in evaluate(registry, runs, adapter,
                           metamorphic_base_limit=opts.metamorphic_base_limit):
            all_findings += cr.findings
        _checkpoint(out_dir, all_findings)

    # 5b) Property-based fuzz (Hypothesis): run the REAL pipeline on drawn
    # inputs, assert no-crash + invariants, shrink failures to minimal repros.
    if opts.fuzz_examples > 0:
        fuzz_findings, fuzz_note = _fuzz_stage(spec, adapter, registry, llm if llm_ok else None,
                                               intent, example_payloads, hypotheses, opts)
        all_findings += fuzz_findings
        if fuzz_note:
            notes.append(fuzz_note)
        _checkpoint(out_dir, all_findings)

    # 6) LLM oracles (capped) — chained so each ADDS to the run's findings -- #
    # Order is granular -> broad (steps, then whole output, then focus); each
    # call sees what's already been found for this run and reports only NEW
    # issues. Each oracle is isolated: one flaky call costs only that call,
    # never the findings the previous oracles already produced.
    if llm_ok:
        rubric = None
        try:
            rubric = get_rubric(llm, intent, spec.project_dir,
                                regenerate=opts.regenerate_checks)
            notes.append(f"Judging against a {len(rubric)}-criterion rubric (rubric.json).")
        except Exception as e:
            notes.append(f"Rubric extraction failed ({e}); judging from raw intent.")

        judged = [r for r in runs if r.output is not None][: opts.llm_judge_limit]
        for r in judged:
            run_findings: List[Finding] = []
            if opts.spot_check:
                try:
                    run_findings += spot_check(llm, intent, r, prior=run_findings, rubric=rubric)
                except Exception as e:
                    notes.append(f"spot_check failed on {r.case.id} ({e}); kept earlier findings.")
            if opts.final_judge:
                try:
                    run_findings += final_judge(llm, intent, r, prior=run_findings, rubric=rubric)
                except Exception as e:
                    notes.append(f"final_judge failed on {r.case.id} ({e}); kept earlier findings.")
            if opts.focus:
                try:
                    run_findings += focused_check(llm, intent, opts.focus, r,
                                                  prior=run_findings, rubric=rubric)
                except Exception as e:
                    notes.append(f"focused_check failed on {r.case.id} ({e}); kept earlier findings.")
            all_findings += run_findings
            _checkpoint(out_dir, all_findings)

    # 7) Report (timestamped subfolder — never overwrites a previous run) -- #
    ctx = _ctx(spec, opts, notes, llm_ok, num_cases=len(cases), num_runs=len(runs))
    paths = write_report(all_findings, ctx, out_dir, cases=cases)
    (out_dir / "findings_checkpoint.json").unlink(missing_ok=True)
    return {"findings": all_findings, "report": paths, "runs": runs, "hypotheses": hypotheses}


def _fuzz_stage(spec, adapter, registry, llm, intent, example_payloads, hypotheses, opts):
    """Load or generate the strategies module, then fuzz. Returns (findings, note)."""
    from .hypothesis_runner import fuzz, load_strategies_module
    from .strategy_gen import generate_strategies_module, write_strategies_module

    module = None
    if spec.strategies_path.exists() and not opts.regenerate_checks:
        try:
            module = load_strategies_module(spec.strategies_path)
        except Exception as e:
            return [], f"Fuzz skipped: existing {spec.strategies_path.name} failed to load ({e})."
    elif llm is not None:
        try:
            source = generate_strategies_module(llm, intent, example_payloads, spec.name,
                                                hypotheses=hypotheses or None)
            write_strategies_module(source, spec.project_dir, spec.name)
            module = load_strategies_module(spec.strategies_path)
        except Exception as e:
            return [], f"Fuzz skipped: strategy generation failed ({e})."
    else:
        return [], ("Fuzz skipped: no strategies module exists and none can be "
                    "generated without a key.")

    try:
        findings = fuzz(adapter, registry, module,
                        max_examples=opts.fuzz_examples,
                        database_dir=spec.project_dir / ".hypothesis")
    except Exception as e:
        return [], f"Fuzz stage failed ({e})."
    return findings, (f"Hypothesis fuzz: {opts.fuzz_examples} examples, "
                      f"{len(findings)} finding(s) (minimal repros attached).")


def _ctx(spec, opts, notes, llm_ok, *, num_cases: int, num_runs: int) -> ReportContext:
    return ReportContext(project=spec.name, mode=opts.mode, num_cases=num_cases,
                         num_runs=num_runs, notes=notes, partial=not llm_ok)


def _checkpoint(out_dir: Path, findings: List[Finding]) -> None:
    """Persist everything found so far; later-stage failures lose nothing."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "findings_checkpoint.json").write_text(
            json.dumps([f.to_dict() for f in findings], indent=2, default=lambda o: repr(o)),
            encoding="utf-8")
    except Exception:
        pass  # checkpointing must never kill the session


def _timestamped_dir(reports_dir: Path) -> Path:
    """A per-run report folder named by date + time (HH:MM), uniquified if two
    runs land in the same minute so nothing is overwritten."""
    base = reports_dir / datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = base
    n = 2
    while out.exists():
        out = reports_dir / f"{base.name}-{n}"
        n += 1
    return out


def _read_code(paths: Sequence[Path]) -> Optional[str]:
    chunks = []
    for p in paths:
        try:
            chunks.append(f"# FILE {p}\n" + Path(p).read_text(encoding="utf-8", errors="replace")[:12000])
        except Exception:
            continue
    return "\n\n".join(chunks) if chunks else None
