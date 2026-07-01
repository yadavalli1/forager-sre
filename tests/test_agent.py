"""Tests for the core investigation loop."""

from unittest.mock import MagicMock, patch

from forager.adapters.llm import LLMResponse, ToolCall


def _tool_call(name: str, inp: dict, id: str = "tc_001") -> ToolCall:
    return ToolCall(id=id, name=name, input=inp)


def _end_turn_response(text: str) -> LLMResponse:
    return LLMResponse(stop_reason="end_turn", text=text, tool_calls=[], raw_content=[])


def _tool_use_response(tool_calls: list[ToolCall]) -> LLMResponse:
    blocks = []
    for tc in tool_calls:
        b = MagicMock()
        b.type = "tool_use"
        b.id = tc.id
        b.name = tc.name
        b.input = tc.input
        blocks.append(b)
    return LLMResponse(stop_reason="tool_use", text="", tool_calls=tool_calls, raw_content=blocks)


def test_investigate_immediate_conclusion(tmp_path, monkeypatch):
    """LLM answers immediately without calling any tools."""
    monkeypatch.chdir(tmp_path)

    conclusion = "ROOT CAUSE: connection pool exhaustion on db-primary"
    with patch("forager.adapters.llm.call", return_value=_end_turn_response(conclusion)):
        with patch("forager.adapters.slack.post", return_value={"status": "skipped"}):
            from forager import agent

            inv = agent.investigate("INC-001", "api", "High latency")

    assert inv.incident_id == "INC-001"
    assert inv.service == "api"
    assert inv.conclusion == conclusion
    assert inv.findings == []


def test_investigate_with_tool_calls(tmp_path, monkeypatch):
    """LLM calls two tools then returns conclusion."""
    monkeypatch.chdir(tmp_path)

    tc1 = _tool_call("query_metrics", {"query": "rate(http_errors[5m])"}, "tc_001")
    tc2 = _tool_call("get_pod_status", {"namespace": "default", "selector": "app=api"}, "tc_002")
    conclusion = "ROOT CAUSE: OOMKilled pods causing request failures"

    call_responses = [
        _tool_use_response([tc1, tc2]),
        _end_turn_response(conclusion),
    ]
    prom_result = {"status": "ok", "results": [{"labels": {}, "value": "0.42"}]}
    k8s_result = {
        "status": "ok",
        "pods": [{"name": "api-abc", "phase": "Running", "restarts": 5, "ready": True, "node": "node-1"}],
    }

    with patch("forager.adapters.llm.call", side_effect=call_responses):
        with patch("forager.adapters.prometheus.query", return_value=prom_result):
            with patch("forager.adapters.kubernetes.pod_status", return_value=k8s_result):
                with patch("forager.adapters.slack.post", return_value={"status": "skipped"}):
                    from importlib import reload

                    import forager.agent as agent_mod

                    reload(agent_mod)
                    inv = agent_mod.investigate("INC-002", "api", "Pod OOMKilled")

    assert inv.conclusion == conclusion
    assert len(inv.findings) == 2
    assert inv.findings[0].tool == "query_metrics"
    assert inv.findings[1].tool == "get_pod_status"


def test_investigate_unknown_tool_returns_error(tmp_path, monkeypatch):
    """Unknown tool name returns error dict without crashing."""
    monkeypatch.chdir(tmp_path)
    import forager.agent as agent_mod

    result = agent_mod._execute_tool("nonexistent_tool", {}, MagicMock())
    assert result["status"] == "error"
    assert "Unknown tool" in result["error"]


def test_investigate_posts_to_slack(tmp_path, monkeypatch):
    """When Slack token is set, investigation result is posted."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL", "#sre")

    conclusion = "ROOT CAUSE: disk saturation on db-primary"
    with patch("forager.adapters.llm.call", return_value=_end_turn_response(conclusion)):
        with patch(
            "forager.adapters.slack.post", return_value={"status": "ok", "ts": "999.000"}
        ) as mock_post:
            import forager.agent as agent_mod

            inv = agent_mod.investigate("INC-003", "db", "Disk full")

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][1] == "#sre"  # channel arg
    assert inv.slack_ts == "999.000"


def test_evidence_lines_only_ok_findings(tmp_path, monkeypatch):
    """evidence_lines() only includes findings with status=ok."""
    monkeypatch.chdir(tmp_path)
    import forager.agent as agent_mod

    inv = agent_mod.Investigation("INC-X", "svc", "alert")
    inv.findings = [
        agent_mod.Finding("query_metrics", {"query": "up"}, {"status": "ok", "results": []}),
        agent_mod.Finding(
            "get_pod_status",
            {"namespace": "x", "selector": "a=b"},
            {"status": "error", "error": "no kubeconfig"},
        ),
    ]
    lines = inv.evidence_lines()
    assert len(lines) == 1
    assert "query_metrics" in lines[0]
