"""Tests for the Datadog adapter (mocked HTTP)."""
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


def test_query_datadog_no_credentials():
    from forager.adapters.datadog import query_datadog_metrics
    result = query_datadog_metrics("", "", "datadoghq.com", "avg:system.cpu{*}")
    assert result["status"] == "error"
    assert "required" in result["error"]


def test_query_datadog_ok():
    from forager.adapters.datadog import query_datadog_metrics
    payload = {
        "data": {
            "attributes": {
                "series": {
                    "avg:system.cpu.system{*}": [
                        {"value": 0.4}, {"value": 0.5}, {"value": 0.6},
                    ]
                }
            }
        }
    }
    with patch("httpx.post", return_value=_mock_response(payload)) as mock_post:
        result = query_datadog_metrics("key", "app", "datadoghq.com", "avg:system.cpu{*}", "5m")
    assert result["status"] == "ok"
    assert result["results"][0]["avg"] == 0.5
    assert result["results"][0]["max"] == 0.6
    # Auth headers
    headers = mock_post.call_args[1]["headers"]
    assert headers["DD-API-KEY"] == "key"
    assert headers["DD-APPLICATION-KEY"] == "app"


def test_query_datadog_no_data():
    from forager.adapters.datadog import query_datadog_metrics
    payload = {"data": {"attributes": {"series": {}}}}
    with patch("httpx.post", return_value=_mock_response(payload)):
        result = query_datadog_metrics("key", "app", "datadoghq.com", "avg:missing{*}")
    assert result["status"] == "no_data"


def test_query_datadog_connection_error():
    from forager.adapters.datadog import query_datadog_metrics
    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        result = query_datadog_metrics("key", "app", "datadoghq.com", "avg:cpu{*}")
    assert result["status"] == "error"
    assert "Cannot reach Datadog" in result["error"]


def test_query_datadog_http_error():
    from forager.adapters.datadog import query_datadog_metrics
    with patch("httpx.post", return_value=_mock_response({}, status=403)):
        result = query_datadog_metrics("bad-key", "bad-app", "datadoghq.com", "avg:cpu{*}")
    assert result["status"] == "error"