"""
Command-line entry point.

    auto-tester list
    auto-tester template intent --out <project>      # scaffold .autotester/intent.md
    auto-tester onboard <project_path> [--name N]     # discover -> profile.json + intent.md
    auto-tester run --path <project_path> [--focus f.md]   # onboard-if-needed + run
    auto-tester run --project <name> [--mode both] [--focus f.md]

The ONLY required input is the project folder path. The tester reads the
project's docs + code to discover how to run it and what it should do, then
runs a full test session and writes a report under projects/<name>/reports/.

Optional steering files the project can maintain (auto-discovered):
    <project>/.autotester/intent.md   authoritative spec
    <project>/.autotester/focus.md    a specific feature/concern to check
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from .bootstrap import find_project_python, maybe_bootstrap
from .config import load_settings
from .core.models import Severity
from .discover import ProjectProfile, profile_project, read_project_focus
from .llm import LLM
from .orchestrator import SessionOptions, run_session
from .registry import available, get_spec, has_profile, inplace_spec
from .templates import TEMPLATES

_PROJECTS = Path(__file__).resolve().parents[2] / "projects"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _require_key(why: str = "this command") -> Optional[int]:
    if not load_settings().has_key:
        print(f"ERROR: GEMINI_API_KEY is not set, and {why} needs the LLM. "
              "Add it to .env (see .env.example).", file=sys.stderr)
        return 2
    return None


def _warn_partial_mode() -> None:
    """The key is missing but a degraded run is possible — say so LOUDLY."""
    bar = "!" * 72
    print(f"\n{bar}\n"
          "!! GEMINI_API_KEY is not set — running in PARTIAL mode.\n"
          "!! SKIPPED: code-scan, input generation, oracle/strategy generation,\n"
          "!!          and all LLM judging (most of this tool's power).\n"
          "!! RUNNING: pre-existing deterministic checks + Hypothesis fuzzing.\n"
          "!! The report will be marked PARTIAL — a clean result is NOT a pass.\n"
          f"{bar}\n", file=sys.stderr)


def _derive_name(path: Path, explicit: Optional[str]) -> str:
    return explicit or path.name.replace(" ", "_").lower()


def _do_onboard(root: Path, name: Optional[str]) -> str:
    """Discover and persist a profile; return the project name."""
    llm = LLM()
    print(f"Discovering project at {root} …")
    profile, intent_md = profile_project(llm, root, name=name)
    profile.python = find_project_python(root)
    project_dir = _PROJECTS / profile.name
    profile.save(project_dir)
    (project_dir / "intent.md").write_text(intent_md, encoding="utf-8")
    ep = profile.entrypoint
    print(f"  name           : {profile.name}")
    print(f"  entrypoint     : {ep.module}:{ep.qualname} ({ep.kind})")
    print(f"  params         : {[p.get('name') for p in ep.params]}")
    print(f"  instrument     : {len(profile.instrument_targets)} step(s)")
    print(f"  example inputs : {len(profile.example_cases)}")
    print(f"  project python : {profile.python or '(current interpreter)'}")
    print(f"  saved          : {project_dir / 'profile.json'}")
    return profile.name


def _load_focus(focus_path: Optional[str], root: Optional[Path]) -> str:
    if focus_path:
        try:
            return Path(focus_path).read_text(encoding="utf-8")
        except Exception as e:
            print(f"WARN: could not read --focus file: {e}", file=sys.stderr)
    if root:
        auto = read_project_focus(root)
        if auto:
            print("Using focus from .autotester/focus.md")
            return auto
    return ""


def _session_opts(args, focus: str) -> SessionOptions:
    return SessionOptions(
        mode=args.mode,
        num_generated=args.num,
        regenerate_checks=args.regenerate_checks,
        spot_check=not args.no_spot_check,
        final_judge=not args.no_final_judge,
        llm_judge_limit=args.judge_limit,
        focus=focus,
        fuzz_examples=0 if args.no_fuzz else args.fuzz,
    )


def _open_report(path: str) -> None:
    """Best-effort: pop the report open in the default viewer (Windows)."""
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        pass


def _run_and_report(spec, opts: SessionOptions, *, open_report: bool = False) -> int:
    settings = load_settings()
    focus_note = ", with focus" if opts.focus else ""
    print(f"Running '{spec.name}' (mode={opts.mode}, "
          f"model={settings.model_pro}/{settings.model_flash}{focus_note}) …")
    result = run_session(spec, LLM(settings), opts)
    by_sev = {}
    for f in result["findings"]:
        by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1
    print("\n=== Summary ===")
    for s in Severity:
        if by_sev.get(s.value):
            print(f"  {s.value:9}: {by_sev[s.value]}")
    print(f"  total    : {len(result['findings'])}")
    md = result["report"]["markdown"]
    print(f"\nReport: {md}")
    if open_report:
        _open_report(md)
    return 0


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def _cmd_list(_: argparse.Namespace) -> int:
    print("Available projects:")
    for name in available():
        kind = "(discovered)" if has_profile(name) else "(built-in)"
        print(f"  - {name} {kind}")
    return 0


def _cmd_template(args: argparse.Namespace) -> int:
    text = TEMPLATES.get(args.kind)
    if text is None:
        print(f"Unknown template '{args.kind}'. Choose: {', '.join(TEMPLATES)}", file=sys.stderr)
        return 2
    if args.out:
        d = Path(args.out) / ".autotester"
        d.mkdir(parents=True, exist_ok=True)
        ext = "yaml" if args.kind == "instrument" else "md"
        path = d / f"{args.kind}.{ext}"
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}")
    else:
        print(text)
    return 0


def _cmd_onboard(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2
    code = maybe_bootstrap(root, sys.argv[1:])
    if code is not None:
        return code
    err = _require_key("onboarding (project discovery)")
    if err:
        return err
    _do_onboard(root, args.name)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.path and not args.project:
        print("ERROR: provide --path <project_folder> or --project <name>.", file=sys.stderr)
        return 2

    # Resolve project root + interpreter, then hop into the project env if needed.
    root: Optional[Path] = None
    explicit_python = None
    if args.path:
        root = Path(args.path).resolve()
        if not root.is_dir():
            print(f"ERROR: not a directory: {root}", file=sys.stderr)
            return 2
    elif has_profile(args.project):
        profile = ProjectProfile.load(_PROJECTS / args.project)
        root = Path(profile.root)
        explicit_python = profile.python

    if root is not None:
        code = maybe_bootstrap(root, sys.argv[1:], explicit_python=explicit_python)
        if code is not None:
            return code

    # The key is mandatory only when discovery must run; with an existing
    # profile a keyless session still runs the deterministic spine, loudly.
    needs_onboard = bool(args.path) and (
        not has_profile(_derive_name(root, args.name)) or args.reonboard)
    if needs_onboard:
        err = _require_key("onboarding (project discovery)")
        if err:
            return err
    elif not load_settings().has_key:
        _warn_partial_mode()

    # Onboard on demand for --path.
    if args.path:
        name = _derive_name(root, args.name)
        if not has_profile(name) or args.reonboard:
            name = _do_onboard(root, args.name)
    else:
        name = args.project
        if not (args.project in available()):
            print(f"ERROR: unknown project '{args.project}'. Available: {', '.join(available())}",
                  file=sys.stderr)
            return 2

    focus = _load_focus(args.focus, root)
    return _run_and_report(get_spec(name), _session_opts(args, focus))


def _cmd_test(args: argparse.Namespace) -> int:
    """Test a project IN PLACE: config + report live in <project>/.autotester/."""
    root = Path(args.path or os.getcwd()).resolve()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2
    code = maybe_bootstrap(root, sys.argv[1:])
    if code is not None:
        return code
    needs_onboard = args.reonboard or not (root / ".autotester" / "profile.json").exists()
    if needs_onboard:
        err = _require_key("onboarding (project discovery)")
        if err:
            return err
    elif not load_settings().has_key:
        _warn_partial_mode()

    spec, did_onboard = inplace_spec(root, LLM(), regenerate=args.reonboard)
    if did_onboard:
        print(f"Onboarded '{spec.name}' -> {root / '.autotester' / 'profile.json'}")
    focus = _load_focus(args.focus, root)
    return _run_and_report(spec, _session_opts(args, focus), open_report=not args.no_open)


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reonboard", action="store_true",
                        help="re-discover even if a profile exists")
    parser.add_argument("--focus", help="path to a focus .md (else uses .autotester/focus.md)")
    parser.add_argument("--mode", default="both", choices=["input", "code", "both"])
    parser.add_argument("--num", type=int, default=12, help="number of inputs to generate")
    parser.add_argument("--regenerate-checks", action="store_true")
    parser.add_argument("--no-spot-check", action="store_true")
    parser.add_argument("--no-final-judge", action="store_true")
    parser.add_argument("--judge-limit", type=int, default=6, help="max runs to LLM-judge")
    parser.add_argument("--fuzz", type=int, default=25,
                        help="Hypothesis examples for the property-based fuzz stage")
    parser.add_argument("--no-fuzz", action="store_true", help="disable the fuzz stage")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autotester",
                                description="AI QA agent for multi-step pipelines.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list available projects").set_defaults(func=_cmd_list)

    t = sub.add_parser("template", help="print/scaffold an .autotester steering file")
    t.add_argument("kind", choices=list(TEMPLATES), help="which template")
    t.add_argument("--out", help="project folder to write .autotester/<kind>.md into")
    t.set_defaults(func=_cmd_template)

    o = sub.add_parser("onboard", help="discover a project from its folder path")
    o.add_argument("path", help="path to the project folder")
    o.add_argument("--name", help="override the project name")
    o.set_defaults(func=_cmd_onboard)

    # The simple one: test a project in place (defaults to the current folder).
    ts = sub.add_parser("test", help="test a project folder in place (report lands in it)")
    ts.add_argument("path", nargs="?", help="project folder (default: current directory)")
    ts.add_argument("--no-open", action="store_true", help="don't auto-open the report")
    _add_run_flags(ts)
    ts.set_defaults(func=_cmd_test)

    r = sub.add_parser("run", help="run a session (tester-managed projects dir)")
    src = r.add_mutually_exclusive_group()
    src.add_argument("--path", help="project folder (onboards on demand)")
    src.add_argument("--project", help=f"already-known project: {', '.join(available())}")
    r.add_argument("--name", help="name to use when onboarding via --path")
    _add_run_flags(r)
    r.set_defaults(func=_cmd_run)
    return p


def _force_utf8_output() -> None:
    """Avoid UnicodeEncodeError on Windows consoles (cp1252) for any output."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
