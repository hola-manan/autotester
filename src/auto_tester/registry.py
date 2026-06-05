"""
Project registry — resolves a project name to a ProjectSpec.

Two kinds of projects:
  * built-ins (``buggy``, ``jeevn``) with hand-written adapters, and
  * **discovered** projects: any folder under ``projects/<name>/`` that has a
    ``profile.json`` (produced by ``auto-tester onboard <path>``), run via the
    config-driven GenericAdapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from .discover import ProjectProfile
from .orchestrator import ProjectSpec

_ROOT = Path(__file__).resolve().parents[2]
_PROJECTS = _ROOT / "projects"

# jeevn source (sibling repo) for the hand-written demo spec's code-scan.
_JEEVN_SRC = Path(r"C:\Users\manan\OneDrive2\Desktop\ashi\src\jeevn")
_JEEVN_CODE = [
    _JEEVN_SRC / "application" / "advisory_service.py",
    _JEEVN_SRC / "domain" / "fertilizer" / "requirements.py",
    _JEEVN_SRC / "domain" / "fertilizer" / "schedule.py",
    _JEEVN_SRC / "domain" / "irrigation" / "et0.py",
    _JEEVN_SRC / "domain" / "soil" / "management.py",
    _JEEVN_SRC / "domain" / "pest_disease_weed" / "assessment.py",
    _JEEVN_SRC / "domain" / "growth_yield" / "projection.py",
    _JEEVN_SRC / "infrastructure" / "pseudo_satellite.py",
]


def _buggy_spec() -> ProjectSpec:
    from .adapters.buggy_adapter import BuggyAdapter
    return ProjectSpec(
        name="buggy",
        make_adapter=lambda: BuggyAdapter(buggy=True),
        project_dir=_PROJECTS / "buggy",
        code_paths=[_ROOT / "src" / "auto_tester" / "fixtures" / "buggy_pipeline.py"],
    )


def _jeevn_spec() -> ProjectSpec:
    from .adapters.jeevn_adapter import JeevnAdapter
    return ProjectSpec(
        name="jeevn",
        make_adapter=JeevnAdapter,
        project_dir=_PROJECTS / "jeevn",
        code_paths=[p for p in _JEEVN_CODE if p.exists()],
    )


_BUILTINS = {"buggy": _buggy_spec, "jeevn": _jeevn_spec}


def _module_to_file(profile: ProjectProfile, dotted: str) -> Path | None:
    root = Path(profile.root)
    rel = dotted.replace(".", "/")
    for sr in profile.src_roots:
        for cand in (root / sr / f"{rel}.py", root / sr / rel / "__init__.py"):
            if cand.exists():
                return cand
    return None


def _discovered_spec(name: str) -> ProjectSpec:
    """Build a ProjectSpec from a saved profile.json + intent.md."""
    from .adapters.generic_adapter import GenericAdapter

    project_dir = _PROJECTS / name
    profile = ProjectProfile.load(project_dir)
    intent = ""
    intent_path = project_dir / "intent.md"
    if intent_path.exists():
        intent = intent_path.read_text(encoding="utf-8")

    # Code-scan reads the entrypoint module + the instrumented step files.
    dotted_modules = {profile.entrypoint.module}
    for t in profile.instrument_targets:
        dotted_modules.add(t.split(":")[0])
    code_paths = [p for p in (_module_to_file(profile, m) for m in dotted_modules) if p]

    return ProjectSpec(
        name=name,
        make_adapter=lambda: GenericAdapter(profile, intent=intent),
        project_dir=project_dir,
        code_paths=code_paths,
    )


def available() -> List[str]:
    names = set(_BUILTINS)
    if _PROJECTS.exists():
        for p in _PROJECTS.glob("*/profile.json"):
            names.add(p.parent.name)
    return sorted(names)


def has_profile(name: str) -> bool:
    return (_PROJECTS / name / "profile.json").exists()


def get_spec(name: str) -> ProjectSpec:
    if name in _BUILTINS:
        return _BUILTINS[name]()
    if has_profile(name):
        return _discovered_spec(name)
    raise KeyError(f"Unknown project '{name}'. Available: {', '.join(available())}")


def inplace_spec(root, llm, regenerate: bool = False):
    """Build a ProjectSpec whose artifacts live in ``<root>/.autotester/``.

    This is the "test in place" path: profile.json, the generated checks, and
    the report all live inside the project being tested, so results appear right
    next to the code. Onboards (discovers) on demand if no profile exists yet.
    Returns ``(spec, did_onboard)``.
    """
    from .adapters.generic_adapter import GenericAdapter
    from .bootstrap import find_project_python
    from .discover import profile_project

    root = Path(root).resolve()
    at = root / ".autotester"
    at.mkdir(parents=True, exist_ok=True)

    did_onboard = False
    if (at / "profile.json").exists() and not regenerate:
        profile = ProjectProfile.load(at)
    else:
        profile, intent_md = profile_project(llm, root)
        profile.python = find_project_python(root)
        profile.save(at)
        # Don't clobber a user-maintained intent.md; only write if absent.
        if not (at / "intent.md").exists():
            (at / "intent.md").write_text(intent_md, encoding="utf-8")
        did_onboard = True

    intent = ""
    if (at / "intent.md").exists():
        intent = (at / "intent.md").read_text(encoding="utf-8")

    dotted = {profile.entrypoint.module} | {t.split(":")[0] for t in profile.instrument_targets}
    code_paths = [p for p in (_module_to_file(profile, m) for m in dotted) if p]

    spec = ProjectSpec(
        name=profile.name,
        make_adapter=lambda: GenericAdapter(profile, intent=intent),
        project_dir=at,
        code_paths=code_paths,
    )
    return spec, did_onboard
