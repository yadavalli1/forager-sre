"""Tests for safety/ops hardening: LiteLLM routing, timeout, webhook auth, concurrency."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from forager.adapters import llm
from forager.adapters.llm import LLMResponse

# ── LiteLLM routing ───────────────────────────────────────────────────────────


def test_unknown_model_without_litellm_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "litellm", None)  # force ImportError
    with pytest.raises(ValueError, match="Unknown model"):
        llm.call("mystery-model-9000", [{"role": "user", "content": "hi"}])


def test_unknown_model_routes_via_litellm(monkeypatch):
    fake_msg = SimpleNamespace(content="ROOT CAUSE: x", tool_calls=None)
    fake_resp = SimpleNamespace(choices=[SimpleNamespace(message=fake_msg)])
    fake_litellm = MagicMock()
    fake_litellm.completion.return_value = fake_resp
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    resp = llm.call("bedrock/anthropic.claude-3-5-sonnet", [{"role": "user", "content": "hi"}])

    assert resp.stop_reason == "end_turn"
    assert resp.text == "ROOT CAUSE: x"
    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "bedrock/anthropic.claude-3-5-sonnet"
    assert any(t["function"]["name"] == "query_metrics" for t in kwargs["tools"])


def test_litellm_parses_tool_calls(monkeypatch):
    tc = SimpleNamespace(
        id="tc_1",
        function=SimpleNamespace(name="query_metrics", arguments='{"query": "up"}'),
    )
    fake_msg = SimpleNamespace(content=None, tool_calls=[tc])
    fake_resp = SimpleNamespace(choices=[SimpleNamespace(message=fake_msg)])
    fake_litellm = MagicMock()
    fake_litellm.completion.return_value = fake_resp
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    resp = llm.call("ollama/llama3", [{"role": "user", "content": "hi"}])

    assert resp.stop_reason == "tool_use"
    assert resp.tool_calls[0].name == "query_metrics"
    assert resp.tool_calls[0].input == {"query": "up"}


# ── investigation timeout ─────────────────────────────────────────────────────


def test_investigation_times_out(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import forager.agent as agent_mod

    tool_resp = LLMResponse(
        stop_reason="tool_use",
        text="",
        tool_calls=[],
        raw_content=[],
    )
    with patch("forager.agent.llm.call", return_value=tool_resp):
        inv = agent_mod.investigate("INC-TO", "api", "SlowAlert", max_seconds=0)

    assert "TIMED OUT" in inv.conclusion


def test_investigation_finishes_within_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import forager.agent as agent_mod

    done = LLMResponse(stop_reason="end_turn", text="ROOT CAUSE: fine")
    with patch("forager.agent.llm.call", return_value=done):
        inv = agent_mod.investigate("INC-OK", "api", "Alert", max_seconds=300)

    assert inv.conclusion == "ROOT CAUSE: fine"


# ── webhook auth ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    import forager.store as store_mod

    store_mod._db = None
    store_mod._db_path = tmp_path / "test.db"
    store_mod.init(store_mod._db_path)
    from forager.server import app

    return TestClient(app)


_PAYLOAD = {
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "authfp01",
            "labels": {"alertname": "A", "service": "s"},
            "annotations": {},
        }
    ]
}


def test_webhook_rejected_without_token(client, monkeypatch):
    monkeypatch.setenv("FORAGER_WEBHOOK_TOKEN", "s3cret")
    resp = client.post("/webhook/alertmanager", json=_PAYLOAD)
    assert resp.status_code == 401


def test_webhook_accepted_with_token(client, monkeypatch):
    monkeypatch.setenv("FORAGER_WEBHOOK_TOKEN", "s3cret")
    from forager.agent import Investigation

    inv = Investigation(incident_id="INC-AUTH", service="s", alert="A")
    inv.conclusion = "ok"
    with patch("forager.agent.investigate", return_value=inv):
        resp = client.post("/webhook/alertmanager", json=_PAYLOAD, headers={"X-Forager-Token": "s3cret"})
    assert resp.status_code == 200
    assert resp.json()["processed"] == 1


def test_webhook_open_when_no_token_configured(client, monkeypatch):
    monkeypatch.delenv("FORAGER_WEBHOOK_TOKEN", raising=False)
    from forager.agent import Investigation

    inv = Investigation(incident_id="INC-OPEN", service="s", alert="A")
    inv.conclusion = "ok"
    with patch("forager.agent.investigate", return_value=inv):
        resp = client.post("/webhook/alertmanager", json=_PAYLOAD)
    assert resp.status_code == 200


# ── batch handling ────────────────────────────────────────────────────────────


def test_duplicate_fingerprints_in_one_batch_run_once(client, monkeypatch):
    monkeypatch.delenv("FORAGER_WEBHOOK_TOKEN", raising=False)
    from forager.agent import Investigation

    inv = Investigation(incident_id="INC-B", service="s", alert="A")
    inv.conclusion = "ok"
    alert = {
        "status": "firing",
        "fingerprint": "batchfp",
        "labels": {"alertname": "A", "service": "s"},
        "annotations": {},
    }
    with patch("forager.agent.investigate", return_value=inv) as mock_inv:
        resp = client.post("/webhook/alertmanager", json={"alerts": [alert, alert]})
    assert resp.status_code == 200
    assert mock_inv.call_count == 1
