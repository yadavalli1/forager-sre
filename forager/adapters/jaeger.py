"""Jaeger adapter — distributed tracing.

Jaeger stores distributed traces that span multiple services. When an SRE
investigation involves a request that crossed service boundaries (e.g.
checkout → payment → inventory), traces pinpoint where latency was spent and
which span errored. Two entry points: lookup a specific trace by ID, or find
recent traces for a service/operation.
"""
from __future__ import annotations
from typing import Any

import httpx


def _summarise_trace(trace: dict) -> dict:
    spans = trace.get("spans", [])
    processes = trace.get("processes", {})
    if not spans:
        return {"trace_id": trace.get("traceID", ""), "spans": 0}
    durations = [s.get("duration", 0) for s in spans]
    errors = sum(1 for s in spans if any(
        tag.get("key") == "error" and tag.get("value") is True
        for tag in s.get("tags", [])
    ))
    services = {
        processes.get(s.get("processID", ""), {}).get("serviceName", "?")
        for s in spans
    }
    return {
        "trace_id": trace.get("traceID", ""),
        "spans": len(spans),
        "duration_us": max(durations) if durations else 0,
        "errors": errors,
        "services": sorted(services),
        "root_operation": spans[0].get("operationName", "") if spans else "",
    }


def get_trace(url: str, trace_id: str) -> dict[str, Any]:
    """Fetch a single trace by ID and return a summary + raw spans."""
    try:
        r = httpx.get(f"{url}/api/traces/{trace_id}", timeout=10)
        r.raise_for_status()
        data = r.json()
        traces = data.get("data", [])
        if not traces:
            return {"status": "no_trace", "trace_id": trace_id}
        trace = traces[0]
        summary = _summarise_trace(trace)
        return {"status": "ok", "trace_id": trace_id, "trace": summary}
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Jaeger at {url}", "trace_id": trace_id}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "trace_id": trace_id}


def find_traces(
    url: str, service: str, operation: str = "", limit: int = 20
) -> dict[str, Any]:
    """Find recent traces for a service (optionally filtered by operation)."""
    try:
        params: dict[str, str] = {"service": service, "limit": str(limit)}
        if operation:
            params["operation"] = operation
        r = httpx.get(f"{url}/api/traces", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        traces = data.get("data", [])
        if not traces:
            return {"status": "no_traces", "service": service, "traces": []}
        summaries = [_summarise_trace(t) for t in traces[:limit]]
        return {
            "status": "ok",
            "service": service,
            "operation": operation or "any",
            "count": len(summaries),
            "traces": summaries,
        }
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Jaeger at {url}", "service": service, "traces": []}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "service": service, "traces": []}