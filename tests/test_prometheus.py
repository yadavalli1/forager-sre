"""Tests for the Prometheus adapter (mocked HTTP)."""

from unittest.mock import MagicMock, patch

import httpx


def _mock_response(data: dict, status: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=MagicMock(), response=resp)
    return resp


def test_query_ok():
    from forager.adapters.prometheus import query

    payload = {
        "data": {
            "result": [
                {"metric": {"job": "api"}, "value": [1700000000, "0.042"]},
            ]
        }
    }
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = query("http://prom:9090", "up")
    assert result["status"] == "ok"
    assert result["results"][0]["value"] == "0.042"
    assert result["results"][0]["labels"]["job"] == "api"


def test_query_no_data():
    from forager.adapters.prometheus import query

    payload = {"data": {"result": []}}
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = query("http://prom:9090", "nonexistent_metric")
    assert result["status"] == "no_data"


def test_query_connection_error():
    from forager.adapters.prometheus import query

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = query("http://prom:9090", "up")
    assert result["status"] == "error"
    assert "Cannot reach" in result["error"]


def test_query_multiple_results():
    from forager.adapters.prometheus import query

    payload = {
        "data": {
            "result": [{"metric": {"instance": f"host{i}"}, "value": [0, str(i * 0.1)]} for i in range(25)]
        }
    }
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = query("http://prom:9090", "up")
    # Capped at 20
    assert len(result["results"]) == 20


def test_query_range_ok():
    from forager.adapters.prometheus import query_range

    payload = {
        "data": {
            "result": [
                {
                    "metric": {"job": "api"},
                    "values": [[t, str(0.05 + t * 0.001)] for t in range(60)],
                }
            ]
        }
    }
    with patch("httpx.get", return_value=_mock_response(payload)):
        result = query_range("http://prom:9090", "rate(requests_total[5m])", "1h")
    assert result["status"] == "ok"
    assert "min" in result["results"][0]
    assert "max" in result["results"][0]
    assert "avg" in result["results"][0]


def test_query_range_connection_error():
    from forager.adapters.prometheus import query_range

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = query_range("http://prom:9090", "up", "5m")
    assert result["status"] == "error"
