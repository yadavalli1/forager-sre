"""Guarded remediation: allowlisted, human-approved, snapshot-backed, undoable.

Safety model (STRATUS-style transactional no-regression):
- Only actions in ALLOWED_ACTIONS can ever run — free-form commands are
  structurally impossible.
- Every action is recorded as 'proposed' first; execution is a separate,
  explicit, human-initiated step (CLI --yes or dry-run default).
- Before executing, the prior state is snapshotted so the action can be
  undone with `forager remediate-undo`.
- The agent itself never calls these functions; its remediation output is
  suggest-only text.
"""

from __future__ import annotations

import json
from typing import Any

from . import store
from .adapters import kubernetes
from .agent import audit_log

# action name → required params
ALLOWED_ACTIONS: dict[str, list[str]] = {
    "restart_deployment": ["namespace", "deployment"],
    "scale_deployment": ["namespace", "deployment", "replicas"],
    "rollback_deployment": ["namespace", "deployment"],
}


def propose(incident_id: str, action: str, params: dict) -> int:
    """Record a remediation proposal. Raises ValueError for disallowed actions."""
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Action '{action}' is not allowlisted. Allowed: {sorted(ALLOWED_ACTIONS)}")
    missing = [p for p in ALLOWED_ACTIONS[action] if p not in params]
    if missing:
        raise ValueError(f"Missing required params for {action}: {missing}")
    rid = store.add_remediation(incident_id, action, params)
    audit_log.info("%s: remediation #%s proposed: %s %s", incident_id, rid, action, json.dumps(params))
    return rid


def execute(remediation_id: int, dry_run: bool = True) -> dict[str, Any]:
    """Execute a proposed remediation. Dry-run by default; snapshots prior state."""
    rec = store.get_remediation(remediation_id)
    if not rec:
        return {"status": "error", "error": f"remediation #{remediation_id} not found"}
    if rec["status"] not in ("proposed", "failed"):
        return {
            "status": "error",
            "error": f"remediation #{remediation_id} is '{rec['status']}', not executable",
        }

    action = rec["action"]
    params = json.loads(rec["params_json"])
    if dry_run:
        return {
            "status": "dry_run",
            "action": action,
            "params": params,
            "note": "no changes made; re-run with --yes to execute",
        }

    if action == "restart_deployment":
        result = kubernetes.restart_deployment(params["namespace"], params["deployment"])
    elif action == "scale_deployment":
        result = kubernetes.scale_deployment(
            params["namespace"], params["deployment"], int(params["replicas"])
        )
    elif action == "rollback_deployment":
        result = kubernetes.rollback_deployment(params["namespace"], params["deployment"])
    else:  # unreachable if proposed via propose(), but defend anyway
        return {"status": "error", "error": f"action '{action}' not allowlisted"}

    if result.get("status") == "ok":
        store.update_remediation(
            remediation_id, "executed", snapshot=result.get("snapshot", {}), result=result
        )
        audit_log.info("%s: remediation #%s executed: %s", rec["incident_id"], remediation_id, action)
    else:
        store.update_remediation(remediation_id, "failed", result=result)
        audit_log.warning(
            "%s: remediation #%s FAILED: %s → %s",
            rec["incident_id"],
            remediation_id,
            action,
            result.get("error"),
        )
    return result


def undo(remediation_id: int) -> dict[str, Any]:
    """Revert an executed remediation using its snapshot."""
    rec = store.get_remediation(remediation_id)
    if not rec:
        return {"status": "error", "error": f"remediation #{remediation_id} not found"}
    if rec["status"] != "executed":
        return {
            "status": "error",
            "error": f"remediation #{remediation_id} is '{rec['status']}', nothing to undo",
        }

    action = rec["action"]
    params = json.loads(rec["params_json"])
    snapshot = json.loads(rec["snapshot_json"] or "{}")

    if action == "scale_deployment" and "replicas" in snapshot:
        result = kubernetes.scale_deployment(
            params["namespace"], params["deployment"], int(snapshot["replicas"])
        )
    elif action == "rollback_deployment" and "template" in snapshot:
        result = kubernetes.patch_deployment_template(
            params["namespace"], params["deployment"], snapshot["template"]
        )
    elif action == "restart_deployment":
        # A restart has no meaningful prior state; undoing is another restart.
        result = kubernetes.restart_deployment(params["namespace"], params["deployment"])
    else:
        return {"status": "error", "error": f"no snapshot available to undo '{action}'"}

    if result.get("status") == "ok":
        store.update_remediation(remediation_id, "undone", result=result)
        audit_log.info("%s: remediation #%s undone", rec["incident_id"], remediation_id)
    return result
