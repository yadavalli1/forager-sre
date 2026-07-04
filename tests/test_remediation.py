"""Tests for guarded remediation: allowlist, dry-run, snapshots, undo."""

from unittest.mock import patch

import pytest

import forager.store as store_mod
from forager import remediation


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    store_mod._db = None
    store_mod._db_path = tmp_path / "test.db"
    store_mod.init(store_mod._db_path)
    yield
    if store_mod._db:
        store_mod._db.close()
        store_mod._db = None


PARAMS = {"namespace": "prod", "deployment": "api"}


def test_propose_disallowed_action_raises():
    with pytest.raises(ValueError, match="not allowlisted"):
        remediation.propose("INC-1", "delete_namespace", PARAMS)


def test_propose_missing_params_raises():
    with pytest.raises(ValueError, match="Missing required params"):
        remediation.propose("INC-1", "scale_deployment", PARAMS)  # no replicas


def test_propose_records_proposal():
    rid = remediation.propose("INC-1", "restart_deployment", PARAMS)
    rec = store_mod.get_remediation(rid)
    assert rec["status"] == "proposed"
    assert rec["incident_id"] == "INC-1"
    assert rec["action"] == "restart_deployment"


def test_execute_defaults_to_dry_run():
    rid = remediation.propose("INC-1", "restart_deployment", PARAMS)
    with patch("forager.remediation.kubernetes.restart_deployment") as mock_restart:
        result = remediation.execute(rid)  # no dry_run arg → dry run
    assert result["status"] == "dry_run"
    mock_restart.assert_not_called()
    assert store_mod.get_remediation(rid)["status"] == "proposed"  # unchanged


def test_execute_with_approval_snapshots_and_marks_executed():
    rid = remediation.propose("INC-1", "scale_deployment", {**PARAMS, "replicas": 10})
    ok = {"status": "ok", "action": "scale_deployment", "snapshot": {"replicas": 3}}
    with patch("forager.remediation.kubernetes.scale_deployment", return_value=ok) as mock_scale:
        result = remediation.execute(rid, dry_run=False)
    assert result["status"] == "ok"
    mock_scale.assert_called_once_with("prod", "api", 10)
    rec = store_mod.get_remediation(rid)
    assert rec["status"] == "executed"
    assert '"replicas": 3' in rec["snapshot_json"]


def test_execute_failure_marks_failed():
    rid = remediation.propose("INC-1", "restart_deployment", PARAMS)
    err = {"status": "error", "error": "forbidden"}
    with patch("forager.remediation.kubernetes.restart_deployment", return_value=err):
        result = remediation.execute(rid, dry_run=False)
    assert result["status"] == "error"
    assert store_mod.get_remediation(rid)["status"] == "failed"


def test_execute_twice_rejected():
    rid = remediation.propose("INC-1", "scale_deployment", {**PARAMS, "replicas": 5})
    ok = {"status": "ok", "snapshot": {"replicas": 2}}
    with patch("forager.remediation.kubernetes.scale_deployment", return_value=ok):
        remediation.execute(rid, dry_run=False)
        result = remediation.execute(rid, dry_run=False)
    assert result["status"] == "error"
    assert "not executable" in result["error"]


def test_undo_scale_restores_snapshot():
    rid = remediation.propose("INC-1", "scale_deployment", {**PARAMS, "replicas": 10})
    ok = {"status": "ok", "snapshot": {"replicas": 3}}
    with patch("forager.remediation.kubernetes.scale_deployment", return_value=ok) as mock_scale:
        remediation.execute(rid, dry_run=False)
        result = remediation.undo(rid)
    assert result["status"] == "ok"
    # second call is the undo, restoring the snapshotted replica count
    assert mock_scale.call_args_list[-1].args == ("prod", "api", 3)
    assert store_mod.get_remediation(rid)["status"] == "undone"


def test_undo_requires_executed_state():
    rid = remediation.propose("INC-1", "restart_deployment", PARAMS)
    result = remediation.undo(rid)
    assert result["status"] == "error"
    assert "nothing to undo" in result["error"]


def test_undo_unknown_id():
    assert remediation.undo(99999)["status"] == "error"


def test_agent_has_no_write_tools():
    """Structural guarantee: no remediation action is exposed as an agent tool."""
    from forager.adapters import llm

    tool_names = {t["name"] for t in llm.TOOLS}
    assert tool_names.isdisjoint(set(remediation.ALLOWED_ACTIONS))
