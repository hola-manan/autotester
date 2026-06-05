"""
Regression test: the deterministic oracle suite must catch every seeded bug in
the fixture and raise nothing on the clean reference. This is the tester
testing itself — if these fail, the tool can't be trusted on real code.

Runs without any API key (deterministic spine only).
"""

import copy
from pathlib import Path

from auto_tester.adapters.buggy_adapter import BuggyAdapter
from auto_tester.core.evaluator import evaluate, load_checks_module

CHECKS = Path(__file__).resolve().parents[1] / "projects" / "buggy" / "checks_buggy.py"

SEEDED = {
    "no_duplicate_ids",
    "count_matches_orders",
    "revenue_reconciles",
    "fields_preserved",
    "no_silent_zero_amount",
}


def _run(buggy: bool):
    adapter = BuggyAdapter(buggy=buggy)
    registry = load_checks_module(CHECKS)
    cases = adapter.default_cases()
    for c in cases:
        c.payload["buggy"] = buggy
        if not buggy:  # clean pipeline correctly raises on bad input; use valid data
            for src in ("source_a", "source_b"):
                for rec in c.payload.get(src, []):
                    if rec.get("amount") == "bad":
                        rec["amount"] = "$3.00"
    runs = [adapter.run(c) for c in cases]
    results = evaluate(registry, runs, adapter)
    return [f for r in results for f in r.findings], results


def test_buggy_run_catches_all_seeded_bugs():
    findings, results = _run(buggy=True)
    fired = {f.check_id for f in findings}
    assert SEEDED.issubset(fired), f"missed: {SEEDED - fired}"
    # no check should silently error out
    errored = [r.check_id for r in results if r.passed is None]
    assert not errored, f"checks errored: {errored}"


def test_clean_run_has_no_false_positives():
    findings, _ = _run(buggy=False)
    assert findings == [], f"false positives: {[f.check_id for f in findings]}"
