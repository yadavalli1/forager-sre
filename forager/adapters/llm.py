"""LLM adapter — supports Claude (default) and OpenAI."""
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

TOOLS: list[dict] = [
    {
        "name": "query_metrics",
        "description": (
            "Query Prometheus for a PromQL expression and return the current value(s). "
            "Use this to check error rates, latency percentiles, saturation, and traffic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL expression"},
                "range": {
                    "type": "string",
                    "description": "Look-back window for range queries, e.g. '5m', '1h'",
                    "default": "5m",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_pod_status",
        "description": "List Kubernetes pods matching a label selector and their current phase/restarts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Kubernetes namespace"},
                "selector": {
                    "type": "string",
                    "description": "Label selector, e.g. 'app=checkout-api'",
                },
            },
            "required": ["namespace", "selector"],
        },
    },
    {
        "name": "get_recent_deploys",
        "description": "List Kubernetes ReplicaSet rollout history to identify recent deploys.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "deployment": {"type": "string", "description": "Deployment name"},
            },
            "required": ["namespace", "deployment"],
        },
    },
    {
        "name": "get_pod_logs",
        "description": "Fetch recent log lines from the first matching pod.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "selector": {"type": "string", "description": "Label selector"},
                "lines": {"type": "integer", "default": 80},
                "since": {
                    "type": "string",
                    "description": "Duration to look back, e.g. '5m'",
                    "default": "10m",
                },
            },
            "required": ["namespace", "selector"],
        },
    },
    {
        "name": "get_github_commits",
        "description": (
            "Fetch recent commits from a GitHub repository to correlate deploys with the incident. "
            "Use this to check if a recent code change could have caused the alert."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "GitHub repo in owner/name format, e.g. 'myorg/checkout-api'",
                },
                "since_hours": {
                    "type": "integer",
                    "description": "How many hours back to look for commits",
                    "default": 6,
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "list_firing_alerts",
        "description": (
            "List currently firing alerts from Alertmanager. Use this when the user's query does "
            "not already name a specific service or alert, so you can discover active incidents "
            "to investigate. Each returned alert carries alertname, service, severity, and a summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "query_loki_logs",
        "description": (
            "Query Loki for logs matching a LogQL expression across all pods of a service. "
            "More powerful than get_pod_logs when you need to grep across many pods or filter "
            "by structured metadata. Example: '{app=\"checkout-api\"} |= \"error\"'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "logql": {"type": "string", "description": "LogQL expression"},
                "limit": {"type": "integer", "default": 100, "description": "Max lines to return"},
                "since": {"type": "string", "default": "15m", "description": "Look-back window, e.g. '5m', '1h'"},
            },
            "required": ["logql"],
        },
    },
    {
        "name": "find_jaeger_traces",
        "description": (
            "Find recent distributed traces in Jaeger for a service (optionally filtered by "
            "operation). Use this when an incident spans multiple services and you need to see "
            "where latency was spent or which span errored."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name to find traces for"},
                "operation": {"type": "string", "description": "Optional operation/span name filter"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_jaeger_trace",
        "description": (
            "Fetch a single distributed trace by its ID from Jaeger. Use this after find_jaeger_traces "
            "to drill into a specific trace that looks suspicious."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string", "description": "Jaeger trace ID"},
            },
            "required": ["trace_id"],
        },
    },
    {
        "name": "query_datadog_metrics",
        "description": (
            "Query Datadog metrics via the v2 query API. Use this instead of query_metrics when the "
            "organization uses Datadog rather than Prometheus. Example query: 'avg:system.cpu.system{*}'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Datadog metric query string"},
                "window": {"type": "string", "default": "5m", "description": "Look-back window, e.g. '5m', '1h'"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_cloudwatch_metrics",
        "description": (
            "Query AWS CloudWatch metric statistics. Use this when the affected service runs on AWS "
            "(Lambda, API Gateway, RDS, SQS, ECS, etc.). Requires AWS credentials or an IAM role."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "CloudWatch namespace, e.g. 'AWS/Lambda'"},
                "metric_name": {"type": "string", "description": "e.g. 'Errors', 'Invocations', '5XXError'"},
                "dimensions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "Name": {"type": "string"},
                            "Value": {"type": "string"},
                        },
                    },
                    "description": "Dimension filters",
                },
                "window": {"type": "string", "default": "15m"},
                "period": {"type": "integer", "default": 60, "description": "Resolution in seconds"},
            },
            "required": ["namespace", "metric_name"],
        },
    },
    {
        "name": "get_sentry_errors",
        "description": (
            "List unresolved Sentry error groups for a project over the last 24h. Use this to check "
            "whether a new error appeared or an existing one spiked after a recent deploy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_argocd_app_status",
        "description": (
            "Check an Argo CD application's sync and health status. Use this when the service is "
            "deployed via GitOps to determine whether it is out of sync or degraded, which often "
            "correlates with a problematic deploy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "Argo CD application name"},
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "list_pagerduty_incidents",
        "description": (
            "List active PagerDuty incidents (triggered or acknowledged). Use this when the query "
            "asks 'what is currently on-call and firing?' or to correlate an alert with an open PD incident."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "default": "triggered,acknowledged",
                    "description": "Comma-separated incident statuses to include",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_jira_issues",
        "description": (
            "Search Jira for issues matching a JQL query. Use this to correlate the incident with "
            "open bugs, planned changes, or known issues. Example JQL: 'project = SRE AND status != Done'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jql": {"type": "string", "description": "JQL query string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["jql"],
        },
    },
]

# Translate forager tool list to OpenAI function format
_OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TOOLS
]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    stop_reason: str  # "tool_use" | "end_turn"
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_content: Any = None  # kept for message replay


def _is_claude(model: str) -> bool:
    return "claude" in model.lower() or model.startswith("us.anthropic")


def _is_openai(model: str) -> bool:
    return model.startswith(("gpt-", "o1", "o3"))


_RETRYABLE = ("529", "overloaded", "rate_limit", "rate limit", "529", "503", "502")


def call(
    model: str,
    messages: list[dict],
    system: str = "",
    max_retries: int = 3,
) -> LLMResponse:
    """Call the LLM with exponential backoff on transient errors."""
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(max_retries):
        try:
            if _is_claude(model):
                return _call_claude(model, messages, system)
            if _is_openai(model):
                return _call_openai(model, messages, system)
            raise ValueError(f"Unknown model '{model}'. Set FORAGER_MODEL to a Claude or OpenAI model name.")
        except ValueError:
            raise
        except Exception as exc:
            last_exc = exc
            if any(tag in str(exc).lower() for tag in _RETRYABLE):
                wait = 2 ** attempt
                time.sleep(wait)
                continue
            raise
    raise last_exc


# ── Claude ────────────────────────────────────────────────────────────────────

def _call_claude(model: str, messages: list[dict], system: str) -> LLMResponse:
    import anthropic  # lazy import

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        tools=TOOLS,  # type: ignore[arg-type]
        messages=messages,
    )

    text = ""
    tool_calls: list[ToolCall] = []
    for block in resp.content:
        if block.type == "text":
            text = block.text
        elif block.type == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

    stop = "tool_use" if tool_calls else "end_turn"
    return LLMResponse(
        stop_reason=stop,
        text=text,
        tool_calls=tool_calls,
        raw_content=resp.content,
    )


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _call_openai(model: str, messages: list[dict], system: str) -> LLMResponse:
    import openai  # lazy import

    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})
    oai_messages.extend(messages)

    resp = client.chat.completions.create(
        model=model,
        tools=_OPENAI_TOOLS,  # type: ignore[arg-type]
        messages=oai_messages,
    )
    msg = resp.choices[0].message
    text = msg.content or ""
    tool_calls: list[ToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=json.loads(tc.function.arguments),
                )
            )
    stop = "tool_use" if tool_calls else "end_turn"
    return LLMResponse(stop_reason=stop, text=text, tool_calls=tool_calls, raw_content=msg)
