"""
The fuzz stage testing itself: Hypothesis must falsify at least one seeded bug
in the buggy fixture (with a shrunk, minimal reproducing payload attached) and
must raise nothing on the clean reference.

Runs without any API key (deterministic spine only).
"""

from pathlib import Path

from auto_tester.adapters.buggy_adapter import BuggyAdapter
from auto_tester.core.evaluator import load_checks_module
from auto_tester.hypothesis_runner import fuzz, load_strategies_module

PROJECT = Path(__file__).resolve().parents[1] / "projects" / "buggy"


def _fuzz(buggy: bool, max_examples: int = 40):
    adapter = BuggyAdapter(buggy=buggy)
    registry = load_checks_module(PROJECT / "checks_buggy.py")
    strategies = load_strategies_module(PROJECT / "strategies_buggy.py")
    # database_dir=None -> derandomized, so this test is deterministic.
    return fuzz(adapter, registry, strategies, max_examples=max_examples, database_dir=None)


def test_fuzz_falsifies_a_seeded_bug_with_minimal_repro():
    findings = _fuzz(buggy=True)
    assert findings, "fuzzing the buggy pipeline found nothing"
    f = findings[0]
    assert f.check_id.startswith("hypothesis."), f.check_id
    minimal = f.evidence.get("minimal_payload")
    assert isinstance(minimal, dict) and {"source_a", "source_b"} <= set(minimal)
    # Shrinking must have reduced the input to very few records.
    n_records = len(minimal["source_a"]) + len(minimal["source_b"])
    assert n_records <= 3, f"expected a shrunk repro, got {n_records} records: {minimal}"


def test_fuzz_clean_pipeline_has_no_false_positives():
    assert _fuzz(buggy=False) == []
