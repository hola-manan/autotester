"""
Auto-discovery: turn a project PATH into a runnable test profile, with no
hand-written adapter.

Given a project directory (a working codebase that ships an overview doc like
jeevn's ARCHITECTURE.md), this:
  1. gathers the overview/README docs,
  2. builds a precise *code map* via AST (modules -> functions/classes/methods
     with signatures) so the LLM picks real, callable entrypoints — not
     hallucinated ones,
  3. asks Gemini for a ProjectProfile: the intent, the single entrypoint to run
     the core process, its parameters with example values, the inner steps worth
     tracing, and a few example inputs.

The profile is saved as ``projects/<name>/profile.json`` + ``intent.md`` so it
is reviewable, editable, and reused across runs.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm import LLM

_DOC_NAMES = ("readme", "architecture", "overview", "design", "quickstart", "about")
AUTOTESTER_DIR = ".autotester"


def read_project_intent(root: str | Path) -> Optional[str]:
    """Authoritative intent the user maintains in the project, if present.

    Looked for at ``.autotester/intent.md`` (preferred) or ``AUTOTEST.md``.
    When present it is treated as the source of truth, dramatically improving
    accuracy over intent inferred from code alone.
    """
    root = Path(root)
    for cand in (root / AUTOTESTER_DIR / "intent.md", root / "AUTOTEST.md"):
        if cand.exists():
            try:
                return cand.read_text(encoding="utf-8")
            except Exception:
                continue
    return None


def read_project_focus(root: str | Path) -> Optional[str]:
    """A specific feature/concern to check, maintained at ``.autotester/focus.md``."""
    root = Path(root)
    cand = root / AUTOTESTER_DIR / "focus.md"
    if cand.exists():
        try:
            return cand.read_text(encoding="utf-8")
        except Exception:
            return None
    return None
_SKIP_DIRS = {".git", ".venv", "venv", ".venv312", "__pycache__", "node_modules",
              ".miniforge3", ".conda-gdal", ".py312embed", "build", "dist",
              ".pytest_cache", "tests", "test", "data", "infra", "docs_build"}
_MAX_DOC = 16000
_MAX_MAP = 24000


# --------------------------------------------------------------------------- #
# Profile model
# --------------------------------------------------------------------------- #
@dataclass
class Entrypoint:
    module: str                  # dotted module, e.g. "jeevn.application.advisory_service"
    qualname: str                # "AgriculturalReportGenerator.generate_report" or "run"
    kind: str = "function"       # function | staticmethod | classmethod | instancemethod
    params: List[Dict[str, Any]] = field(default_factory=list)  # [{name, example, required}]


@dataclass
class ProjectProfile:
    name: str
    root: str                              # absolute project root
    src_roots: List[str]                   # dirs (relative to root) to add to sys.path
    entrypoint: Entrypoint
    instrument_targets: List[str] = field(default_factory=list)
    example_cases: List[Dict[str, Any]] = field(default_factory=list)  # [{label,payload}]
    python: Optional[str] = None           # the project's venv interpreter, if found
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ProjectProfile":
        ep = d.get("entrypoint", {})
        return ProjectProfile(
            name=d["name"], root=d["root"], src_roots=d.get("src_roots", []),
            entrypoint=Entrypoint(**ep), instrument_targets=d.get("instrument_targets", []),
            example_cases=d.get("example_cases", []), python=d.get("python"),
            notes=d.get("notes", ""),
        )

    def save(self, project_dir: Path) -> Path:
        project_dir = Path(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "profile.json").write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return project_dir / "profile.json"

    @staticmethod
    def load(project_dir: Path) -> "ProjectProfile":
        return ProjectProfile.from_dict(
            json.loads((Path(project_dir) / "profile.json").read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# Gathering docs + code map
# --------------------------------------------------------------------------- #
def gather_docs(root: Path) -> str:
    chunks: List[str] = []
    budget = _MAX_DOC
    # top-level + docs/ markdown, prioritizing overview-like names
    candidates = list(root.glob("*.md")) + list(root.glob("*.txt")) + list((root / "docs").glob("*.md"))
    def _rank(p: Path) -> int:
        n = p.name.lower()
        return min((i for i, k in enumerate(_DOC_NAMES) if k in n), default=len(_DOC_NAMES))
    for p in sorted(set(candidates), key=_rank):
        if budget <= 0:
            break
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        text = text[: min(len(text), budget)]
        budget -= len(text)
        chunks.append(f"# ===== DOC: {p.relative_to(root)} =====\n{text}")
    return "\n\n".join(chunks)


def detect_src_roots(root: Path) -> List[str]:
    """Common Python layouts: prefer ./src, else the dir holding top packages."""
    if (root / "src").is_dir():
        return ["src"]
    return ["."]


def _module_name(py: Path, src_root: Path) -> str:
    rel = py.relative_to(src_root).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def _sig(args: ast.arguments) -> List[str]:
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    return [n for n in names if n not in ("self", "cls")]


def build_code_map(root: Path, src_roots: List[str]) -> str:
    """AST summary of public functions/classes/methods with their parameter names."""
    lines: List[str] = []
    budget = _MAX_MAP
    for sr in src_roots:
        src_root = (root / sr).resolve()
        if not src_root.is_dir():
            continue
        for py in sorted(src_root.rglob("*.py")):
            if any(part in _SKIP_DIRS for part in py.parts):
                continue
            if budget <= 0:
                break
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            mod = _module_name(py, src_root)
            entries: List[str] = []
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    entries.append(f"  def {node.name}({', '.join(_sig(node.args))})")
                elif isinstance(node, ast.ClassDef):
                    methods = []
                    for sub in node.body:
                        if isinstance(sub, ast.FunctionDef) and not sub.name.startswith("_"):
                            deco = {d.id for d in sub.decorator_list if isinstance(d, ast.Name)}
                            kind = ("staticmethod" if "staticmethod" in deco
                                    else "classmethod" if "classmethod" in deco else "method")
                            methods.append(f"    {kind} {sub.name}({', '.join(_sig(sub.args))})")
                    head = f"  class {node.name}"
                    entries.append(head + ("\n" + "\n".join(methods) if methods else ""))
            if entries:
                block = f"# module: {mod}  ({py.relative_to(root)})\n" + "\n".join(entries)
                budget -= len(block)
                lines.append(block)
    return "\n\n".join(lines)


# --------------------------------------------------------------------------- #
# LLM profiling
# --------------------------------------------------------------------------- #
_SYSTEM = (
    "You analyze a working Python codebase and produce a machine-readable test "
    "profile. You pick a REAL, importable entrypoint that runs the project's "
    "core process end-to-end and returns a result — preferring a module-level "
    "function or a @staticmethod/@classmethod that needs NO object construction. "
    "You only reference modules/functions that appear in the provided code map."
)


def _prompt(docs: str, code_map: str, authoritative_intent: Optional[str]) -> str:
    head = []
    if authoritative_intent:
        head = [
            "# AUTHORITATIVE intent (maintained by the project owner — treat as truth)",
            authoritative_intent.strip(),
            "",
            "Use the above as the definitive intent. Echo it back (lightly cleaned) as"
            " intent_markdown; do not contradict it. Still derive the entrypoint,"
            " instrument_targets, and example_cases from the code map below.",
            "",
        ]
    return "\n".join(head + [
        "# Project overview docs",
        docs or "(none found)",
        "",
        "# Code map (real modules/functions/methods with parameter names)",
        code_map,
        "",
        "# Task: produce the test profile as JSON with EXACTLY these keys:",
        "{",
        '  "name": short snake_case project name,',
        '  "intent_markdown": a thorough plain-English description of what the project'
        " SHOULD do end-to-end and the correctness expectations a tester should check"
        " (ranges, disclosure of fabricated/defaulted data, inputs flowing to outputs,"
        " internal consistency). Derive it from the docs + code.,",
        '  "src_roots": [dirs to add to sys.path, e.g. ["src"]],',
        '  "entrypoint": {"module": dotted, "qualname": "Func" or "Class.method",'
        ' "kind": "function|staticmethod|classmethod", "params": [{"name","example","required"}]},',
        '  "instrument_targets": ["module:qualname", ...]  // the inner step functions'
        " (domain/business logic) worth tracing; class methods are safest,",
        '  "example_cases": [{"label": str, "payload": {param_name: value, ...}}]  //'
        " 2-3 valid inputs whose keys match the entrypoint params exactly",
        "}",
        "",
        "Rules: the entrypoint must be runnable as module.qualname(**payload) with no"
        " object construction. payload keys MUST be the entrypoint parameter names."
        " Choose 6-12 instrument_targets that are the meaningful computation steps.",
    ])


def profile_project(llm: LLM, project_root: str | Path, name: Optional[str] = None) -> ProjectProfile:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")
    docs = gather_docs(root)
    authoritative_intent = read_project_intent(root)
    src_roots = detect_src_roots(root)
    code_map = build_code_map(root, src_roots)
    if not code_map.strip():
        raise ValueError(f"No Python modules found under {root} (src_roots={src_roots}).")

    raw = llm.json(_prompt(docs, code_map, authoritative_intent), tier="pro",
                   system=_SYSTEM, temperature=0.2)
    ep = raw.get("entrypoint", {})
    profile = ProjectProfile(
        name=name or raw.get("name") or root.name.replace(" ", "_").lower(),
        root=str(root),
        src_roots=raw.get("src_roots") or src_roots,
        entrypoint=Entrypoint(
            module=ep["module"], qualname=ep["qualname"],
            kind=ep.get("kind", "function"), params=ep.get("params", []),
        ),
        instrument_targets=raw.get("instrument_targets", []),
        example_cases=raw.get("example_cases", []),
        notes=raw.get("notes", ""),
    )
    # Authoritative file wins over any LLM-rephrased intent.
    intent_md = authoritative_intent or raw.get("intent_markdown", "")
    return profile, intent_md
