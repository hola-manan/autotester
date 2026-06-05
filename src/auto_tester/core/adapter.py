"""
The Adapter interface — the only project-specific glue the tester needs.

An adapter teaches the generic core three things about a target pipeline:
  * what it *should* do          -> ``intent`` (plain English),
  * how to run it on an input    -> ``run(case)`` returning a RunResult,
  * which inner steps to watch    -> ``instrument_targets`` (dotted paths).

The default ``run`` already wires tracing + timing + crash capture around a
single ``invoke`` method, so most adapters only implement ``invoke`` (call the
HTTP endpoint / function) and declare ``instrument_targets``.
"""

from __future__ import annotations

import time
import traceback
from abc import ABC, abstractmethod
from typing import Any, List, Sequence

from .models import Case, RunResult
from .tracer import instrument, trace_context


class PipelineAdapter(ABC):
    name: str = "pipeline"

    # Plain-English description of what the pipeline (and key steps) should do.
    # This is the oracle's source of truth; usually loaded from intent.md.
    intent: str = ""

    # Dotted paths to monkeypatch-trace, e.g.
    # "jeevn.domain.irrigation.et0:IrrigationCalculator.calculate_et0_hargreaves_samani"
    instrument_targets: Sequence[str] = ()

    @abstractmethod
    def invoke(self, case: Case) -> Any:
        """Run the target pipeline for one Case and return its final output.

        Tracing of inner steps is handled by ``run`` — just call the pipeline
        (HTTP request, function call, subprocess) and return whatever it gives
        back (e.g. parsed JSON response).
        """

    def default_cases(self) -> List[Case]:
        """Optional seed inputs the input generator mutates from."""
        return []

    def collect_logs(self, case: Case) -> str:
        """Optional: return any log/artifact text produced by the run."""
        return ""

    # -- the generic execution wrapper ----------------------------------- #
    def run(self, case: Case) -> RunResult:
        """Execute one Case with step tracing, timing, and crash capture."""
        result = RunResult(case=case)
        start = time.perf_counter()
        with trace_context() as trace:
            with instrument(self.instrument_targets):
                try:
                    result.output = self.invoke(case)
                except Exception:
                    result.error = traceback.format_exc()
        result.trace = trace
        result.duration_ms = (time.perf_counter() - start) * 1000.0
        try:
            result.logs = self.collect_logs(case)
        except Exception:
            result.logs = ""
        return result
