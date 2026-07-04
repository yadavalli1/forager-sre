"""Datadog adapter — metric queries for teams whose telemetry lives in Datadog."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _range_seconds(range_: str) -> int:
    try:
        return int(range_[:-1]) * _UNITS.get(range_[-1], 60)
    except (ValueError, IndexError):
        return 300


def query(promql_or_dd: str, range_: str = "5m") -> dict[str, Any]:
    """Query the Datadog v1 metrics API. Requires DD_API_KEY and DD_APP_KEY env vars."""
    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    if not api_key or not app_key:
        return {"status": "error", "error": "DD_API_KEY / DD_APP_KEY not set"}
    site = os.environ.get("DD_SITE", "datadoghq.com")
    now = int(time.time())
    try:
        r = httpx.get(
            f"https://api.{site}/api/v1/query",
            params={"from": now - _range_seconds(range_), "to": now, "query": promql_or_dd},
            headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key},
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
    except httpx.HTTPError as exc:
        return {"status": "error", "error": str(exc)}

    series = []
    for s in body.get("series", [])[:20]:
        points = [p[1] for p in (s.get("pointlist") or []) if p[1] is not None]
        if not points:
            continue
        series.append(
            {
                "metric": s.get("expression", s.get("metric", "")),
                "scope": s.get("scope", ""),
                "min": min(points),
                "max": max(points),
                "avg": sum(points) / len(points),
                "last": points[-1],
            }
        )
    if not series:
        return {"status": "no_results", "query": promql_or_dd}
    return {"status": "ok", "query": promql_or_dd, "series": series}
