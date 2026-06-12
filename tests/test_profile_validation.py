"""
Profile/discovery hardening: malformed LLM output and malformed profile.json
must fail with actionable messages (never a bare KeyError), env-var roots must
resolve or fail loudly, and the GenericAdapter must tolerate params=None.

Runs without any API key.
"""

import pytest

from auto_tester.adapters.generic_adapter import GenericAdapter
from auto_tester.discover import Entrypoint, ProjectProfile, profile_project


class _StubLLM:
    """LLM stub whose .json returns a canned profile."""

    def __init__(self, raw):
        self.raw = raw

    def json(self, *a, **k):
        return self.raw


def _project(tmp_path):
    (tmp_path / "app.py").write_text("def run(x):\n    return x\n", encoding="utf-8")
    return tmp_path


def test_discovery_rejects_missing_entrypoint_with_clear_message(tmp_path):
    llm = _StubLLM({"name": "x", "entrypoint": {"module": "app"}})  # qualname missing
    with pytest.raises(ValueError, match="missing the entrypoint"):
        profile_project(llm, _project(tmp_path))


def test_discovery_rejects_non_dict_response(tmp_path):
    with pytest.raises(ValueError, match="Discovery failed"):
        profile_project(_StubLLM(["not", "a", "profile"]), _project(tmp_path))


def test_from_dict_rejects_incomplete_entrypoint():
    with pytest.raises(ValueError, match="incomplete entrypoint"):
        ProjectProfile.from_dict({"name": "x", "root": "/tmp", "entrypoint": {}})


def test_from_dict_fails_loudly_on_unset_env_var(monkeypatch):
    monkeypatch.delenv("NOPE_NOT_SET", raising=False)
    with pytest.raises(EnvironmentError, match="NOT set"):
        ProjectProfile.from_dict({
            "name": "x", "root": "${NOPE_NOT_SET}",
            "entrypoint": {"module": "m", "qualname": "f"},
        })


def test_from_dict_expands_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_PROJ", str(tmp_path))
    p = ProjectProfile.from_dict({
        "name": "x", "root": "${MY_PROJ}",
        "entrypoint": {"module": "m", "qualname": "f"},
    })
    assert p.root == str(tmp_path)


def test_generic_adapter_tolerates_none_params():
    profile = ProjectProfile(
        name="x", root="/tmp", src_roots=["."],
        entrypoint=Entrypoint(module="m", qualname="f", params=None),
    )
    adapter = GenericAdapter(profile)
    assert adapter._param_names == []
    # falls back to passing the payload through unchanged
    case = adapter.default_cases()[0]
    assert case.payload == {}
