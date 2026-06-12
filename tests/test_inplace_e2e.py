"""
End-to-end proof of the generic path: ``autotester test <folder>`` on a small
project the tester has never seen, with NO API key. The project carries the
``.autotester/`` convention files (profile, intent, checks, strategies), so the
session runs the deterministic spine — execute the real code, trace a step,
evaluate checks, fuzz — and writes a report marked PARTIAL into the project.
"""

import json
import sys
from pathlib import Path

from auto_tester.cli import main

PIPELINE = '''\
"""Sums a list of transaction amounts. BUG: negatives are silently dropped."""

def total(amounts):
    kept = [a for a in amounts if a >= 0]   # seeded bug: drops negatives
    return {"total": sum(kept), "count": len(amounts)}
'''

CHECKS = '''\
from auto_tester.core.checks import invariant

@invariant(id="total_includes_every_amount",
           description="total must equal the sum of ALL input amounts.",
           severity="critical", category="correctness")
def _(run):
    if not isinstance(run.output, dict):
        return None
    expected = sum(run.case.payload.get("amounts", []))
    got = run.output.get("total")
    if got != expected:
        return {"observed": f"total={got} but inputs sum to {expected}",
                "evidence": {"amounts": run.case.payload.get("amounts")}}
    return None
'''

STRATEGIES = '''\
from hypothesis import strategies as st

def payload_strategy():
    return st.fixed_dictionaries({
        "amounts": st.lists(st.integers(min_value=-100, max_value=100), max_size=6),
    })

SEEDS = [{"amounts": [-5, 10]}]
'''


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "sample_proj"
    at = root / ".autotester"
    at.mkdir(parents=True)
    (root / "sample_pipe.py").write_text(PIPELINE, encoding="utf-8")
    (at / "profile.json").write_text(json.dumps({
        "name": "sample_proj",
        "root": str(root),
        "src_roots": ["."],
        "entrypoint": {"module": "sample_pipe", "qualname": "total", "kind": "function",
                       "params": [{"name": "amounts", "example": [1, 2, 3], "required": True}]},
        "instrument_targets": [],
        "example_cases": [{"label": "simple", "payload": {"amounts": [1, 2, 3]}}],
    }), encoding="utf-8")
    (at / "intent.md").write_text("# Sum ALL amounts, including negatives.\n", encoding="utf-8")
    (at / "checks_sample_proj.py").write_text(CHECKS, encoding="utf-8")
    (at / "strategies_sample_proj.py").write_text(STRATEGIES, encoding="utf-8")
    return root


def test_cli_test_command_runs_generic_project_keyless(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    root = _make_project(tmp_path)

    code = main(["test", str(root), "--no-open", "--fuzz", "15"])
    assert code == 0

    err = capsys.readouterr().err
    assert "PARTIAL mode" in err, "missing the loud no-key banner"

    reports = list((root / ".autotester" / "reports").glob("*/findings.json"))
    assert reports, "no report was written into the project"
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["partial"] is True
    fired = {f["check_id"] for f in report["findings"]}
    # the seeded drop-negatives bug must be caught by check eval and/or fuzz
    assert any(c.endswith("total_includes_every_amount") for c in fired), fired
    # fuzz findings carry the shrunk minimal payload
    fuzz_findings = [f for f in report["findings"] if f["check_id"].startswith("hypothesis.")]
    if fuzz_findings:
        assert "minimal_payload" in fuzz_findings[0]["evidence"]
    # imported sample module shouldn't leak into other tests
    sys.modules.pop("sample_pipe", None)
