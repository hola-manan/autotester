"""
GenericAdapter — runs any project from a discovered :class:`ProjectProfile`,
with no project-specific Python.

It adds the project's source roots to ``sys.path``, imports the discovered
entrypoint, and calls it with the case payload mapped to the entrypoint's
parameters. ``instrument_targets`` come straight from the profile, so inner
steps are traced exactly as with a hand-written adapter.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, List

from ..core.adapter import PipelineAdapter
from ..core.models import Case
from ..discover import ProjectProfile


class GenericAdapter(PipelineAdapter):
    def __init__(self, profile: ProjectProfile, intent: str = ""):
        self.profile = profile
        self.name = profile.name
        self.intent = intent
        self.instrument_targets = tuple(profile.instrument_targets)
        self._callable = None
        self._param_names = [p["name"] for p in (profile.entrypoint.params or [])
                             if isinstance(p, dict) and "name" in p]

    def _ensure_path(self) -> None:
        root = Path(self.profile.root)
        for sr in self.profile.src_roots:
            p = str((root / sr).resolve())
            if p not in sys.path and Path(p).exists():
                sys.path.insert(0, p)

    def _resolve_callable(self):
        if self._callable is not None:
            return self._callable
        self._ensure_path()
        ep = self.profile.entrypoint
        obj: Any = importlib.import_module(ep.module)
        for attr in ep.qualname.split("."):
            obj = getattr(obj, attr)
        self._callable = obj
        return obj

    def invoke(self, case: Case) -> Any:
        fn = self._resolve_callable()
        payload = case.payload
        # Map the payload to the entrypoint params; pass only recognized names so
        # generated inputs with extra keys don't raise TypeError.
        if self._param_names:
            kwargs = {k: payload[k] for k in self._param_names if k in payload}
        else:
            kwargs = dict(payload)
        return fn(**kwargs)

    def default_cases(self) -> List[Case]:
        cases: List[Case] = []
        for ex in self.profile.example_cases:
            payload = ex.get("payload", ex) if isinstance(ex, dict) else {}
            cases.append(Case(payload=payload, label=str(ex.get("label", "")),
                              origin="default", rationale="discovered example"))
        if not cases:  # fall back to the entrypoint's example values
            payload = {p["name"]: p.get("example") for p in (self.profile.entrypoint.params or [])
                       if isinstance(p, dict) and "name" in p}
            cases.append(Case(payload=payload, label="from-entrypoint-examples", origin="default"))
        return cases
