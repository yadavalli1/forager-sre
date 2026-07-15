"""Tests for the Jaeger adapter (mocked HTTP)."""
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


def _trace(trace_id="abc123", spans=None):
    return {
        "traceID": trace_id,
        "spans": spans or [
            {"operationName": "GET /checkout", "duration": 50000, "processID": "p1",
             "tags": [{"key": "error", "value": False}]},
            {"operationName": "POST /pay", "duration": 200000, "processID": "p2",
             "tags": [{"key": "error", "value": True}]},
        ],
        "processes": {
            "p1": {"serviceName": "checkout-api"},
            "p2": {"serviceName": "payment-api"},
        },
    }


def test_get_trace_ok():
    from forager.adapters.jaeger import get_trace
    payload = {"data": [_trace("abc123")]}
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = get_trace("http://jaeger:16686", "abc123")
    assert result["status"] == "ok"
    assert result["trace"]["spans"] == 2
    assert result["trace"]["errors"] == 1
    assert "payment-api" in result["trace"]["services"]


def test_get_trace_no_trace():
    from forager.adapters.jaeger import get_trace
    payload = {"data": []}
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = get_trace("http://jaeger:16686", "missing")
    assert result["status"] == "no_trace"


def test_get_trace_connection_error():
    from forager.adapters.jaeger import get_trace
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = get_trace("http://jaeger:16686", "abc")
    assert result["status"] == "error"
    assert "Cannot reach Jaeger" in result["error"]


def test_find_traces_ok():
    from forager.adapters.jaeger import find_traces
    payload = {"data": [_trace("t1"), _trace("t2")]}
    with patch("httpx.get", return_value=_mock_response(payload)) as mock_get:
        result = find_traces("http://jaeger:16686", "checkout-api", operation="GET /checkout")
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert result["service"] == "checkout-api"
    # operation should be passed as query param
    params = mock_get.call_args[1]["params"]
    assert params["operation"] == "GET /checkout"


def test_find_traces_no_traces():
    from forager.adapters.jaeger import find_traces
    payload = {"data": []}
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = find_traces("http://jaeger:16686", "unknown-svc")
    assert result["status"] == "no_traces"


def test_find_traces_connection_error():
    from forager.adapters.jaeger import find_traces
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = find_traces("http://jaeger:16686", "api")
    assert result["status"] == "error"
    assert "Cannot reach Jaeger" in result["error"]