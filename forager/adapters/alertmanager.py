"""Alertmanager adapter — list currently firing alerts.

Used by the one-call SRE agent to self-discover active incidents when the
caller's free-form query does not already name a specific service or alert.
"""
from __future__ import annotations
import httpx
from typing import Any


def list_firing_alerts(url: str, max_alerts: int = 20) -> dict[str, Any]:
    """Return currently firing alerts from Alertmanager.

    Args:
        url: Alertmanager base URL, e.g. http://alertmanager:9093
        max_alerts: Cap on the number of alerts returned (newest first).

    Returns:
        {"status": "ok"|"no_alerts"|"error", "alertmanager": url,
         "count": int, "alerts": [...]}
    """
    try:
        r = httpx.get(
            f"{url}/api/v2/alerts",
            params={"active": "true"},
            timeout=10,
        )
        r.raise_for_status()
        raw_alerts = r.json()
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Alertmanager at {url}", "alertmanager": url, "count": 0, "alerts": []}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "alertmanager": url, "count": 0, "alerts": []}

    firing = [a for a in raw_alerts if a.get("status") == "firing"]
    if not firing:
        return {"status": "no_alerts", "alertmanager": url, "count": 0, "alerts": []}

    alerts = []
    for a in firing[:max_alerts]:
        labels = a.get("labels", {})
        annotations = a.get("annotations", {})
        alerts.append({
            "fingerprint": a.get("fingerprint", ""),
            "alertname": labels.get("alertname", "UnknownAlert"),
            "service": labels.get("service", labels.get("job", "unknown")),
            "severity": labels.get("severity", ""),
            "summary": annotations.get("summary", ""),
            "description": annotations.get("description", ""),
        })
    return {"status": "ok", "alertmanager": url, "count": len(alerts), "alerts": alerts}