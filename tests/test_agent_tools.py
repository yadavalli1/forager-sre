"""Tests for _execute_tool dispatch of all new DevOps tool integrations.

Verifies that the agent's tool router correctly invokes each new adapter
with config-sourced connection details and passes through input parameters.
"""
import pytest
from unittest.mock import patch, MagicMock

from forager import agent
from forager.config import Config


def _cfg() -> Config:
    return Config()


def test_dispatch_query_loki_logs():
    cfg = _cfg()
    with patch("forager.adapters.loki.query_loki_logs", return_value={"status": "ok", "lines": []}) as m:
        result = agent._execute_tool("query_loki_logs", {"logql": '{app="api"}', "limit": 50, "since": "5m"}, cfg)
    m.assert_called_once_with(cfg.loki.url, '{app="api"}', 50, "5m")
    assert result["status"] == "ok"


def test_dispatch_find_jaeger_traces():
    cfg = _cfg()
    with patch("forager.adapters.jaeger.find_traces", return_value={"status": "ok", "traces": []}) as m:
        result = agent._execute_tool("find_jaeger_traces", {"service": "api", "operation": "GET /", "limit": 10}, cfg)
    m.assert_called_once_with(cfg.jaeger.url, "api", "GET /", 10)
    assert result["status"] == "ok"


def test_dispatch_get_jaeger_trace():
    cfg = _cfg()
    with patch("forager.adapters.jaeger.get_trace", return_value={"status": "ok"}) as m:
        result = agent._execute_tool("get_jaeger_trace", {"trace_id": "abc123"}, cfg)
    m.assert_called_once_with(cfg.jaeger.url, "abc123")
    assert result["status"] == "ok"


def test_dispatch_query_datadog_metrics():
    cfg = _cfg()
    cfg.datadog.api_key = "dk"
    cfg.datadog.app_key = "ak"
    cfg.datadog.site = "datadoghq.com"
    with patch("forager.adapters.datadog.query_datadog_metrics", return_value={"status": "ok"}) as m:
        result = agent._execute_tool("query_datadog_metrics", {"query": "avg:cpu{*}", "window": "1h"}, cfg)
    m.assert_called_once_with("dk", "ak", "datadoghq.com", "avg:cpu{*}", "1h")
    assert result["status"] == "ok"


def test_dispatch_query_cloudwatch_metrics():
    cfg = _cfg()
    cfg.cloudwatch.access_key_id = "ak"
    cfg.cloudwatch.secret_access_key = "sk"
    with patch("forager.adapters.cloudwatch.query_cloudwatch_metrics", return_value={"status": "ok"}) as m:
        result = agent._execute_tool("query_cloudwatch_metrics", {
            "namespace": "AWS/Lambda",
            "metric_name": "Errors",
            "dimensions": [{"Name": "FunctionName", "Value": "checkout"}],
            "window": "10m",
            "period": 300,
        }, cfg)
    m.assert_called_once_with(
        "us-east-1", "ak", "sk", "AWS/Lambda", "Errors",
        [{"Name": "FunctionName", "Value": "checkout"}], "10m", 300,
    )
    assert result["status"] == "ok"


def test_dispatch_get_sentry_errors():
    cfg = _cfg()
    cfg.sentry.token = "tok"
    cfg.sentry.organization = "org"
    cfg.sentry.project = "proj"
    with patch("forager.adapters.sentry.get_sentry_errors", return_value={"status": "ok"}) as m:
        result = agent._execute_tool("get_sentry_errors", {}, cfg)
    m.assert_called_once_with("tok", "org", "proj")
    assert result["status"] == "ok"


def test_dispatch_get_argocd_app_status():
    cfg = _cfg()
    cfg.argocd.token = "tok"
    with patch("forager.adapters.argocd.get_argocd_app_status", return_value={"status": "ok"}) as m:
        result = agent._execute_tool("get_argocd_app_status", {"app_name": "checkout"}, cfg)
    m.assert_called_once_with(cfg.argocd.url, "tok", "checkout")
    assert result["status"] == "ok"


def test_dispatch_list_pagerduty_incidents():
    cfg = _cfg()
    cfg.pagerduty.token = "tok"
    with patch("forager.adapters.pagerduty.list_pagerduty_incidents", return_value={"status": "ok"}) as m:
        result = agent._execute_tool("list_pagerduty_incidents", {"status": "triggered"}, cfg)
    m.assert_called_once_with("tok", "triggered")
    assert result["status"] == "ok"


def test_dispatch_list_pagerduty_incidents_default_status():
    cfg = _cfg()
    cfg.pagerduty.token = "tok"
    with patch("forager.adapters.pagerduty.list_pagerduty_incidents", return_value={"status": "ok"}) as m:
        agent._execute_tool("list_pagerduty_incidents", {}, cfg)
    m.assert_called_once_with("tok", "triggered,acknowledged")


def test_dispatch_search_jira_issues():
    cfg = _cfg()
    cfg.jira.url = "https://org.atlassian.net"
    cfg.jira.email = "a@b.com"
    cfg.jira.token = "tok"
    with patch("forager.adapters.jira.search_jira_issues", return_value={"status": "ok"}) as m:
        result = agent._execute_tool("search_jira_issues", {"jql": "project = SRE", "limit": 5}, cfg)
    m.assert_called_once_with("https://org.atlassian.net", "a@b.com", "tok", "project = SRE", 5)
    assert result["status"] == "ok"


def test_dispatch_unknown_tool_still_errors():
    cfg = _cfg()
    result = agent._execute_tool("nonexistent", {}, cfg)
    assert result["status"] == "error"
    assert "Unknown tool" in result["error"]


def test_all_new_tools_have_definitions():
    """Every dispatched tool must have a matching definition in TOOLS for the LLM to call it."""
    from forager.adapters.llm import TOOLS
    defined = {t["name"] for t in TOOLS}
    expected = {
        "query_metrics", "get_pod_status", "get_recent_deploys", "get_pod_logs",
        "get_github_commits", "list_firing_alerts",
        "query_loki_logs", "find_jaeger_traces", "get_jaeger_trace",
        "query_datadog_metrics", "query_cloudwatch_metrics",
        "get_sentry_errors", "get_argocd_app_status",
        "list_pagerduty_incidents", "search_jira_issues",
    }
    missing = expected - defined
    assert not missing, f"Missing tool definitions: {missing}"