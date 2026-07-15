"""Tests for the Sentry adapter (mocked HTTP)."""
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


def _issue(iid="1", title="NullPointerException", level="error", count="42"):
    return {
        "id": iid,
        "shortId": f"PROJ-{iid}",
        "title": title,
        "level": level,
        "status": "unresolved",
        "count": count,
        "firstSeen": "2026-06-28T08:00:00Z",
        "lastSeen": "2026-06-28T12:00:00Z",
        "release": {"version": "api@v2.1.0"},
    }


def test_get_sentry_errors_no_credentials():
    from forager.adapters.sentry import get_sentry_errors
    result = get_sentry_errors("", "org", "proj")
    assert result["status"] == "error"
    assert "required" in result["error"]


def test_get_sentry_errors_ok():
    from forager.adapters.sentry import get_sentry_errors
    payload = [_issue("1", "NPE"), _issue("2", "TimeoutError")]
    with patch("httpx.get", return_value=_mock_response(payload)) as mock_get:
        result = get_sentry_errors("tok", "myorg", "api")
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert result["issues"][0]["title"] == "NPE"
    assert result["issues"][0]["release"] == "api@v2.1.0"
    headers = mock_get.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer tok"


def test_get_sentry_errors_no_errors():
    from forager.adapters.sentry import get_sentry_errors
    with patch("httpx.get", return_value=_mock_response([])):
        result = get_sentry_errors("tok", "myorg", "api")
    assert result["status"] == "no_errors"


def test_get_sentry_errors_connection_error():
    from forager.adapters.sentry import get_sentry_errors
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = get_sentry_errors("tok", "myorg", "api")
    assert result["status"] == "error"
    assert "Cannot reach Sentry" in result["error"]


def test_get_sentry_errors_http_error():
    from forager.adapters.sentry import get_sentry_errors
    with patch("httpx.get", return_value=_mock_response({}, status=401)):
        result = get_sentry_errors("bad-tok", "myorg", "api")
    assert result["status"] == "error"
    assert "401" in result["error"]