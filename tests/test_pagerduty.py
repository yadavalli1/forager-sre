"""Tests for the PagerDuty adapter (mocked HTTP)."""
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


def _incident(iid="P1", number=1, title="DB down", status="triggered"):
    return {
        "id": iid,
        "incident_number": number,
        "title": title,
        "status": status,
        "urgency": "high",
        "service": {"summary": "postgres"},
        "created_at": "2026-06-28T10:00:00Z",
        "last_status_change_at": "2026-06-28T10:05:00Z",
    }


def test_list_pagerduty_no_token():
    from forager.adapters.pagerduty import list_pagerduty_incidents
    result = list_pagerduty_incidents("")
    assert result["status"] == "error"
    assert "required" in result["error"]


def test_list_pagerduty_ok():
    from forager.adapters.pagerduty import list_pagerduty_incidents
    payload = {"incidents": [_incident("P1", 1, "DB down"), _incident("P2", 2, "API 5xx")]}
    with patch("httpx.get", return_value=_mock_response(payload)) as mock_get:
        result = list_pagerduty_incidents("tok")
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert result["incidents"][0]["title"] == "DB down"
    assert result["incidents"][0]["service"] == "postgres"
    headers = mock_get.call_args[1]["headers"]
    assert headers["Authorization"] == "Token token=tok"


def test_list_pagerduty_no_incidents():
    from forager.adapters.pagerduty import list_pagerduty_incidents
    with patch("httpx.get", return_value=_mock_response({"incidents": []})):
        result = list_pagerduty_incidents("tok")
    assert result["status"] == "no_incidents"


def test_list_pagerduty_connection_error():
    from forager.adapters.pagerduty import list_pagerduty_incidents
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = list_pagerduty_incidents("tok")
    assert result["status"] == "error"
    assert "Cannot reach PagerDuty" in result["error"]


def test_list_pagerduty_http_error():
    from forager.adapters.pagerduty import list_pagerduty_incidents
    with patch("httpx.get", return_value=_mock_response({}, status=401)):
        result = list_pagerduty_incidents("bad-tok")
    assert result["status"] == "error"
    assert "401" in result["error"]