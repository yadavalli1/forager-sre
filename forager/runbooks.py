"""YAML runbooks: per-alert investigation guidance and tool exclusion rules.

A runbook is a YAML file in the runbooks directory:

    match:
      alerts: ["HighErrorRate", "High*"]     # fnmatch patterns, case-insensitive
      services: ["api", "checkout-*"]        # optional; empty = any service
    exclude_tools: ["get_pod_logs"]          # tools the agent must not use here
    notes: |
      This alert is usually the DB connection pool. Check pg_pool_available
      first; skip log tailing — these pods log at DEBUG and it wastes context.

Evidence from production teams shows runbooks matter more than model choice:
encoding "don't use X here, check Y first" cuts wasted tool calls dramatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import yaml


@dataclass
class Runbook:
    path: str
    alerts: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    exclude_tools: list[str] = field(default_factory=list)
    notes: str = ""

    def matches(self, alert: str, service: str) -> bool:
        alert_ok = not self.alerts or any(fnmatch(alert.lower(), p.lower()) for p in self.alerts)
        service_ok = not self.services or any(fnmatch(service.lower(), p.lower()) for p in self.services)
        return alert_ok and service_ok


def _parse(path: Path) -> Runbook | None:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    match = raw.get("match") or {}
    return Runbook(
        path=str(path),
        alerts=list(match.get("alerts") or []),
        services=list(match.get("services") or []),
        exclude_tools=list(raw.get("exclude_tools") or []),
        notes=str(raw.get("notes") or "").strip(),
    )


def load_matching(alert: str, service: str, directory: str = "runbooks") -> list[Runbook]:
    """Return runbooks in `directory` whose match rules apply to this alert/service."""
    root = Path(directory)
    if not root.is_dir():
        return []
    matched = []
    for path in sorted(root.glob("*.y*ml")):
        rb = _parse(path)
        if rb and rb.matches(alert, service):
            matched.append(rb)
    return matched
