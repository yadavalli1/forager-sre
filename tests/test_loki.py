"""Tests for the Loki adapter (mocked HTTP)."""
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


def test_query_loki_logs_ok():
    from forager.adapters.loki import query_loki_logs
    payload = {
        "data": {
            "result": [
                {
                    "stream": {"app": "api", "pod": "api-1"},
                    "values": [["1700000000", "ERROR pool exhausted"], ["1700000001", "WARN retry"]],
                }
            ]
        }
    }
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = query_loki_logs("http://loki:3100", '{app="api"} |= "error"')
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert "pool exhausted" in result["lines"][0]["line"]
    assert result["lines"][0]["labels"]["app"] == "api"


def test_query_loki_logs_no_logs():
    from forager.adapters.loki import query_loki_logs
    payload = {"data": {"result": []}}
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = query_loki_logs("http://loki:3100", '{app="missing"}')
    assert result["status"] == "no_logs"
    assert result["lines"] == []


def test_query_loki_logs_connection_error():
    from forager.adapters.loki import query_loki_logs
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = query_loki_logs("http://loki:3100", '{app="api"}')
    assert result["status"] == "error"
    assert "Cannot reach Loki" in result["error"]


def test_query_loki_logs_generic_error():
    from forager.adapters.loki import query_loki_logs
    with patch("httpx.get", side_effect=ValueError("bad")):
        result = query_loki_logs("http://loki:3100", '{app="api"}')
    assert result["status"] == "error"
    assert "bad" in result["error"]