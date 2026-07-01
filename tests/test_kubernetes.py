"""Tests for the Kubernetes adapter (mocked k8s client)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


def _make_pod(name: str, phase: str = "Running", restarts: int = 0, ready: bool = True):
    pod = MagicMock()
    pod.metadata.name = name
    pod.spec.node_name = "node-1"
    pod.status.phase = phase
    cs = MagicMock()
    cs.restart_count = restarts
    cs.ready = ready
    pod.status.container_statuses = [cs]
    return pod


def _make_rs(name: str, image: str = "api:latest", replicas: int = 3, ready: int = 3):
    rs = MagicMock()
    rs.metadata.name = name
    rs.metadata.creation_timestamp = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    rs.status.replicas = replicas
    rs.status.ready_replicas = ready
    container = MagicMock()
    container.image = image
    rs.spec.template.spec.containers = [container]
    return rs


def test_pod_status_ok():
    from forager.adapters import kubernetes as k8s_adapter

    mock_pods = MagicMock()
    mock_pods.items = [
        _make_pod("api-abc12", "Running", restarts=0),
        _make_pod("api-def34", "Running", restarts=2),
    ]
    with patch.object(k8s_adapter, "_client") as mock_client:
        mock_v1 = MagicMock()
        mock_v1.list_namespaced_pod.return_value = mock_pods
        mock_client.return_value.CoreV1Api.return_value = mock_v1
        result = k8s_adapter.pod_status("default", "app=api")

    assert result["status"] == "ok"
    assert len(result["pods"]) == 2
    assert result["pods"][1]["restarts"] == 2


def test_pod_status_no_pods():
    from forager.adapters import kubernetes as k8s_adapter

    mock_pods = MagicMock()
    mock_pods.items = []
    with patch.object(k8s_adapter, "_client") as mock_client:
        mock_v1 = MagicMock()
        mock_v1.list_namespaced_pod.return_value = mock_pods
        mock_client.return_value.CoreV1Api.return_value = mock_v1
        result = k8s_adapter.pod_status("default", "app=missing")

    assert result["status"] == "no_pods"


def test_pod_status_error():
    from forager.adapters import kubernetes as k8s_adapter

    with patch.object(k8s_adapter, "_client", side_effect=Exception("kubeconfig not found")):
        result = k8s_adapter.pod_status("default", "app=api")
    assert result["status"] == "error"
    assert "kubeconfig" in result["error"]


def test_recent_deploys_ok():
    from forager.adapters import kubernetes as k8s_adapter

    mock_rs_list = MagicMock()
    mock_rs_list.items = [
        _make_rs("api-v2", "api:v2.0", 3, 3),
        _make_rs("api-v1", "api:v1.9", 3, 0),
    ]
    with patch.object(k8s_adapter, "_client") as mock_client:
        mock_apps = MagicMock()
        mock_apps.list_namespaced_replica_set.return_value = mock_rs_list
        mock_client.return_value.AppsV1Api.return_value = mock_apps
        result = k8s_adapter.recent_deploys("default", "api")

    assert result["status"] == "ok"
    assert len(result["history"]) == 2
    assert result["history"][0]["image"] in ("api:v1.9", "api:v2.0")


def test_pod_logs_ok():
    from forager.adapters import kubernetes as k8s_adapter

    mock_pods = MagicMock()
    mock_pods.items = [_make_pod("api-abc12")]
    with patch.object(k8s_adapter, "_client") as mock_client:
        mock_v1 = MagicMock()
        mock_v1.list_namespaced_pod.return_value = mock_pods
        mock_v1.read_namespaced_pod_log.return_value = (
            "ERROR connection pool exhausted\nWARN retry attempt 3\n"
        )
        mock_client.return_value.CoreV1Api.return_value = mock_v1
        result = k8s_adapter.pod_logs("default", "app=api", lines=50, since="5m")

    assert result["status"] == "ok"
    assert result["pod"] == "api-abc12"
    assert "connection pool" in result["logs"]


def test_pod_logs_no_pods():
    from forager.adapters import kubernetes as k8s_adapter

    mock_pods = MagicMock()
    mock_pods.items = []
    with patch.object(k8s_adapter, "_client") as mock_client:
        mock_v1 = MagicMock()
        mock_v1.list_namespaced_pod.return_value = mock_pods
        mock_client.return_value.CoreV1Api.return_value = mock_v1
        result = k8s_adapter.pod_logs("default", "app=missing")

    assert result["status"] == "no_pods"
