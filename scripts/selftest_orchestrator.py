"""
End-to-end orchestrator wiring test (no API key needed).

With no GEMINI_API_KEY, the LLM-backed steps (code-scan, input-gen, oracle-gen,
spot-check, judge) degrade gracefully and are skipped, while the deterministic
spine (default cases -> run -> existing checks -> evaluate -> report) still
produces a full report. This proves the orchestrator wiring is sound; with a
key, the same path additionally runs the LLM oracles.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_tester.llm import LLM
from auto_tester.orchestrator import SessionOptions, run_session
from auto_tester.registry import get_spec

spec = get_spec("buggy")
llm = LLM()  # no key -> LLM calls will raise and be caught by the orchestrator
result = run_session(spec, llm, SessionOptions(mode="both"))

findings = result["findings"]
print(f"runs: {len(result['runs'])}   findings: {len(findings)}")
caught = sorted({f.check_id for f in findings})
print("checks fired:", caught)
print("report:", result["report"]["markdown"])

# The deterministic invariants must still have fired despite no LLM.
expected = {"count_matches_orders", "revenue_reconciles", "fields_preserved",
            "no_silent_zero_amount"}
print("PASS" if expected.issubset(set(caught)) else f"MISSING {expected - set(caught)}")
