"""Core investigation loop: observe → correlate → hypothesize → verify."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import config as cfg_mod
from .adapters import llm, prometheus, kubernetes, slack
from .adapters import github, alertmanager, loki, jaeger, datadog
from .adapters import cloudwatch, sentry, argocd, pagerduty, jira

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

ONECALL_SYSTEM_PROMPT = """\
You are forager-sre, an autonomous one-call SRE agent.
A human operator gives you a free-form description of a situation (an alert, a symptom, \
a service name, or a vague "something is wrong"). Your job is to triage it end-to-end:

1. If the query names a specific service or alert, treat that as the target and start \
   investigating it directly — check the four golden signals (latency, traffic, errors, \
   saturation) for that service.
2. If the query is vague or does not name a target, call `list_firing_alerts` first to \
   discover active incidents in Alertmanager, then pick the most relevant one (or investigate \
   all of them if several are firing).
3. Once you have a target, investigate it like a normal SRE investigation: query metrics, \
   check pod status / restarts / logs, recent Kubernetes deploys, and recent GitHub commits.
4. Cite every claim with the specific metric value, log line, commit SHA, or deploy that \
   supports it.
5. When you have enough evidence, stop calling tools and write your final analysis.

Output format for your final answer (no tools):
ROOT CAUSE: <one sentence>
EVIDENCE:
- <metric / log / deploy / commit that proves it>
- ...
REMEDIATION:
- <step 1>
- ...
"""

# Safety cap on LLM tool-calling turns for any single investigation.
_MAX_TURNS = 12


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
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
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
    if name == "list_firing_alerts":
        am_url = cfg.prometheus.url.replace(":9090", ":9093")
        return alertmanager.list_firing_alerts(am_url)
    if name == "query_loki_logs":
        return loki.query_loki_logs(
            cfg.loki.url,
            inp["logql"],
            inp.get("limit", 100),
            inp.get("since", "15m"),
        )
    if name == "find_jaeger_traces":
        return jaeger.find_traces(
            cfg.jaeger.url,
            inp["service"],
            inp.get("operation", ""),
            inp.get("limit", 20),
        )
    if name == "get_jaeger_trace":
        return jaeger.get_trace(cfg.jaeger.url, inp["trace_id"])
    if name == "query_datadog_metrics":
        return datadog.query_datadog_metrics(
            cfg.datadog.api_key,
            cfg.datadog.app_key,
            cfg.datadog.site,
            inp["query"],
            inp.get("window", "5m"),
        )
    if name == "query_cloudwatch_metrics":
        return cloudwatch.query_cloudwatch_metrics(
            cfg.cloudwatch.region,
            cfg.cloudwatch.access_key_id,
            cfg.cloudwatch.secret_access_key,
            inp["namespace"],
            inp["metric_name"],
            inp.get("dimensions"),
            inp.get("window", "15m"),
            inp.get("period", 60),
        )
    if name == "get_sentry_errors":
        return sentry.get_sentry_errors(
            cfg.sentry.token,
            cfg.sentry.organization,
            cfg.sentry.project,
        )
    if name == "get_argocd_app_status":
        return argocd.get_argocd_app_status(
            cfg.argocd.url,
            cfg.argocd.token,
            inp["app_name"],
        )
    if name == "list_pagerduty_incidents":
        return pagerduty.list_pagerduty_incidents(
            cfg.pagerduty.token,
            inp.get("status", "triggered,acknowledged"),
        )
    if name == "search_jira_issues":
        return jira.search_jira_issues(
            cfg.jira.url,
            cfg.jira.email,
            cfg.jira.token,
            inp["jql"],
            inp.get("limit", 20),
        )
    return {"status": "error", "error": f"Unknown tool: {name}"}


def _run_loop(
    inv: Investigation,
    messages: list[dict],
    system: str,
    cfg: cfg_mod.Config,
) -> Investigation:
    """Shared LLM tool-calling loop for structured investigations and one-call queries."""
    for _ in range(_MAX_TURNS):
        resp = llm.call(cfg.model, messages, system=system)

        if resp.stop_reason == "end_turn":
            inv.conclusion = resp.text
            break

        messages.append({"role": "assistant", "content": resp.raw_content})

        tool_results = []
        for tc in resp.tool_calls:
            result = _execute_tool(tc.name, tc.input, cfg)
            inv.findings.append(Finding(tool=tc.name, input=tc.input, result=result))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

    return inv


def _post_to_slack_if_configured(inv: Investigation, cfg: cfg_mod.Config) -> None:
    if cfg.slack.token:
        blocks = slack.investigation_blocks(
            inv.incident_id, inv.conclusion, inv.evidence_lines()
        )
        result = slack.post(
            cfg.slack.token,
            cfg.slack.channel,
            text=f"[{inv.incident_id}] {inv.alert}",
            blocks=blocks,
        )
        inv.slack_ts = result.get("ts", "")


def investigate(
    incident_id: str,
    service: str,
    alert: str,
    description: str = "",
) -> Investigation:
    cfg = cfg_mod.load()
    inv = Investigation(
        incident_id=incident_id,
        service=service,
        alert=alert,
        description=description,
    )

    user_msg = (
        f"Incident: {incident_id}\n"
        f"Alert: {alert}\n"
        f"Service: {service}\n"
        f"Description: {description or 'N/A'}\n\n"
        "Please investigate and provide your root-cause analysis."
    )
    messages: list[dict] = [{"role": "user", "content": user_msg}]
    inv = _run_loop(inv, messages, SYSTEM_PROMPT, cfg)
    _post_to_slack_if_configured(inv, cfg)
    return inv


def onecall(query: str) -> Investigation:
    """One-call SRE agent: a free-form natural-language query drives the whole triage.

    The LLM decides for itself whether to discover firing alerts via
    `list_firing_alerts` (when the query is vague) or to investigate a named
    service directly. A synthetic incident_id is generated so the result is
    still persisted and deduplicated like a structured investigation.
    """
    cfg = cfg_mod.load()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    inv = Investigation(
        incident_id=f"OC-{ts}",
        service="unknown",
        alert=query.strip().splitlines()[0][:80] if query.strip() else "one-call",
        description=query,
    )

    messages: list[dict] = [{"role": "user", "content": query}]
    inv = _run_loop(inv, messages, ONECALL_SYSTEM_PROMPT, cfg)
    _post_to_slack_if_configured(inv, cfg)
    return inv
