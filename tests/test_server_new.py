"""Tests for new server features: deduplication, /investigations/{id}, /dashboard."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    import forager.store as store_mod

    store_mod._db = None
    store_mod._db_path = tmp_path / "test.db"
    store_mod.init(store_mod._db_path)
    yield
    if store_mod._db:
        store_mod._db.close()
        store_mod._db = None


@pytest.fixture
def client(fresh_store):
    from forager.server import app

    return TestClient(app)


def _make_inv(incident_id: str = "INC-TEST"):
    from forager.agent import Investigation

    inv = Investigation(incident_id=incident_id, service="api", alert="High latency")
    inv.conclusion = "ROOT CAUSE: pool exhaustion"
    return inv


# ── /investigations/{id} ──────────────────────────────────────────────────────


def test_get_investigation_after_webhook(client):
    inv = _make_inv("INC-AB1234")
    with patch("forager.agent.investigate", return_value=inv):
        client.post(
            "/webhook/alertmanager",
            json={
                "alerts": [
                    {
                        "status": "firing",
                        "fingerprint": "ab1234",
                        "labels": {"alertname": "HighLatency", "service": "api"},
                        "annotations": {},
                    }
                ]
            },
        )
    resp = client.get("/investigations/INC-AB1234")
    assert resp.status_code == 200
    assert resp.json()["id"] == "INC-AB1234"


def test_get_investigation_not_found(client):
    resp = client.get("/investigations/INC-MISSING")
    assert resp.status_code == 404


# ── deduplication ─────────────────────────────────────────────────────────────


def test_duplicate_alert_not_reinvestigated(client):
    inv = _make_inv("INC-DEDUP")
    alert_payload = {
        "alerts": [
            {
                "status": "firing",
                "fingerprint": "fp_dedup_001",
                "labels": {"alertname": "DupAlert", "service": "svc"},
                "annotations": {},
            }
        ]
    }
    with patch("forager.agent.investigate", return_value=inv) as mock_inv:
        # First webhook — should investigate
        resp1 = client.post("/webhook/alertmanager", json=alert_payload)
        # Second webhook same fingerprint — should deduplicate
        resp2 = client.post("/webhook/alertmanager", json=alert_payload)

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert mock_inv.call_count == 1  # only investigated once

    data2 = resp2.json()
    assert data2["investigations"][0]["status"] == "deduplicated"


def test_different_fingerprints_both_investigated(client):
    inv = _make_inv("INC-MULTI")
    with patch("forager.agent.investigate", return_value=inv) as mock_inv:
        client.post(
            "/webhook/alertmanager",
            json={
                "alerts": [
                    {
                        "status": "firing",
                        "fingerprint": "fp_aaa",
                        "labels": {"alertname": "Alert", "service": "svc"},
                        "annotations": {},
                    }
                ]
            },
        )
        client.post(
            "/webhook/alertmanager",
            json={
                "alerts": [
                    {
                        "status": "firing",
                        "fingerprint": "fp_bbb",
                        "labels": {"alertname": "Alert", "service": "svc"},
                        "annotations": {},
                    }
                ]
            },
        )
    assert mock_inv.call_count == 2


# ── /dashboard ────────────────────────────────────────────────────────────────


def test_dashboard_empty(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "No investigations yet" in resp.text
    assert "text/html" in resp.headers["content-type"]


def test_dashboard_shows_investigation(client):
    inv = _make_inv("INC-DASH")
    with patch("forager.agent.investigate", return_value=inv):
        client.post(
            "/webhook/alertmanager",
            json={
                "alerts": [
                    {
                        "status": "firing",
                        "fingerprint": "dash_fp",
                        "labels": {"alertname": "DashAlert", "service": "api"},
                        "annotations": {},
                    }
                ]
            },
        )
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "INC-DASH" in resp.text
    assert "api" in resp.text
