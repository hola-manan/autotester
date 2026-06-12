"""
Session-level resilience: a flaky LLM oracle must cost only its own call (never
the findings earlier oracles produced), and a keyless session must run the
deterministic spine LOUDLY marked as partial instead of silently passing.

Runs without any API key.
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from auto_tester import orchestrator
from auto_tester.adapters.buggy_adapter import BuggyAdapter
from auto_tester.core.models import Finding, Severity
from auto_tester.orchestrator import ProjectSpec, SessionOptions, run_session

FIXTURE = Path(__file__).resolve().parents[1] / "projects" / "buggy"


@dataclass
class _FakeSettings:
    has_key: bool = True


class _FakeLLM:
    settings = _FakeSettings()


def _spec(tmp_path: Path) -> ProjectSpec:
    project_dir = tmp_path / "buggy"
    project_dir.mkdir()
    shutil.copy(FIXTURE / "checks_buggy.py", project_dir / "checks_buggy.py")
    shutil.copy(FIXTURE / "strategies_buggy.py", project_dir / "strategies_buggy.py")
    return ProjectSpec(
        name="buggy",
        make_adapter=lambda: BuggyAdapter(buggy=True),
        project_dir=project_dir,
        code_paths=[],
    )


def _finding(title: str) -> Finding:
    return Finding(title=title, severity=Severity.HIGH, category="correctness",
                   intent_excerpt="", observed=title)


def test_failing_oracle_keeps_earlier_oracle_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "generate_cases", lambda *a, **k: [])
    monkeypatch.setattr(orchestrator, "get_rubric", lambda *a, **k: ["criterion 1"])
    monkeypatch.setattr(orchestrator, "spot_check",
                        lambda *a, **k: [_finding("from spot_check")])
    def _boom(*a, **k):
        raise RuntimeError("flaky judge call")
    monkeypatch.setattr(orchestrator, "final_judge", _boom)

    result = run_session(_spec(tmp_path), _FakeLLM(),
                         SessionOptions(mode="input", fuzz_examples=0))

    titles = {f.title for f in result["findings"]}
    assert "from spot_check" in titles, "spot_check findings were lost"
    report = json.loads(Path(result["report"]["json"]).read_text(encoding="utf-8"))
    assert any("final_judge failed" in n for n in report["notes"])
    # deterministic checks still contributed alongside the LLM oracles
    assert any(f.check_id == "count_matches_orders" for f in result["findings"])


def test_keyless_session_is_loudly_partial_not_silently_green(tmp_path):
    class _NoKeyLLM:
        settings = _FakeSettings(has_key=False)

    result = run_session(_spec(tmp_path), _NoKeyLLM(),
                         SessionOptions(mode="both", fuzz_examples=5))

    md = Path(result["report"]["markdown"]).read_text(encoding="utf-8")
    assert "PARTIAL RUN" in md
    report = json.loads(Path(result["report"]["json"]).read_text(encoding="utf-8"))
    assert report["partial"] is True
    # The deterministic spine still caught the seeded bugs.
    fired = {f.check_id for f in result["findings"]}
    assert "count_matches_orders" in fired
    # the checkpoint file is cleaned up after the final report is written
    out_dir = Path(result["report"]["markdown"]).parent
    assert not (out_dir / "findings_checkpoint.json").exists()


def test_checkpoint_survives_a_mid_session_crash(tmp_path, monkeypatch):
    """If a late stage hard-crashes, the checkpoint on disk still has the
    findings from earlier stages."""
    monkeypatch.setattr(orchestrator, "generate_cases", lambda *a, **k: [])
    monkeypatch.setattr(orchestrator, "get_rubric", lambda *a, **k: None)

    def _hard_crash(*a, **k):
        raise KeyboardInterrupt  # not caught by per-oracle isolation

    monkeypatch.setattr(orchestrator, "spot_check", _hard_crash)
    spec = _spec(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        run_session(spec, _FakeLLM(), SessionOptions(mode="input", fuzz_examples=0))

    checkpoints = list(spec.reports_dir.glob("*/findings_checkpoint.json"))
    assert checkpoints, "no checkpoint was persisted before the crash"
    saved = json.loads(checkpoints[0].read_text(encoding="utf-8"))
    assert any(f["check_id"] == "count_matches_orders" for f in saved)
