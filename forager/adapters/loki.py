"""Loki adapter — query logs via LogQL.

Loki is Grafana's horizontally-scalable log aggregation system, the log
counterpart to Prometheus. The agent uses this to grep across all pods of a
service at once, which is far more powerful than `kubectl logs` on a single pod.
"""
from __future__ import annotations
import time
from typing import Any

import httpx


def _duration_to_seconds(duration: str) -> int:
    unit = duration[-1]
    val = int(duration[:-1])
    return val * {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)


def query_loki_logs(
    url: str, logql: str, limit: int = 100, since: str = "15m"
) -> dict[str, Any]:
    """Run a Loki range query and return log lines (newest first).

    Args:
        url: Loki base URL, e.g. http://loki:3100
        logql: LogQL expression, e.g. '{app="checkout-api"} |= "error"'
        limit: Maximum number of lines to return.
        since: Look-back window, e.g. '5m', '1h'.
    """
    try:
        now = int(time.time())
        secs = _duration_to_seconds(since)
        r = httpx.get(
            f"{url}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": str(now - secs),
                "end": str(now),
                "limit": str(limit),
                "direction": "backward",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("data", {}).get("result", [])
        if not result:
            return {"status": "no_logs", "query": logql, "lines": []}
        lines = []
        for stream in result[:10]:
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                lines.append({"labels": labels, "line": line})
        return {
            "status": "ok",
            "query": logql,
            "since": since,
            "count": len(lines),
            "lines": lines[:limit],
        }
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Loki at {url}", "query": logql, "lines": []}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "query": logql, "lines": []}