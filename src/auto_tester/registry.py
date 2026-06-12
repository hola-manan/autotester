"""
Project registry — resolves a project name to a ProjectSpec.

Two kinds of projects:
  * the ``buggy`` self-test fixture (hand-written adapter), and
  * **discovered** projects: any folder under ``projects/<name>/`` that has a
    ``profile.json`` (produced by ``auto-tester onboard <path>`` or checked in
    by hand, like ``jeevn``), run via the config-driven GenericAdapter.

A checked-in profile may use ``${ENV_VAR}`` in its ``root`` so it works on any
machine (jeevn uses ``${JEEVN_SRC}``); resolution fails loudly if the variable
is unset rather than silently scanning nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from .discover import ProjectProfile, read_project_instruments
from .orchestrator import ProjectSpec

_ROOT = Path(__file__).resolve().parents[2]
_PROJECTS = _ROOT / "projects"


def _buggy_spec() -> ProjectSpec:
    from .adapters.buggy_adapter import BuggyAdapter
    return ProjectSpec(
        name="buggy",
        make_adapter=lambda: BuggyAdapter(buggy=True),
        project_dir=_PROJECTS / "buggy",
        code_paths=[_ROOT / "src" / "auto_tester" / "fixtures" / "buggy_pipeline.py"],
    )


_BUILTINS = {"buggy": _buggy_spec}


def _module_to_file(profile: ProjectProfile, dotted: str) -> Path | None:
    root = Path(profile.root)
    rel = dotted.replace(".", "/")
    for sr in profile.src_roots:
        for cand in (root / sr / f"{rel}.py", root / sr / rel / "__init__.py"):
            if cand.exists():
                return cand
    return None


def _code_paths(profile: ProjectProfile) -> List[Path]:
    """Files for the white-box code-scan: the entrypoint + every traced step."""
    dotted = {profile.entrypoint.module} | {t.split(":")[0] for t in profile.instrument_targets}
    paths = [p for p in (_module_to_file(profile, m) for m in sorted(dotted)) if p]
    if dotted and not paths:
        print(f"WARN: none of {len(dotted)} source module(s) found under "
              f"{profile.root} — the code-scan will have nothing to read. "
              "Check the profile's root/src_roots.", file=sys.stderr)
    return paths


def _check_root(profile: ProjectProfile, name: str) -> None:
    root = Path(profile.root)
    if not root.is_dir():
        raise FileNotFoundError(
            f"Project root for '{name}' does not exist: {root}. "
            "Fix the profile's root (or the environment variable it references)."
        )


def _discovered_spec(name: str) -> ProjectSpec:
    """Build a ProjectSpec from a saved profile.json + intent.md."""
    from .adapters.generic_adapter import GenericAdapter

    project_dir = _PROJECTS / name
    profile = ProjectProfile.load(project_dir)
    _check_root(profile, name)
    intent = ""
    intent_path = project_dir / "intent.md"
    if intent_path.exists():
        intent = intent_path.read_text(encoding="utf-8")

    return ProjectSpec(
        name=name,
        make_adapter=lambda: GenericAdapter(profile, intent=intent),
        project_dir=project_dir,
        code_paths=_code_paths(profile),
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

    # The user-maintained instrument list always wins, even after onboarding.
    user_targets = read_project_instruments(root)
    if user_targets:
        profile.instrument_targets = user_targets

    _check_root(profile, profile.name)
    intent = ""
    if (at / "intent.md").exists():
        intent = (at / "intent.md").read_text(encoding="utf-8")

    spec = ProjectSpec(
        name=profile.name,
        make_adapter=lambda: GenericAdapter(profile, intent=intent),
        project_dir=at,
        code_paths=_code_paths(profile),
    )
    return spec, did_onboard
