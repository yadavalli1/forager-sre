"""Sentry adapter — fetch recent error groups for a project.

Sentry tracks application errors across releases. When the agent suspects a
recent code change caused the incident, Sentry can confirm whether a new error
group appeared or an existing one spiked after the last deploy.
"""
from __future__ import annotations
from typing import Any

import httpx


def get_sentry_errors(
    token: str, organization: str, project: str, limit: int = 20
) -> dict[str, Any]:
    """List unresolved Sentry issues for a project, sorted by last seen."""
    if not token or not organization or not project:
        return {
            "status": "error",
            "error": "SENTRY_TOKEN, SENTRY_ORG, and SENTRY_PROJECT are required",
            "issues": [],
        }
    try:
        r = httpx.get(
            f"https://sentry.io/api/0/projects/{organization}/{project}/issues/",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params={"statsPeriod": "24h", "query": "is:unresolved", "per_page": str(limit)},
            timeout=15,
        )
        r.raise_for_status()
        issues = r.json()
        if not issues:
            return {"status": "no_errors", "organization": organization, "project": project, "issues": []}
        rows = [
            {
                "id": i.get("id"),
                "short_id": i.get("shortId"),
                "title": i.get("title", "")[:120],
                "level": i.get("level"),
                "status": i.get("status"),
                "count": i.get("count"),
                "first_seen": i.get("firstSeen"),
                "last_seen": i.get("lastSeen"),
                "release": (i.get("release") or {}).get("version", ""),
            }
            for i in issues[:limit]
        ]
        return {
            "status": "ok",
            "organization": organization,
            "project": project,
            "count": len(rows),
            "issues": rows,
        }
    except httpx.ConnectError:
        return {"status": "error", "error": "Cannot reach Sentry API", "issues": []}
    except httpx.HTTPStatusError as exc:
        return {"status": "error", "error": f"Sentry API returned {exc.response.status_code}", "issues": []}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "issues": []}