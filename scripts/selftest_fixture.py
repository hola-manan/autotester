"""
Self-test: run the seeded-bug fixture through the real adapter + evaluator and
confirm the oracle suite catches every injected bug (buggy=True) while raising
nothing on the clean reference (buggy=False).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auto_tester.adapters.buggy_adapter import BuggyAdapter
from auto_tester.core.evaluator import evaluate, load_checks_module
from auto_tester.core.reporter import ReportContext, write_report

CHECKS = ROOT / "projects" / "buggy" / "checks_buggy.py"


def _sanitize(case):
    """For the clean reference, replace the deliberately-unparseable amount with
    a valid one — the clean pipeline correctly RAISES on bad input (that's the
    intended behavior), which is a separate concern from false positives."""
    for src in ("source_a", "source_b"):
        for rec in case.payload.get(src, []):
            if rec.get("amount") == "bad":
                rec["amount"] = "$3.00"
    return case


def run(buggy: bool):
    adapter = BuggyAdapter(buggy=buggy)
    registry = load_checks_module(CHECKS)
    cases = adapter.default_cases()
    for c in cases:
        c.payload["buggy"] = buggy
        if not buggy:
            _sanitize(c)
    runs = [adapter.run(c) for c in cases]
    results = evaluate(registry, runs, adapter)
    findings = [f for r in results for f in r.findings]
    return runs, results, findings


print("=" * 70)
print("BUGGY run — expect findings for each seeded bug")
print("=" * 70)
runs, results, findings = run(buggy=True)
out = runs[0].output
print(f"pipeline output: order_count={out['order_count']} total_revenue={out['total_revenue']}\n")
caught = sorted({f.check_id for f in findings})
for f in findings:
    print(f"  [{f.severity.value:8}] {f.check_id}: {f.observed}")
print(f"\nchecks that fired: {caught}")
errored = [r for r in results if r.passed is None]
for r in errored:
    print(f"  !! check {r.check_id} could not evaluate: {r.note.splitlines()[0]}")

print("\n" + "=" * 70)
print("CLEAN run — expect ZERO findings (no false positives)")
print("=" * 70)
cruns, cresults, cfindings = run(buggy=False)
cout = cruns[0].output
print(f"pipeline output: order_count={cout['order_count']} total_revenue={cout['total_revenue']}")
for f in cfindings:
    print(f"  [FALSE POSITIVE] {f.check_id}: {f.observed}")
print(f"false positives: {len(cfindings)}")

expected = {"no_duplicate_ids", "count_matches_orders", "revenue_reconciles",
            "fields_preserved", "no_silent_zero_amount"}
caught = set(caught)
missed = expected - caught
print("\n" + "=" * 70)
print(f"RESULT: caught {len(expected & caught)}/{len(expected)} seeded-bug checks; "
      f"{len(cfindings)} false positives")
if missed:
    print(f"  MISSED: {sorted(missed)}")
print("PASS" if not missed and not cfindings else "NEEDS REVIEW")

# Exercise the reporter on the buggy findings to produce a real artifact.
paths = write_report(
    findings,
    ReportContext(project="buggy", mode="deterministic-selftest", num_cases=len(runs), num_runs=len(runs)),
    ROOT / "projects" / "buggy" / "reports",
    cases=[r.case for r in runs],
)
print(f"\nreport written: {paths['markdown']}")
