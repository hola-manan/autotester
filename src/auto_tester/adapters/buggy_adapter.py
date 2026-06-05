"""
Adapter for the seeded-bug ``buggy_pipeline`` fixture.

Demonstrates the in-process / decorator tracing path: the fixture's functions
are already ``@step``-decorated, so ``instrument_targets`` is empty and we just
call the pipeline in ``invoke``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from ..core.adapter import PipelineAdapter
from ..core.models import Case
from ..fixtures import buggy_pipeline as bp

_INTENT = (Path(__file__).resolve().parents[3] / "projects" / "buggy" / "intent.md")


class BuggyAdapter(PipelineAdapter):
    name = "buggy"
    instrument_targets = ()  # fixture is already decorated

    def __init__(self, buggy: bool = True):
        self.buggy = buggy
        try:
            self.intent = _INTENT.read_text(encoding="utf-8")
        except Exception:
            self.intent = bp.__doc__ or ""

    def invoke(self, case: Case) -> Any:
        payload = case.payload
        buggy = payload.get("buggy", self.buggy)
        return bp.run_pipeline(payload["source_a"], payload["source_b"], buggy=buggy)

    def default_cases(self) -> List[Case]:
        return [
            Case(
                label="mixed-sources-with-dups-hold-and-bad-amount",
                origin="default",
                rationale="exercises dedup, a held order, a swapped field, and an unparseable amount",
                payload={
                    "source_a": [
                        {"id": 1, "customer": "Acme", "region": "north", "status": "ok", "amount": "$10.00"},
                        {"id": 2, "customer": "Beta", "region": "east", "status": "hold", "amount": "$5.50"},
                        {"id": 3, "customer": "Cee", "region": "south", "status": "ok", "amount": "$7.25"},
                    ],
                    "source_b": [
                        {"id": 4, "customer": "Dee", "region": "west", "status": "ok", "amount": "bad"},
                        {"id": 1, "customer": "Acme", "region": "north", "status": "ok", "amount": "$10.00"},
                    ],
                },
            )
        ]
