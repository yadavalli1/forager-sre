"""Prometheus adapter — instant and range queries."""

from __future__ import annotations

from typing import Any

import httpx


def query(url: str, promql: str, range_: str = "5m") -> dict[str, Any]:
    """Run an instant query and return parsed results."""
    try:
        r = httpx.get(
            f"{url}/api/v1/query",
            params={"query": promql},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("data", {}).get("result", [])
        if not results:
            return {"status": "no_data", "query": promql}
        rows = []
        for item in results[:20]:  # cap results
            rows.append(
                {
                    "labels": item.get("metric", {}),
                    "value": item.get("value", [None, None])[1],
                }
            )
        return {"status": "ok", "query": promql, "results": rows}
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Prometheus at {url}"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def query_range(url: str, promql: str, duration: str = "1h", step: str = "1m") -> dict[str, Any]:
    """Run a range query, returning summary stats instead of every point."""
    try:
        import time

        now = int(time.time())
        # convert duration to seconds
        unit = duration[-1]
        val = int(duration[:-1])
        secs = val * {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)
        r = httpx.get(
            f"{url}/api/v1/query_range",
            params={"query": promql, "start": now - secs, "end": now, "step": step},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("data", {}).get("result", [])
        if not results:
            return {"status": "no_data", "query": promql}
        summaries = []
        for item in results[:5]:
            values = [float(v[1]) for v in item.get("values", []) if v[1] != "NaN"]
            if not values:
                continue
            summaries.append(
                {
                    "labels": item.get("metric", {}),
                    "min": round(min(values), 4),
                    "max": round(max(values), 4),
                    "avg": round(sum(values) / len(values), 4),
                    "last": round(values[-1], 4),
                }
            )
        return {"status": "ok", "query": promql, "range": duration, "results": summaries}
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Prometheus at {url}"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
