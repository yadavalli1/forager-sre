"""PagerDuty adapter — list active incidents.

The existing PagerDuty integration is inbound-only (a webhook receiver). This
adapter adds outbound lookup so the one-call agent can enumerate current
PagerDuty incidents — useful when the query is "what's currently on-call and
firing?" without needing Alertmanager.
"""
from __future__ import annotations
from typing import Any

import httpx


def list_pagerduty_incidents(
    token: str, status: str = "triggered,acknowledged", limit: int = 25
) -> dict[str, Any]:
    """List PagerDuty incidents (default: active ones)."""
    if not token:
        return {
            "status": "error",
            "error": "PAGERDUTY_TOKEN is required",
            "incidents": [],
        }
    try:
        r = httpx.get(
            "https://api.pagerduty.com/incidents",
            headers={
                "Authorization": f"Token token={token}",
                "Accept": "application/vnd.pagerduty+json;version=2",
                "Content-Type": "application/json",
            },
            params={"statuses[]": status, "limit": str(limit), "sort_by": "created_at:desc"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        incidents = data.get("incidents", [])
        if not incidents:
            return {"status": "no_incidents", "count": 0, "incidents": []}
        rows = [
            {
                "id": i.get("id"),
                "number": i.get("incident_number"),
                "title": i.get("title", "")[:120],
                "status": i.get("status"),
                "urgency": i.get("urgency"),
                "service": (i.get("service", {}) or {}).get("summary", ""),
                "created_at": i.get("created_at"),
                "last_status_change_at": i.get("last_status_change_at"),
            }
            for i in incidents[:limit]
        ]
        return {"status": "ok", "count": len(rows), "incidents": rows}
    except httpx.ConnectError:
        return {"status": "error", "error": "Cannot reach PagerDuty API", "incidents": []}
    except httpx.HTTPStatusError as exc:
        return {"status": "error", "error": f"PagerDuty API returned {exc.response.status_code}", "incidents": []}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "incidents": []}