"""Tests for YAML runbook matching, exclusion rules, and prompt injection."""

from unittest.mock import patch

from forager import runbooks
from forager.adapters.llm import LLMResponse


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_no_directory_returns_empty(tmp_path):
    assert runbooks.load_matching("Anything", "svc", str(tmp_path / "missing")) == []


def test_alert_pattern_match(tmp_path):
    _write(tmp_path, "a.yaml", "match:\n  alerts: ['HighErrorRate']\nnotes: check the pool")
    matched = runbooks.load_matching("HighErrorRate", "api", str(tmp_path))
    assert len(matched) == 1
    assert matched[0].notes == "check the pool"


def test_alert_glob_and_case_insensitive(tmp_path):
    _write(tmp_path, "a.yaml", "match:\n  alerts: ['high*']\nnotes: n")
    assert len(runbooks.load_matching("HighLatency", "api", str(tmp_path))) == 1
    assert runbooks.load_matching("LowDisk", "api", str(tmp_path)) == []


def test_service_filter(tmp_path):
    _write(tmp_path, "a.yaml", "match:\n  alerts: ['*']\n  services: ['api']\nnotes: n")
    assert len(runbooks.load_matching("Any", "api", str(tmp_path))) == 1
    assert runbooks.load_matching("Any", "worker", str(tmp_path)) == []


def test_no_match_block_matches_everything(tmp_path):
    _write(tmp_path, "a.yaml", "notes: global guidance")
    assert len(runbooks.load_matching("Whatever", "anything", str(tmp_path))) == 1


def test_exclude_tools_parsed(tmp_path):
    _write(tmp_path, "a.yaml", "exclude_tools: ['get_pod_logs']\nnotes: n")
    rb = runbooks.load_matching("X", "y", str(tmp_path))[0]
    assert rb.exclude_tools == ["get_pod_logs"]


def test_malformed_yaml_skipped(tmp_path):
    _write(tmp_path, "bad.yaml", "{{ not yaml ::")
    _write(tmp_path, "good.yaml", "notes: fine")
    matched = runbooks.load_matching("X", "y", str(tmp_path))
    assert len(matched) == 1
    assert matched[0].notes == "fine"


# ── agent integration ─────────────────────────────────────────────────────────


def test_agent_injects_runbook_and_filters_tools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rb_dir = tmp_path / "runbooks"
    rb_dir.mkdir()
    (rb_dir / "r.yaml").write_text(
        "match:\n  alerts: ['HighErrorRate']\n"
        "exclude_tools: ['get_pod_logs']\n"
        "notes: check pg_pool_available first"
    )

    import forager.agent as agent_mod

    captured = {}

    def fake_call(model, messages, system="", max_retries=3, tools=None):
        captured["system"] = system
        captured["tools"] = tools
        return LLMResponse(stop_reason="end_turn", text="ROOT CAUSE: x")

    with patch("forager.agent.llm.call", side_effect=fake_call):
        agent_mod.investigate("INC-RB", "api", "HighErrorRate")

    assert "check pg_pool_available first" in captured["system"]
    tool_names = [t["name"] for t in captured["tools"]]
    assert "get_pod_logs" not in tool_names
    assert "query_metrics" in tool_names


def test_agent_without_runbooks_uses_all_tools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import forager.agent as agent_mod
    from forager.adapters import llm as llm_mod

    captured = {}

    def fake_call(model, messages, system="", max_retries=3, tools=None):
        captured["tools"] = tools
        return LLMResponse(stop_reason="end_turn", text="done")

    with patch("forager.agent.llm.call", side_effect=fake_call):
        agent_mod.investigate("INC-NORB", "api", "SomeAlert")

    assert [t["name"] for t in captured["tools"]] == [t["name"] for t in llm_mod.TOOLS]
