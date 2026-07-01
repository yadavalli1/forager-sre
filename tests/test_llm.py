"""Tests for the LLM adapter — Claude and OpenAI routing."""

from unittest.mock import MagicMock, patch

import pytest


def test_is_claude():
    from forager.adapters.llm import _is_claude

    assert _is_claude("claude-sonnet-4-6")
    assert _is_claude("claude-opus-4-8")
    assert _is_claude("us.anthropic.claude-3-5-sonnet")
    assert not _is_claude("gpt-4o")
    assert not _is_claude("llama3")


def test_is_openai():
    from forager.adapters.llm import _is_openai

    assert _is_openai("gpt-4o")
    assert _is_openai("gpt-4o-mini")
    assert _is_openai("o1-preview")
    assert not _is_openai("claude-sonnet-4-6")


def test_unknown_model_raises():
    from forager.adapters.llm import call

    with pytest.raises(ValueError, match="Unknown model"):
        call("llama3-local", [{"role": "user", "content": "hi"}])


def _make_claude_response(text: str = "", tool_calls: list = None):
    """Build a mock anthropic Messages response."""
    blocks = []
    if text:
        tb = MagicMock()
        tb.type = "text"
        tb.text = text
        blocks.append(tb)
    for tc in tool_calls or []:
        cb = MagicMock()
        cb.type = "tool_use"
        cb.id = tc["id"]
        cb.name = tc["name"]
        cb.input = tc["input"]
        blocks.append(cb)
    resp = MagicMock()
    resp.content = blocks
    return resp


def test_claude_end_turn():
    from forager.adapters.llm import call

    mock_resp = _make_claude_response(text="ROOT CAUSE: connection pool exhaustion")
    with patch("anthropic.Anthropic") as MockCls:
        MockCls.return_value.messages.create.return_value = mock_resp
        result = call("claude-sonnet-4-6", [{"role": "user", "content": "investigate"}])

    assert result.stop_reason == "end_turn"
    assert "connection pool" in result.text
    assert result.tool_calls == []


def test_claude_tool_use():
    from forager.adapters.llm import call

    mock_resp = _make_claude_response(
        tool_calls=[
            {"id": "tc_001", "name": "query_metrics", "input": {"query": "up"}},
        ]
    )
    with patch("anthropic.Anthropic") as MockCls:
        MockCls.return_value.messages.create.return_value = mock_resp
        result = call("claude-sonnet-4-6", [{"role": "user", "content": "investigate"}])

    assert result.stop_reason == "tool_use"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "query_metrics"
    assert result.tool_calls[0].input == {"query": "up"}


def test_claude_multiple_tool_calls():
    from forager.adapters.llm import call

    mock_resp = _make_claude_response(
        tool_calls=[
            {"id": "tc_001", "name": "query_metrics", "input": {"query": "up"}},
            {
                "id": "tc_002",
                "name": "get_pod_status",
                "input": {"namespace": "default", "selector": "app=api"},
            },
        ]
    )
    with patch("anthropic.Anthropic") as MockCls:
        MockCls.return_value.messages.create.return_value = mock_resp
        result = call("claude-sonnet-4-6", [{"role": "user", "content": "investigate"}])

    assert len(result.tool_calls) == 2
    assert result.tool_calls[1].name == "get_pod_status"


def test_tool_definitions_are_valid():
    from forager.adapters.llm import TOOLS

    for tool in TOOLS:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema
