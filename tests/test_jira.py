"""Tests for the Jira adapter (mocked HTTP)."""
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


def _issue(key="SRE-42", summary="Pool exhaustion", status="In Progress", priority="High"):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "priority": {"name": priority},
            "assignee": {"displayName": "alice"},
            "updated": "2026-06-28T11:00:00Z",
        },
    }


def test_search_jira_no_credentials():
    from forager.adapters.jira import search_jira_issues
    result = search_jira_issues("", "", "", "project = SRE")
    assert result["status"] == "error"
    assert "required" in result["error"]


def test_search_jira_ok():
    from forager.adapters.jira import search_jira_issues
    payload = {"issues": [_issue("SRE-42"), _issue("SRE-43", "Disk full")]}
    with patch("httpx.get", return_value=_mock_response(payload)) as mock_get:
        result = search_jira_issues("https://myorg.atlassian.net", "a@b.com", "tok", "project = SRE")
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert result["issues"][0]["key"] == "SRE-42"
    assert result["issues"][0]["assignee"] == "alice"
    assert result["issues"][0]["url"] == "https://myorg.atlassian.net/browse/SRE-42"
    auth = mock_get.call_args[1]["auth"]
    assert auth == ("a@b.com", "tok")


def test_search_jira_no_issues():
    from forager.adapters.jira import search_jira_issues
    with patch("httpx.get", return_value=_mock_response({"issues": []})):
        result = search_jira_issues("https://myorg.atlassian.net", "a@b.com", "tok", "project = NONE")
    assert result["status"] == "no_issues"


def test_search_jira_connection_error():
    from forager.adapters.jira import search_jira_issues
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = search_jira_issues("https://myorg.atlassian.net", "a@b.com", "tok", "project = SRE")
    assert result["status"] == "error"
    assert "Cannot reach Jira" in result["error"]


def test_search_jira_http_error_real():
    from forager.adapters.jira import search_jira_issues
    with patch("httpx.get", return_value=_mock_response({}, status=403)):
        result = search_jira_issues("https://myorg.atlassian.net", "a@b.com", "bad-tok", "project = SRE")
    assert result["status"] == "error"
    assert "403" in result["error"]