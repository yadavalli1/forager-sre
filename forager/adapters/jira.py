"""Jira adapter — search for issues related to an incident.

When an SRE investigation identifies a likely cause, correlating it with open
Jira tickets (e.g. a known bug, a planned change, an incident ticket) tells
the operator whether this is a known issue and who owns it.
"""
from __future__ import annotations
from typing import Any

import httpx


def search_jira_issues(
    url: str, email: str, token: str, jql: str, limit: int = 20
) -> dict[str, Any]:
    """Search Jira via GET /rest/api/3/search?jql=.

    Args:
        url: Jira base URL, e.g. 'https://myorg.atlassian.net'.
        email: Atlassian account email (used as the username with API token).
        token: Atlassian API token.
        jql: JQL query, e.g. 'project = SRE AND status != Done ORDER BY updated DESC'.
        limit: Maximum issues to return.
    """
    if not url or not email or not token:
        return {
            "status": "error",
            "error": "JIRA_URL, JIRA_EMAIL, and JIRA_TOKEN are required",
            "issues": [],
        }
    try:
        r = httpx.get(
            f"{url}/rest/api/3/search",
            params={"jql": jql, "maxResults": str(limit)},
            auth=(email, token),
            headers={"Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        issues = data.get("issues", [])
        if not issues:
            return {"status": "no_issues", "jql": jql, "issues": []}
        rows = [
            {
                "key": i.get("key"),
                "summary": (i.get("fields", {}).get("summary") or "")[:120],
                "status": (i.get("fields", {}).get("status", {}) or {}).get("name", ""),
                "priority": (i.get("fields", {}).get("priority", {}) or {}).get("name", ""),
                "assignee": (i.get("fields", {}).get("assignee", {}) or {}).get("displayName", "unassigned"),
                "updated": i.get("fields", {}).get("updated", ""),
                "url": f"{url}/browse/{i.get('key')}",
            }
            for i in issues[:limit]
        ]
        return {"status": "ok", "jql": jql, "count": len(rows), "issues": rows}
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Jira at {url}", "issues": []}
    except httpx.HTTPStatusError as exc:
        return {"status": "error", "error": f"Jira API returned {exc.response.status_code}", "issues": []}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "issues": []}