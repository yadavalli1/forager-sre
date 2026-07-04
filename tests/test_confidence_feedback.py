"""Tests for confidence parsing, feedback loop, /metrics, postmortem, grouping."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import forager.store as store_mod
from forager.adapters.llm import LLMResponse
from forager.agent import Investigation


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    store_mod._db = None
    store_mod._db_path = tmp_path / "test.db"
    store_mod.init(store_mod._db_path)
    yield
    if store_mod._db:
        store_mod._db.close()
        store_mod._db = None


@pytest.fixture
def client():
    from forager.server import app

    return TestClient(app)


def _save(incident_id, conclusion="ROOT CAUSE: x", confidence=""):
    inv = Investigation(incident_id=incident_id, service="api", alert="A")
    inv.conclusion = conclusion
    inv.confidence = confidence
    store_mod.save(inv)


# ── confidence ────────────────────────────────────────────────────────────────


def test_confidence_parsed_from_conclusion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import forager.agent as agent_mod

    done = LLMResponse(
        stop_reason="end_turn",
        text="ROOT CAUSE: pool\nCONFIDENCE: low\nEVIDENCE:\n- x",
    )
    with patch("forager.agent.llm.call", return_value=done):
        inv = agent_mod.investigate("INC-C", "api", "A")
    assert inv.confidence == "low"


def test_confidence_absent_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import forager.agent as agent_mod

    done = LLMResponse(stop_reason="end_turn", text="ROOT CAUSE: pool")
    with patch("forager.agent.llm.call", return_value=done):
        inv = agent_mod.investigate("INC-NC", "api", "A")
    assert inv.confidence == ""


def test_confidence_persisted():
    _save("INC-P", confidence="high")
    assert store_mod.get("INC-P")["confidence"] == "high"


def test_low_confidence_flagged_in_slack_blocks():
    from forager.adapters import slack

    blocks = slack.investigation_blocks("INC-1", "ROOT CAUSE: ?", [], confidence="low")
    assert any("Low confidence" in str(b) for b in blocks)


# ── feedback ──────────────────────────────────────────────────────────────────


def test_feedback_recorded(client):
    _save("INC-FB")
    resp = client.post("/investigations/INC-FB/feedback", json={"verdict": "up", "note": "spot on"})
    assert resp.status_code == 200
    rec = store_mod.get("INC-FB")
    assert rec["feedback_verdict"] == "up"
    assert rec["feedback_note"] == "spot on"


def test_feedback_invalid_verdict(client):
    _save("INC-FB2")
    resp = client.post("/investigations/INC-FB2/feedback", json={"verdict": "meh"})
    assert resp.status_code == 422


def test_feedback_unknown_incident(client):
    resp = client.post("/investigations/NOPE/feedback", json={"verdict": "up"})
    assert resp.status_code == 404


def test_downvoted_excluded_from_memory(client):
    _save("INC-GOOD")
    _save("INC-BAD")
    client.post("/investigations/INC-BAD/feedback", json={"verdict": "down"})
    ids = {r["id"] for r in store_mod.search_similar(service="api")}
    assert ids == {"INC-GOOD"}


# ── /metrics ──────────────────────────────────────────────────────────────────


def test_metrics_endpoint(client):
    _save("INC-M1")
    _save("INC-M2")
    client.post("/investigations/INC-M1/feedback", json={"verdict": "up"})
    store_mod.incr_counter("dedup_hits", 3)

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "forager_investigations_total 2" in body
    assert "forager_dedup_total 3" in body
    assert 'forager_feedback_total{verdict="up"} 1' in body


# ── postmortem ────────────────────────────────────────────────────────────────


def test_postmortem_generation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _save("INC-PM", conclusion="ROOT CAUSE: bad deploy")
    from forager import postmortem

    md = LLMResponse(stop_reason="end_turn", text="# Postmortem: INC-PM\n## Summary\n...")
    with patch("forager.postmortem.llm.call", return_value=md) as mock_call:
        out = postmortem.generate("INC-PM")
    assert out.startswith("# Postmortem")
    assert mock_call.call_args.kwargs["tools"] == []  # plain completion, no tools


def test_postmortem_unknown_incident():
    from forager import postmortem

    with pytest.raises(KeyError):
        postmortem.generate("INC-MISSING")


def test_postmortem_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _save("INC-PME")
    md = LLMResponse(stop_reason="end_turn", text="# Postmortem")
    with patch("forager.postmortem.llm.call", return_value=md):
        resp = client.get("/investigations/INC-PME/postmortem")
    assert resp.status_code == 200
    assert "markdown" in resp.headers["content-type"]


def test_postmortem_endpoint_404(client):
    resp = client.get("/investigations/NOPE/postmortem")
    assert resp.status_code == 404


# ── alert grouping ────────────────────────────────────────────────────────────


def _alerts(*specs):
    return {
        "alerts": [
            {
                "status": "firing",
                "fingerprint": fp,
                "labels": {"alertname": name, "service": svc},
                "annotations": {"description": f"{name} firing"},
            }
            for fp, name, svc in specs
        ]
    }


def test_grouping_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("FORAGER_GROUP_ALERTS", raising=False)
    inv = Investigation(incident_id="INC-G", service="api", alert="A")
    inv.conclusion = "ok"
    with patch("forager.agent.investigate", return_value=inv) as mock_inv:
        resp = client.post(
            "/webhook/alertmanager",
            json=_alerts(("f1", "HighLatency", "api"), ("f2", "HighErrors", "api")),
        )
    assert resp.json()["processed"] == 2
    assert mock_inv.call_count == 2


def test_grouping_correlates_same_service(client, monkeypatch):
    monkeypatch.setenv("FORAGER_GROUP_ALERTS", "1")
    inv = Investigation(incident_id="INC-G", service="api", alert="A")
    inv.conclusion = "ok"
    with patch("forager.agent.investigate", return_value=inv) as mock_inv:
        resp = client.post(
            "/webhook/alertmanager",
            json=_alerts(
                ("f1", "HighLatency", "api"),
                ("f2", "HighErrors", "api"),
                ("f3", "DiskFull", "db"),
            ),
        )
    assert resp.json()["processed"] == 2  # api group + db alone
    assert mock_inv.call_count == 2
    grouped_call = next(c for c in mock_inv.call_args_list if c.args[1] == "api")
    assert "HighErrors" in grouped_call.args[2] and "HighLatency" in grouped_call.args[2]
    assert "2 correlated alerts" in grouped_call.args[3]


# ── live Slack progress ───────────────────────────────────────────────────────


def test_slack_placeholder_then_update(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
    import forager.agent as agent_mod

    done = LLMResponse(stop_reason="end_turn", text="ROOT CAUSE: x\nCONFIDENCE: high")
    with (
        patch("forager.agent.llm.call", return_value=done),
        patch("forager.agent.slack.post", return_value={"status": "ok", "ts": "111.222"}) as mock_post,
        patch("forager.agent.slack.update", return_value={"status": "ok", "ts": "111.222"}) as mock_update,
    ):
        inv = agent_mod.investigate("INC-LIVE", "api", "A")

    assert inv.slack_ts == "111.222"
    mock_post.assert_called_once()  # placeholder
    assert "investigating" in mock_post.call_args.kwargs.get("text", str(mock_post.call_args))
    mock_update.assert_called_once()  # final report replaces it
    assert mock_update.call_args.args[2] == "111.222"
