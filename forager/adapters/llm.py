"""LLM adapter — Claude and OpenAI natively, everything else via LiteLLM."""

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
        "name": "search_logs",
        "description": (
            "Search logs across services with a LogQL query (Loki). Unlike get_pod_logs, this "
            'greps historical logs across all pods, e.g. \'{app="api"} |= "error"\'.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "LogQL query"},
                "since": {"type": "string", "description": "Look-back window, e.g. '10m'", "default": "10m"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_past_incidents",
        "description": (
            "Search past investigations for similar incidents (same service or similar alert name) "
            "and their concluded root causes. Use this early — recurring incidents often share a cause."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name to match"},
                "alert": {"type": "string", "description": "Alert name (substring match)"},
            },
            "required": [],
        },
    },
]


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """Translate forager tool schemas to OpenAI function format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
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
    # LiteLLM models are provider-prefixed ("bedrock/...", "ollama/..."); a slash
    # means the model is not for the native Anthropic client even if it says "claude".
    return "/" not in model and ("claude" in model.lower() or model.startswith("us.anthropic"))


def _is_openai(model: str) -> bool:
    return "/" not in model and model.startswith(("gpt-", "o1", "o3"))


_RETRYABLE = ("529", "overloaded", "rate_limit", "rate limit", "529", "503", "502")


def call(
    model: str,
    messages: list[dict],
    system: str = "",
    max_retries: int = 3,
    tools: list[dict] | None = None,
) -> LLMResponse:
    """Call the LLM with exponential backoff on transient errors."""
    if tools is None:
        tools = TOOLS
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(max_retries):
        try:
            if _is_claude(model):
                return _call_claude(model, messages, system, tools)
            if _is_openai(model):
                return _call_openai(model, messages, system, tools)
            return _call_litellm(model, messages, system, tools)
        except ValueError:
            raise
        except Exception as exc:
            last_exc = exc
            if any(tag in str(exc).lower() for tag in _RETRYABLE):
                wait = 2**attempt
                time.sleep(wait)
                continue
            raise
    raise last_exc


# ── Claude ────────────────────────────────────────────────────────────────────


def _call_claude(model: str, messages: list[dict], system: str, tools: list[dict]) -> LLMResponse:
    import anthropic  # lazy import

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    kwargs: dict[str, Any] = {}
    if tools:  # tools=[] means a plain completion (e.g. postmortem generation)
        kwargs["tools"] = tools
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=messages,
        **kwargs,
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


def _parse_openai_message(msg: Any) -> LLMResponse:
    """Parse an OpenAI-format chat message (also emitted by LiteLLM) into an LLMResponse."""
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


def _call_openai(model: str, messages: list[dict], system: str, tools: list[dict]) -> LLMResponse:
    import openai  # lazy import

    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})
    oai_messages.extend(messages)

    kwargs: dict[str, Any] = {}
    if tools:
        kwargs["tools"] = _to_openai_tools(tools)
    resp = client.chat.completions.create(model=model, messages=oai_messages, **kwargs)
    return _parse_openai_message(resp.choices[0].message)


# ── LiteLLM (Bedrock, Vertex, Ollama, local, gateways) ────────────────────────


def _call_litellm(model: str, messages: list[dict], system: str, tools: list[dict]) -> LLMResponse:
    try:
        import litellm  # lazy import
    except ImportError:
        raise ValueError(
            f"Unknown model '{model}'. Use a Claude or OpenAI model name, or install the litellm "
            "extra (pip install 'forager-sre[litellm]') to route any provider — e.g. "
            "'bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0' or 'ollama/llama3'."
        ) from None

    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})
    oai_messages.extend(messages)

    kwargs: dict[str, Any] = {}
    if tools:
        kwargs["tools"] = _to_openai_tools(tools)
    resp = litellm.completion(model=model, messages=oai_messages, **kwargs)
    return _parse_openai_message(resp.choices[0].message)
