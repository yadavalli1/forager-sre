"""Tests for the one-call SRE agent (free-form query triage)."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from forager.adapters.llm import LLMResponse, ToolCall


def _end_turn_response(text: str) -> LLMResponse:
    return LLMResponse(stop_reason="end_turn", text=text, tool_calls=[], raw_content=[])


def _tool_use_response(tool_calls: list[ToolCall]) -> LLMResponse:
    return LLMResponse(stop_reason="tool_use", text="", tool_calls=tool_calls, raw_content=[{"type": "tool_use"}])


# ── agent.onecall ────────────────────────────────────────────────────────────

def test_onecall_immediate_conclusion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conclusion = "ROOT CAUSE: connection pool exhaustion on db-primary\nEVIDENCE:\n- n/a\nREMEDIATION:\n- bump pool size"

    with patch("forager.adapters.llm.call", return_value=_end_turn_response(conclusion)):
        with patch("forager.adapters.slack.post", return_value={"status": "skipped"}):
            from forager import agent
            inv = agent.onecall("checkout-api is throwing 5xx since 10 min")

    assert inv.incident_id.startswith("OC-")
    assert inv.description == "checkout-api is throwing 5xx since 10 min"
    assert inv.conclusion == conclusion
    assert inv.findings == []


def test_onecall_discovers_firing_alerts_via_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    tc = ToolCall(id="tc_001", name="list_firing_alerts", input={})
    conclusion = "ROOT CAUSE: OOMKilled pods causing request failures"

    am_result = {
        "status": "ok",
        "count": 1,
        "alerts": [{"fingerprint": "fp1", "alertname": "HighErrorRate", "service": "api", "severity": "critical", "summary": "", "description": ""}],
    }

    with patch("forager.adapters.llm.call", side_effect=[_tool_use_response([tc]), _end_turn_response(conclusion)]):
        with patch("forager.adapters.alertmanager.list_firing_alerts", return_value=am_result) as mock_am:
            with patch("forager.adapters.slack.post", return_value={"status": "skipped"}):
                from forager import agent
                inv = agent.onecall("something is wrong with the api")

    assert inv.conclusion == conclusion
    assert len(inv.findings) == 1
    assert inv.findings[0].tool == "list_firing_alerts"
    assert inv.findings[0].result["status"] == "ok"
    mock_am.assert_called_once()


def test_onecall_posts_to_slack(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL", "#sre")

    with patch("forager.adapters.llm.call", return_value=_end_turn_response("ROOT CAUSE: disk saturation")):
        with patch("forager.adapters.slack.post", return_value={"status": "ok", "ts": "111.222"}) as mock_post:
            from forager import agent
            inv = agent.onecall("disk full on db")

    mock_post.assert_called_once()
    assert inv.slack_ts == "111.222"


def test_onecall_executes_query_metrics_after_discovery(tmp_path, monkeypatch):
    """After discovering alerts, the agent should be able to call other tools."""
    monkeypatch.chdir(tmp_path)

    tc1 = ToolCall(id="tc_001", name="list_firing_alerts", input={})
    tc2 = ToolCall(id="tc_002", name="query_metrics", input={"query": "rate(http_errors[5m])"})
    conclusion = "ROOT CAUSE: error spike confirmed"

    am_result = {"status": "ok", "count": 1, "alerts": [{"alertname": "Err", "service": "api", "severity": "warn", "summary": "", "description": "", "fingerprint": "f"}]}
    prom_result = {"status": "ok", "results": [{"labels": {}, "value": "0.42"}]}

    with patch("forager.adapters.llm.call", side_effect=[_tool_use_response([tc1]), _tool_use_response([tc2]), _end_turn_response(conclusion)]):
        with patch("forager.adapters.alertmanager.list_firing_alerts", return_value=am_result):
            with patch("forager.adapters.prometheus.query", return_value=prom_result):
                with patch("forager.adapters.slack.post", return_value={"status": "skipped"}):
                    from forager import agent
                    inv = agent.onecall("api seems off")

    assert inv.conclusion == conclusion
    assert len(inv.findings) == 2
    assert inv.findings[0].tool == "list_firing_alerts"
    assert inv.findings[1].tool == "query_metrics"


# ── HTTP endpoint POST /agent/onecall ────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    import forager.store as store_mod
    store_mod._db = None
    store_mod._db_path = tmp_path / "test.db"
    store_mod.init(store_mod._db_path)
    from forager.server import app
    return TestClient(app)


def test_onecall_endpoint_returns_investigation(client):
    from forager.agent import Investigation
    from datetime import datetime, timezone
    inv = Investigation(incident_id="OC-TEST1", service="api", alert="q", description="api failing")
    inv.conclusion = "ROOT CAUSE: pool exhaustion"
    inv.slack_ts = ""

    with patch("forager.agent.onecall", return_value=inv) as mock_oc:
        resp = client.post("/agent/onecall", json={"query": "api failing"})

    assert resp.status_code == 200
    mock_oc.assert_called_once_with("api failing")
    data = resp.json()
    assert data["incident_id"] == "OC-TEST1"
    assert data["conclusion"] == "ROOT CAUSE: pool exhaustion"
    assert data["query"] == "api failing"
    assert data["status"] == "ok"


def test_onecall_endpoint_rejects_empty_query(client):
    resp = client.post("/agent/onecall", json={"query": "   "})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"]


def test_onecall_endpoint_rejects_missing_query(client):
    resp = client.post("/agent/onecall", json={})
    assert resp.status_code == 400


def test_onecall_endpoint_persists_to_store(client):
    from forager.agent import Investigation
    inv = Investigation(incident_id="OC-PERSIST", service="api", alert="q", description="disk full")
    inv.conclusion = "ROOT CAUSE: disk full"

    with patch("forager.agent.onecall", return_value=inv):
        client.post("/agent/onecall", json={"query": "disk full"})

    record = client.get("/investigations/OC-PERSIST")
    assert record.status_code == 200
    assert record.json()["id"] == "OC-PERSIST"