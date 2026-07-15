"""Tests for the Alertmanager adapter (mocked HTTP)."""
import pytest
import httpx
from unittest.mock import patch, MagicMock


def _mock_response(data, status: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


def _alert(name="HighErrorRate", service="api", status="firing", fp="abc12345"):
    return {
        "status": status,
        "fingerprint": fp,
        "labels": {"alertname": name, "service": service, "severity": "critical"},
        "annotations": {"summary": "errors elevated", "description": "5xx above 5%"},
    }


def test_list_firing_alerts_ok():
    from forager.adapters.alertmanager import list_firing_alerts
    payload = [
        _alert("HighErrorRate", "api", "firing", "fp1"),
        _alert("DiskFull", "db", "firing", "fp2"),
        _alert("ResolvedOne", "cache", "resolved", "fp3"),
    ]
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = list_firing_alerts("http://am:9093")
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert result["alerts"][0]["alertname"] == "HighErrorRate"
    assert result["alerts"][0]["service"] == "api"
    assert result["alerts"][0]["severity"] == "critical"
    assert result["alerts"][1]["alertname"] == "DiskFull"
    assert result["alertmanager"] == "http://am:9093"


def test_list_firing_alerts_empty():
    from forager.adapters.alertmanager import list_firing_alerts
    with patch("httpx.get", return_value=_mock_response([])):
        result = list_firing_alerts("http://am:9093")
    assert result["status"] == "no_alerts"
    assert result["count"] == 0
    assert result["alerts"] == []


def test_list_firing_alerts_only_resolved_returns_no_alerts():
    from forager.adapters.alertmanager import list_firing_alerts
    payload = [_alert("Old", "svc", "resolved", "fp1")]
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = list_firing_alerts("http://am:9093")
    assert result["status"] == "no_alerts"
    assert result["count"] == 0


def test_list_firing_alerts_connection_error():
    from forager.adapters.alertmanager import list_firing_alerts
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = list_firing_alerts("http://am:9093")
    assert result["status"] == "error"
    assert "Cannot reach" in result["error"]
    assert result["count"] == 0


def test_list_firing_alerts_generic_error():
    from forager.adapters.alertmanager import list_firing_alerts
    with patch("httpx.get", side_effect=ValueError("bad parse")):
        result = list_firing_alerts("http://am:9093")
    assert result["status"] == "error"
    assert "bad parse" in result["error"]


def test_list_firing_alerts_caps_at_max():
    from forager.adapters.alertmanager import list_firing_alerts
    payload = [_alert(f"Alert{i}", "svc", "firing", f"fp{i}") for i in range(25)]
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = list_firing_alerts("http://am:9093", max_alerts=10)
    assert result["status"] == "ok"
    assert result["count"] == 10
    assert len(result["alerts"]) == 10


def test_list_firing_alerts_falls_back_to_job_when_no_service():
    from forager.adapters.alertmanager import list_firing_alerts
    payload = [{
        "status": "firing",
        "fingerprint": "fpj",
        "labels": {"alertname": "A", "job": "checkout"},
        "annotations": {},
    }]
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = list_firing_alerts("http://am:9093")
    assert result["alerts"][0]["service"] == "checkout"