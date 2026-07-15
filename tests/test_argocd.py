"""Tests for the Argo CD adapter (mocked HTTP)."""
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


def _app(name="checkout-api", sync="Synced", health="Healthy"):
    return {
        "metadata": {"name": name},
        "spec": {"source": {"targetRevision": "main"}},
        "status": {
            "sync": {"status": sync, "revision": "abc123def456"},
            "health": {"status": health, "message": ""},
            "operationState": {
                "phase": "Succeeded",
                "startedAt": "2026-06-28T10:00:00Z",
                "finishedAt": "2026-06-28T10:01:00Z",
            },
        },
    }


def test_get_argocd_app_status_ok():
    from forager.adapters.argocd import get_argocd_app_status
    with patch("httpx.get", return_value=_mock_response(_app())) as mock_get:
        result = get_argocd_app_status("http://argocd:8080", "tok", "checkout-api")
    assert result["status"] == "ok"
    assert result["sync_status"] == "Synced"
    assert result["health_status"] == "Healthy"
    assert result["sync_revision"] == "abc123def456"  # [:12] of a 12-char revision is identity
    assert result["last_operation"] == "Succeeded"
    headers = mock_get.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer tok"


def test_get_argocd_app_status_out_of_sync():
    from forager.adapters.argocd import get_argocd_app_status
    with patch("httpx.get", return_value=_mock_response(_app(sync="OutOfSync", health="Degraded"))):
        result = get_argocd_app_status("http://argocd:8080", "tok", "checkout-api")
    assert result["sync_status"] == "OutOfSync"
    assert result["health_status"] == "Degraded"


def test_get_argocd_app_status_no_token():
    from forager.adapters.argocd import get_argocd_app_status
    with patch("httpx.get", return_value=_mock_response(_app())) as mock_get:
        get_argocd_app_status("http://argocd:8080", "", "app")
    headers = mock_get.call_args[1]["headers"]
    assert "Authorization" not in headers


def test_get_argocd_app_status_empty_name():
    from forager.adapters.argocd import get_argocd_app_status
    result = get_argocd_app_status("http://argocd:8080", "tok", "")
    assert result["status"] == "error"
    assert "required" in result["error"]


def test_get_argocd_app_status_connection_error():
    from forager.adapters.argocd import get_argocd_app_status
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = get_argocd_app_status("http://argocd:8080", "tok", "app")
    assert result["status"] == "error"
    assert "Cannot reach Argo CD" in result["error"]


def test_get_argocd_app_status_http_error():
    from forager.adapters.argocd import get_argocd_app_status
    with patch("httpx.get", return_value=_mock_response({}, status=404)):
        result = get_argocd_app_status("http://argocd:8080", "tok", "missing")
    assert result["status"] == "error"
    assert "404" in result["error"]