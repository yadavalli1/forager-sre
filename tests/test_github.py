"""Tests for the GitHub adapter."""

from unittest.mock import MagicMock, patch

import httpx


def _mock_response(data, status: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=MagicMock(), response=resp)
    return resp


def _commit(sha: str = "abc12345", msg: str = "fix: pool size", author: str = "alice"):
    return {
        "sha": sha,
        "commit": {
            "message": msg,
            "author": {"name": author, "date": "2026-06-28T10:00:00Z"},
        },
    }


def test_recent_commits_ok():
    from forager.adapters.github import recent_commits

    commits = [_commit("abc12345", "fix: connection pool"), _commit("def67890", "chore: bump version")]
    with patch("httpx.get", return_value=_mock_response(commits)):
        result = recent_commits("myorg/api")
    assert result["status"] == "ok"
    assert result["repo"] == "myorg/api"
    assert len(result["commits"]) == 2
    assert result["commits"][0]["sha"] == "abc12345"
    assert result["commits"][0]["message"] == "fix: connection pool"
    assert result["commits"][0]["author"] == "alice"


def test_recent_commits_normalises_github_url():
    from forager.adapters.github import recent_commits

    with patch("httpx.get", return_value=_mock_response([])) as mock_get:
        recent_commits("https://github.com/myorg/api")
    url = mock_get.call_args[0][0]
    assert "myorg/api" in url
    assert "https://github.com/" not in url


def test_recent_commits_empty():
    from forager.adapters.github import recent_commits

    with patch("httpx.get", return_value=_mock_response([])):
        result = recent_commits("myorg/api")
    assert result["status"] == "ok"
    assert result["commits"] == []


def test_recent_commits_connection_error():
    from forager.adapters.github import recent_commits

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = recent_commits("myorg/api")
    assert result["status"] == "error"
    assert "Cannot reach" in result["error"]


def test_recent_commits_http_error():
    from forager.adapters.github import recent_commits

    with patch("httpx.get", return_value=_mock_response({}, status=404)):
        result = recent_commits("myorg/nonexistent")
    assert result["status"] == "error"
    assert "404" in result["error"]


def test_recent_commits_truncates_long_message():
    from forager.adapters.github import recent_commits

    long_msg = "fix: " + "x" * 200
    commits = [_commit("abc12345", long_msg)]
    with patch("httpx.get", return_value=_mock_response(commits)):
        result = recent_commits("myorg/api")
    assert len(result["commits"][0]["message"]) <= 120


def test_recent_commits_uses_token(monkeypatch):
    from forager.adapters.github import recent_commits

    monkeypatch.setenv("GITHUB_TOKEN", "")
    with patch("httpx.get", return_value=_mock_response([])) as mock_get:
        recent_commits("myorg/api", token="ghp_testtoken")
    headers = mock_get.call_args[1]["headers"]
    assert headers.get("Authorization") == "Bearer ghp_testtoken"


def test_recent_commits_no_auth_header_without_token(monkeypatch):
    from forager.adapters.github import recent_commits

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with patch("httpx.get", return_value=_mock_response([])) as mock_get:
        recent_commits("myorg/api", token="")
    headers = mock_get.call_args[1]["headers"]
    assert "Authorization" not in headers


def test_recent_prs_ok():
    from forager.adapters.github import recent_prs

    prs = [
        {
            "number": 42,
            "title": "feat: raise connection pool limit",
            "user": {"login": "alice"},
            "merged_at": "2026-06-28T09:00:00Z",
            "base": {"ref": "main"},
        },
        {
            "number": 41,
            "title": "chore: update deps",
            "user": {"login": "bob"},
            "merged_at": None,  # not merged
            "base": {"ref": "main"},
        },
    ]
    with patch("httpx.get", return_value=_mock_response(prs)):
        result = recent_prs("myorg/api")
    assert result["status"] == "ok"
    # Only merged PRs included
    assert len(result["prs"]) == 1
    assert result["prs"][0]["number"] == 42


def test_recent_prs_error():
    from forager.adapters.github import recent_prs

    with patch("httpx.get", return_value=_mock_response({}, status=403)):
        result = recent_prs("myorg/private-repo")
    assert result["status"] == "error"
