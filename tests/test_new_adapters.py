"""Tests for the Loki and Datadog adapters and provider routing."""

from unittest.mock import MagicMock, patch

from forager.adapters import datadog, loki


def _mock_response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


# ── Loki ──────────────────────────────────────────────────────────────────────


def test_loki_no_url_configured():
    result = loki.search_logs("", '{app="api"}')
    assert result["status"] == "error"
    assert "not configured" in result["error"]


def test_loki_search_returns_lines():
    payload = {
        "data": {
            "result": [
                {
                    "stream": {"app": "api", "pod": "api-1"},
                    "values": [["1700000000000000000", "connection refused"]],
                }
            ]
        }
    }
    with patch("forager.adapters.loki.httpx.get", return_value=_mock_response(payload)) as mock_get:
        result = loki.search_logs("http://loki:3100", '{app="api"} |= "error"', since="5m")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert "connection refused" in result["lines"][0]
    assert "app=api" in result["lines"][0]
    assert "query_range" in mock_get.call_args.args[0]


def test_loki_no_results():
    payload = {"data": {"result": []}}
    with patch("forager.adapters.loki.httpx.get", return_value=_mock_response(payload)):
        result = loki.search_logs("http://loki:3100", '{app="ghost"}')
    assert result["status"] == "no_results"


def test_loki_http_error():
    import httpx

    with patch("forager.adapters.loki.httpx.get", side_effect=httpx.ConnectError("refused")):
        result = loki.search_logs("http://loki:3100", '{app="api"}')
    assert result["status"] == "error"


# ── Datadog ───────────────────────────────────────────────────────────────────


def test_datadog_requires_keys(monkeypatch):
    monkeypatch.delenv("DD_API_KEY", raising=False)
    monkeypatch.delenv("DD_APP_KEY", raising=False)
    result = datadog.query("avg:system.cpu.user{*}")
    assert result["status"] == "error"


def test_datadog_query_summarises_series(monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "k")
    monkeypatch.setenv("DD_APP_KEY", "a")
    payload = {
        "series": [
            {
                "metric": "system.cpu.user",
                "scope": "host:web-1",
                "pointlist": [[1, 10.0], [2, 20.0], [3, None], [4, 30.0]],
            }
        ]
    }
    with patch("forager.adapters.datadog.httpx.get", return_value=_mock_response(payload)):
        result = datadog.query("avg:system.cpu.user{*}", "5m")
    assert result["status"] == "ok"
    s = result["series"][0]
    assert s["min"] == 10.0 and s["max"] == 30.0 and s["last"] == 30.0


# ── provider routing + search_logs dispatch ──────────────────────────────────


def test_query_metrics_routes_to_datadog_when_provider_set():
    from unittest.mock import MagicMock

    import forager.agent as agent_mod

    cfg = MagicMock()
    cfg.provider = "datadog"
    with patch("forager.agent.datadog.query", return_value={"status": "ok"}) as mock_dd:
        result = agent_mod._execute_tool("query_metrics", {"query": "avg:foo{*}"}, cfg)
    assert result["status"] == "ok"
    mock_dd.assert_called_once()


def test_search_logs_dispatches_to_loki():
    from unittest.mock import MagicMock

    import forager.agent as agent_mod

    cfg = MagicMock()
    cfg.loki.url = "http://loki:3100"
    with patch("forager.agent.loki.search_logs", return_value={"status": "ok"}) as mock_loki:
        result = agent_mod._execute_tool("search_logs", {"query": '{app="x"}'}, cfg)
    assert result["status"] == "ok"
    mock_loki.assert_called_once_with("http://loki:3100", '{app="x"}', "10m", 100)


def test_search_logs_tool_in_schema():
    from forager.adapters import llm

    assert any(t["name"] == "search_logs" for t in llm.TOOLS)
