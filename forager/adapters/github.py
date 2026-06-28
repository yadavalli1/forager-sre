"""GitHub adapter — fetch recent commits and PRs for deploy correlation."""
from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx


def _headers(token: str = "") -> dict:
    tok = token or os.environ.get("GITHUB_TOKEN", "")
    h = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _normalise_repo(repo: str) -> str:
    return repo.removeprefix("https://github.com/").strip("/")


def recent_commits(repo: str, token: str = "", since_hours: int = 6) -> dict[str, Any]:
    """Return commits pushed to the default branch in the last N hours."""
    repo = _normalise_repo(repo)
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    try:
        r = httpx.get(
            f"https://api.github.com/repos/{repo}/commits",
            params={"since": since, "per_page": 15},
            headers=_headers(token),
            timeout=10,
        )
        r.raise_for_status()
        commits = [
            {
                "sha": c["sha"][:8],
                "message": c["commit"]["message"].split("\n")[0][:120],
                "author": c["commit"]["author"]["name"],
                "time": c["commit"]["author"]["date"],
            }
            for c in r.json()
        ]
        return {"status": "ok", "repo": repo, "since_hours": since_hours, "commits": commits}
    except httpx.ConnectError:
        return {"status": "error", "error": "Cannot reach GitHub API"}
    except httpx.HTTPStatusError as exc:
        return {"status": "error", "error": f"GitHub API returned {exc.response.status_code}"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def recent_prs(repo: str, token: str = "") -> dict[str, Any]:
    """Return recently merged pull requests (useful for correlating deploys)."""
    repo = _normalise_repo(repo)
    try:
        r = httpx.get(
            f"https://api.github.com/repos/{repo}/pulls",
            params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": 10},
            headers=_headers(token),
            timeout=10,
        )
        r.raise_for_status()
        prs = [
            {
                "number": p["number"],
                "title": p["title"][:120],
                "author": p["user"]["login"],
                "merged_at": p.get("merged_at"),
                "base": p["base"]["ref"],
            }
            for p in r.json()
            if p.get("merged_at")
        ]
        return {"status": "ok", "repo": repo, "prs": prs}
    except httpx.HTTPStatusError as exc:
        return {"status": "error", "error": f"GitHub API returned {exc.response.status_code}"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
