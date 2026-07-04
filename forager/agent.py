"""Core investigation loop: observe → correlate → hypothesize → verify."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import config as cfg_mod
from . import runbooks as runbooks_mod
from . import store
from .adapters import github, kubernetes, llm, prometheus, slack

# Audit trail: every tool call the agent makes is logged here.
audit_log = logging.getLogger("forager.audit")

# Wall-clock budget per investigation, to bound token spend and runaway loops.
DEFAULT_TIMEOUT_S = int(os.environ.get("FORAGER_TIMEOUT_S", "300"))

SYSTEM_PROMPT = """\
You are forager-sre, an autonomous SRE investigation agent.
Your job is to investigate alerts by querying real telemetry — metrics, pod status, \
deploy history, GitHub commits, and logs — then produce a concise root-cause analysis.

Rules:
- Always start by checking the four golden signals for the affected service \
  (latency, traffic, errors, saturation).
- Check for recent GitHub commits and Kubernetes deploys before concluding there is a novel bug.
- Cite every claim with the specific metric value, log line, commit SHA, or deploy that \
  supports it.
- When you have enough evidence, stop calling tools and write your final analysis.

Output format for your final answer (no tools):
ROOT CAUSE: <one sentence>
EVIDENCE:
- <metric / log / deploy / commit that proves it>
- ...
REMEDIATION:
- <step 1>
- ...
"""


@dataclass
class Finding:
    tool: str
    input: dict[str, Any]
    result: dict[str, Any]


@dataclass
class Investigation:
    incident_id: str
    service: str
    alert: str
    description: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    findings: list[Finding] = field(default_factory=list)
    conclusion: str = ""
    slack_ts: str = ""

    def evidence_lines(self) -> list[str]:
        return [
            f"{f.tool}({json.dumps(f.input)[:60]}) → {str(f.result)[:120]}"
            for f in self.findings
            if f.result.get("status") == "ok"
        ]


def _execute_tool(name: str, inp: dict, cfg: cfg_mod.Config) -> dict[str, Any]:
    if name == "query_metrics":
        return prometheus.query(cfg.prometheus.url, inp["query"], inp.get("range", "5m"))
    if name == "get_pod_status":
        return kubernetes.pod_status(inp["namespace"], inp["selector"])
    if name == "get_recent_deploys":
        return kubernetes.recent_deploys(inp["namespace"], inp["deployment"])
    if name == "get_pod_logs":
        return kubernetes.pod_logs(
            inp["namespace"],
            inp["selector"],
            inp.get("lines", 80),
            inp.get("since", "10m"),
        )
    if name == "get_github_commits":
        return github.recent_commits(
            inp["repo"],
            token=cfg.github_token,
            since_hours=inp.get("since_hours", 6),
        )
    if name == "search_past_incidents":
        incidents = store.search_similar(inp.get("service", ""), inp.get("alert", ""))
        return {"status": "ok", "incidents": incidents}
    return {"status": "error", "error": f"Unknown tool: {name}"}


def investigate(
    incident_id: str,
    service: str,
    alert: str,
    description: str = "",
    max_seconds: int | None = None,
) -> Investigation:
    cfg = cfg_mod.load()
    inv = Investigation(
        incident_id=incident_id,
        service=service,
        alert=alert,
        description=description,
    )

    # Runbooks: inject matching guidance into the system prompt and honor
    # exclusion rules by removing those tools from the schema entirely.
    system = SYSTEM_PROMPT
    excluded: set[str] = set()
    for rb in runbooks_mod.load_matching(alert, service, cfg.runbooks_dir):
        excluded.update(rb.exclude_tools)
        if rb.notes:
            system += f"\n\nRunbook guidance for this alert ({rb.path}):\n{rb.notes}"
    tools = [t for t in llm.TOOLS if t["name"] not in excluded]

    user_msg = (
        f"Incident: {incident_id}\n"
        f"Alert: {alert}\n"
        f"Service: {service}\n"
        f"Description: {description or 'N/A'}\n\n"
        "Please investigate and provide your root-cause analysis."
    )
    messages: list[dict] = [{"role": "user", "content": user_msg}]

    deadline = time.monotonic() + (max_seconds if max_seconds is not None else DEFAULT_TIMEOUT_S)
    for _ in range(12):  # iteration safety cap
        if time.monotonic() > deadline:
            audit_log.warning("%s: investigation timed out", incident_id)
            inv.conclusion = (
                "INVESTIGATION TIMED OUT before reaching a conclusion. "
                f"Partial evidence gathered ({len(inv.findings)} tool calls) — human follow-up needed."
            )
            break

        resp = llm.call(cfg.model, messages, system=system, tools=tools)

        if resp.stop_reason == "end_turn":
            inv.conclusion = resp.text
            break

        messages.append({"role": "assistant", "content": resp.raw_content})

        tool_results = []
        for tc in resp.tool_calls:
            result = _execute_tool(tc.name, tc.input, cfg)
            audit_log.info(
                "%s: tool=%s input=%s status=%s",
                incident_id,
                tc.name,
                json.dumps(tc.input)[:200],
                result.get("status", "?"),
            )
            inv.findings.append(Finding(tool=tc.name, input=tc.input, result=result))
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(result),
                }
            )

        messages.append({"role": "user", "content": tool_results})

    # Post to Slack if configured
    if cfg.slack.token:
        blocks = slack.investigation_blocks(inv.incident_id, inv.conclusion, inv.evidence_lines())
        result = slack.post(
            cfg.slack.token,
            cfg.slack.channel,
            text=f"[{incident_id}] {alert}",
            blocks=blocks,
        )
        inv.slack_ts = result.get("ts", "")

    return inv
