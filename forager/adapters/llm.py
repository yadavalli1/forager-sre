"""LLM adapter — supports Claude (default) and OpenAI."""
from __future__ import annotations
import json
import os
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


def call(
    model: str,
    messages: list[dict],
    system: str = "",
) -> LLMResponse:
    if _is_claude(model):
        return _call_claude(model, messages, system)
    if _is_openai(model):
        return _call_openai(model, messages, system)
    raise ValueError(f"Unknown model '{model}'. Set FORAGER_MODEL to a Claude or OpenAI model name.")


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
