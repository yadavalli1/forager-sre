"""Kubernetes adapter — pods, deployments, logs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _client():
    from kubernetes import client  # type: ignore
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()
    return client


def pod_status(namespace: str, selector: str) -> dict[str, Any]:
    try:
        k = _client()
        v1 = k.CoreV1Api()
        pods = v1.list_namespaced_pod(namespace, label_selector=selector)
        rows = []
        for p in pods.items:
            restarts = sum(
                (cs.restart_count for cs in (p.status.container_statuses or [])),
                0,
            )
            rows.append(
                {
                    "name": p.metadata.name,
                    "phase": p.status.phase,
                    "restarts": restarts,
                    "ready": all(cs.ready for cs in (p.status.container_statuses or [])),
                    "node": p.spec.node_name,
                }
            )
        if not rows:
            return {"status": "no_pods", "selector": selector, "namespace": namespace}
        return {"status": "ok", "pods": rows}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def recent_deploys(namespace: str, deployment: str) -> dict[str, Any]:
    try:
        k = _client()
        apps = k.AppsV1Api()
        rs_list = apps.list_namespaced_replica_set(
            namespace,
            label_selector=f"app={deployment}",
        )
        entries = []
        for rs in sorted(
            rs_list.items,
            key=lambda r: r.metadata.creation_timestamp or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )[:5]:
            entries.append(
                {
                    "name": rs.metadata.name,
                    "created": rs.metadata.creation_timestamp.isoformat()
                    if rs.metadata.creation_timestamp
                    else None,
                    "replicas": rs.status.replicas,
                    "ready": rs.status.ready_replicas,
                    "image": (
                        rs.spec.template.spec.containers[0].image
                        if rs.spec.template.spec.containers
                        else None
                    ),
                }
            )
        return {"status": "ok", "deployment": deployment, "history": entries}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def pod_logs(namespace: str, selector: str, lines: int = 80, since: str = "10m") -> dict[str, Any]:
    try:
        k = _client()
        v1 = k.CoreV1Api()
        pods = v1.list_namespaced_pod(namespace, label_selector=selector)
        if not pods.items:
            return {"status": "no_pods", "selector": selector}
        pod_name = pods.items[0].metadata.name

        # convert since string to seconds
        unit = since[-1]
        val = int(since[:-1])
        secs = val * {"s": 1, "m": 60, "h": 3600}.get(unit, 60)

        log_text = v1.read_namespaced_pod_log(
            pod_name,
            namespace,
            tail_lines=lines,
            since_seconds=secs,
        )
        return {"status": "ok", "pod": pod_name, "logs": log_text}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
