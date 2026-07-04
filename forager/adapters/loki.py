"""Loki adapter — LogQL log search across services (not just single-pod tails)."""

from __future__ import annotations

import time
from typing import Any

import httpx

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _since_seconds(since: str) -> int:
    try:
        return int(since[:-1]) * _UNITS.get(since[-1], 60)
    except (ValueError, IndexError):
        return 600


def search_logs(url: str, query: str, since: str = "10m", limit: int = 100) -> dict[str, Any]:
    """Run a LogQL query, e.g. '{app="api"} |= "error"', over the last `since` window."""
    if not url:
        return {"status": "error", "error": "Loki URL not configured (set LOKI_URL)"}
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - _since_seconds(since) * 10**9
    try:
        r = httpx.get(
            f"{url.rstrip('/')}/loki/api/v1/query_range",
            params={"query": query, "start": start_ns, "end": now_ns, "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
    except httpx.HTTPError as exc:
        return {"status": "error", "error": str(exc)}

    lines: list[str] = []
    for stream in data.get("result", [])[:20]:
        labels = stream.get("stream", {})
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        for _ts, line in stream.get("values", []):
            lines.append(f"[{label_str}] {line}")
    lines = lines[:limit]
    if not lines:
        return {"status": "no_results", "query": query, "since": since}
    return {"status": "ok", "query": query, "lines": lines, "count": len(lines)}
