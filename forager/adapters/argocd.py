"""Argo CD adapter — GitOps application sync and health status.

When a service is deployed via Argo CD, the agent can check whether the
application is in sync with the Git source and whether Argo's health checks
report it healthy. An out-of-sync or degraded app often correlates with a
recent deploy that triggered the alert.
"""
from __future__ import annotations
from typing import Any

import httpx


def get_argocd_app_status(url: str, token: str, app_name: str) -> dict[str, Any]:
    """Fetch an Argo CD application's sync, health, and revision details."""
    if not app_name:
        return {"status": "error", "error": "app_name is required"}
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.get(
            f"{url}/api/v1/applications/{app_name}",
            headers=headers,
            timeout=10,
            verify=False,
        )
        r.raise_for_status()
        data = r.json()
        status = data.get("status", {})
        sync = status.get("sync", {})
        health = status.get("health", {})
        operation_state = status.get("operationState", {})
        return {
            "status": "ok",
            "app": app_name,
            "sync_status": sync.get("status"),
            "sync_revision": sync.get("revision", "")[:12],
            "health_status": health.get("status"),
            "health_message": health.get("message", ""),
            "target_revision": data.get("spec", {}).get("source", {}).get("targetRevision", ""),
            "last_operation": operation_state.get("phase", ""),
            "last_operation_started": operation_state.get("startedAt", ""),
            "last_operation_finished": operation_state.get("finishedAt", ""),
        }
    except httpx.ConnectError:
        return {"status": "error", "error": f"Cannot reach Argo CD at {url}", "app": app_name}
    except httpx.HTTPStatusError as exc:
        return {"status": "error", "error": f"Argo CD API returned {exc.response.status_code}", "app": app_name}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "app": app_name}