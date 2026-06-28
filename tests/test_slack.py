"""Tests for the Slack adapter."""
import pytest
from unittest.mock import patch, MagicMock


def test_post_no_token():
    from forager.adapters.slack import post
    result = post("", "#incidents", "test message")
    assert result["status"] == "skipped"


def test_post_ok():
    from forager.adapters.slack import post
    mock_client = MagicMock()
    mock_client.chat_postMessage.return_value = {"ts": "1234567890.123", "channel": "C123"}
    with patch("slack_sdk.WebClient", return_value=mock_client):
        result = post("xoxb-test-token", "#incidents", "test message")
    assert result["status"] == "ok"
    assert result["ts"] == "1234567890.123"


def test_post_error():
    from forager.adapters.slack import post
    mock_client = MagicMock()
    mock_client.chat_postMessage.side_effect = Exception("channel_not_found")
    with patch("slack_sdk.WebClient", return_value=mock_client):
        result = post("xoxb-test-token", "#bad-channel", "test")
    assert result["status"] == "error"
    assert "channel_not_found" in result["error"]


def test_investigation_blocks_structure():
    from forager.adapters.slack import investigation_blocks
    blocks = investigation_blocks(
        "INC-4827",
        "Root cause: connection pool exhaustion on db-primary",
        ["query_metrics → p99=480ms", "get_pod_logs → 'pool exhausted' x42"],
    )
    assert len(blocks) >= 3
    types = [b["type"] for b in blocks]
    assert "header" in types
    assert "section" in types

    header_text = next(b for b in blocks if b["type"] == "header")["text"]["text"]
    assert "INC-4827" in header_text


def test_investigation_blocks_truncates_evidence():
    from forager.adapters.slack import investigation_blocks
    evidence = [f"finding {i}" for i in range(20)]
    blocks = investigation_blocks("INC-1", "conclusion", evidence)
    # Only first 8 evidence items included
    section_text = next(
        b["text"]["text"] for b in blocks
        if b.get("type") == "section" and "Evidence" in b.get("text", {}).get("text", "")
    )
    assert "finding 7" in section_text
    assert "finding 8" not in section_text
