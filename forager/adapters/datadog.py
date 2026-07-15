"""Datadog adapter — query metrics via the v2 metrics API.

Datadog is a hosted observability platform. When an organization uses Datadog
instead of (or alongside) Prometheus, the agent can query the same telemetry
through this adapter. Requires DATADOG_API_KEY and DATADOG_APP_KEY.
"""
from __future__ import annotations
import time
from typing import Any

import httpx


def _duration_to_seconds(duration: str) -> int:
    unit = duration[-1]
    val = int(duration[:-1])
    return val * {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)


def query_datadog_metrics(
    api_key: str,
    app_key: str,
    site: str,
    query: str,
    window: str = "5m",
) -> dict[str, Any]:
    """Query Datadog metrics via POST /api/v2/query.

    Args:
        api_key: Datadog API key.
        app_key: Datadog application key.
        site: Datadog site, e.g. 'datadoghq.com'.
        query: Datadog metric query string, e.g. 'avg:system.cpu.system{*}'.format()...
        window: Look-back window, e.g. '5m', '1h'.
    """
    if not api_key or not app_key:
        return {"status": "error", "error": "DATADOG_API_KEY and DATADOG_APP_KEY are required", "query": query}
    try:
        now = int(time.time())
        secs = _duration_to_seconds(window)
        r = httpx.post(
            f"https://api.{site}/api/v2/query",
            headers={
                "DD-API-KEY": api_key,
                "DD-APPLICATION-KEY": app_key,
                "Content-Type": "application/json",
            },
            json={
                "data": {
                    "type": "metrics_request",
                    "attributes": {
                        "formulas": [{"formula": query}],
                        "from": (now - secs) * 1000,
                        "to": now * 1000,
                        "interval": "60s",
                    },
                }
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        series = data.get("data", {}).get("attributes", {}).get("series", {})
        rows = []
        for key, points in (series or {}).items():
            values = [p.get("value") for p in points if p.get("value") is not None]
            if not values:
                continue
            rows.append({
                "metric": key,
                "count": len(values),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "avg": round(sum(values) / len(values), 4),
                "last": round(values[-1], 4),
            })
        if not rows:
            return {"status": "no_data", "query": query, "window": window}
        return {"status": "ok", "query": query, "window": window, "results": rows}
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Datadog API at {site}", "query": query}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "query": query}