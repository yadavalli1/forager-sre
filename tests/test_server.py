"""Tests for the FastAPI webhook server."""
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from forager.server import app
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "time" in data


def test_root_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_investigations_empty(client, tmp_path, monkeypatch):
    import forager.store as store_mod
    store_mod._db = None
    store_mod._db_path = tmp_path / "test.db"
    store_mod.init(store_mod._db_path)
    resp = client.get("/investigations")
    assert resp.status_code == 200
    assert resp.json() == []


def _mock_investigation(incident_id: str) -> MagicMock:
    from forager.agent import Investigation
    from datetime import datetime, timezone
    inv = Investigation(incident_id=incident_id, service="api", alert="High error rate")
    inv.conclusion = "ROOT CAUSE: depleted connection pool"
    return inv


def test_alertmanager_webhook_firing(client):
    inv = _mock_investigation("INC-AB1234")
    with patch("forager.agent.investigate", return_value=inv) as mock_inv:
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "fingerprint": "ab1234cd5678",
                    "labels": {
                        "alertname": "HighErrorRate",
                        "service": "api",
                    },
                    "annotations": {"description": "Error rate above 5%"},
                }
            ]
        }
        resp = client.post("/webhook/alertmanager", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1
    mock_inv.assert_called_once()
    call_args = mock_inv.call_args[0]
    assert call_args[2] == "HighErrorRate"  # alert name
    assert call_args[1] == "api"            # service


def test_alertmanager_webhook_resolved_skipped(client):
    """Resolved alerts should not trigger investigation."""
    payload = {
        "alerts": [
            {
                "status": "resolved",
                "fingerprint": "aabbccdd",
                "labels": {"alertname": "HighErrorRate"},
                "annotations": {},
            }
        ]
    }
    with patch("forager.agent.investigate") as mock_inv:
        resp = client.post("/webhook/alertmanager", json=payload)
    assert resp.status_code == 200
    assert resp.json()["processed"] == 0
    mock_inv.assert_not_called()


def test_alertmanager_webhook_multiple_alerts(client):
    """Multiple firing alerts all get investigated."""
    inv = _mock_investigation("INC-TEST")
    alerts = [
        {
            "status": "firing",
            "fingerprint": f"fp{i:06d}",
            "labels": {"alertname": f"Alert{i}", "service": "svc"},
            "annotations": {},
        }
        for i in range(3)
    ]
    with patch("forager.agent.investigate", return_value=inv):
        resp = client.post("/webhook/alertmanager", json={"alerts": alerts})

    assert resp.status_code == 200
    assert resp.json()["processed"] == 3


def test_pagerduty_webhook_triggered(client):
    inv = _mock_investigation("PD-42")
    payload = {
        "events": [
            {
                "event_type": "incident.triggered",
                "data": {
                    "number": 42,
                    "title": "Database disk full",
                    "service": {"name": "postgres"},
                    "body": {"details": "Disk usage at 98%"},
                },
            }
        ]
    }
    with patch("forager.agent.investigate", return_value=inv) as mock_inv:
        resp = client.post("/webhook/pagerduty", json=payload)

    assert resp.status_code == 200
    assert resp.json()["processed"] == 1
    mock_inv.assert_called_once()
    args = mock_inv.call_args[0]
    assert args[0] == "PD-42"
    assert args[1] == "postgres"
    assert args[2] == "Database disk full"


def test_pagerduty_webhook_other_event_skipped(client):
    payload = {
        "events": [{"event_type": "incident.resolved", "data": {"number": 1, "title": "t", "service": {"name": "s"}}}]
    }
    with patch("forager.agent.investigate") as mock_inv:
        resp = client.post("/webhook/pagerduty", json=payload)
    assert resp.status_code == 200
    assert resp.json()["processed"] == 0
    mock_inv.assert_not_called()


def test_investigations_list_after_webhook(client, tmp_path):
    import forager.store as store_mod
    store_mod._db = None
    store_mod._db_path = tmp_path / "test.db"
    store_mod.init(store_mod._db_path)
    inv = _mock_investigation("INC-LIST")
    with patch("forager.agent.investigate", return_value=inv):
        client.post("/webhook/alertmanager", json={
            "alerts": [{
                "status": "firing",
                "fingerprint": "listtest2",
                "labels": {"alertname": "Test"},
                "annotations": {},
            }]
        })
    resp = client.get("/investigations")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) >= 1
    assert records[0]["id"] == "INC-LIST"
