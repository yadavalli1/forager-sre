"""Tests for LLM retry / backoff logic."""
import pytest
from unittest.mock import patch, MagicMock, call


def _end_turn_response(text: str = "ROOT CAUSE: ok"):
    from forager.adapters.llm import LLMResponse
    return LLMResponse(stop_reason="end_turn", text=text, tool_calls=[], raw_content=[])


def test_retries_on_overloaded(monkeypatch):
    """Should retry up to max_retries on overloaded errors."""
    from forager.adapters import llm

    attempt = {"n": 0}

    def flaky_claude(*args, **kwargs):
        attempt["n"] += 1
        if attempt["n"] < 3:
            raise Exception("overloaded_error: model is overloaded")
        return _end_turn_response()

    monkeypatch.setattr(llm, "_call_claude", flaky_claude)
    monkeypatch.setattr("time.sleep", lambda _: None)  # no real sleeping

    result = llm.call("claude-sonnet-4-6", [{"role": "user", "content": "hi"}], max_retries=3)
    assert result.stop_reason == "end_turn"
    assert attempt["n"] == 3


def test_retries_on_rate_limit(monkeypatch):
    from forager.adapters import llm

    attempt = {"n": 0}

    def flaky(*args, **kwargs):
        attempt["n"] += 1
        if attempt["n"] == 1:
            raise Exception("rate_limit exceeded")
        return _end_turn_response()

    monkeypatch.setattr(llm, "_call_claude", flaky)
    monkeypatch.setattr("time.sleep", lambda _: None)

    result = llm.call("claude-sonnet-4-6", [{"role": "user", "content": "hi"}], max_retries=3)
    assert result.stop_reason == "end_turn"
    assert attempt["n"] == 2


def test_does_not_retry_non_transient(monkeypatch):
    """Non-transient errors (e.g. invalid key) should raise immediately."""
    from forager.adapters import llm

    attempt = {"n": 0}

    def bad_key(*args, **kwargs):
        attempt["n"] += 1
        raise Exception("authentication_error: invalid API key")

    monkeypatch.setattr(llm, "_call_claude", bad_key)
    monkeypatch.setattr("time.sleep", lambda _: None)

    with pytest.raises(Exception, match="authentication_error"):
        llm.call("claude-sonnet-4-6", [{"role": "user", "content": "hi"}], max_retries=3)
    assert attempt["n"] == 1  # raised immediately, no retries


def test_raises_after_max_retries(monkeypatch):
    """If all retries are exhausted, the last exception is re-raised."""
    from forager.adapters import llm

    monkeypatch.setattr(llm, "_call_claude", lambda *a, **k: (_ for _ in ()).throw(Exception("overloaded")))
    monkeypatch.setattr("time.sleep", lambda _: None)

    with pytest.raises(Exception, match="overloaded"):
        llm.call("claude-sonnet-4-6", [{"role": "user", "content": "hi"}], max_retries=2)


def test_unknown_model_never_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    from forager.adapters import llm
    with pytest.raises(ValueError, match="Unknown model"):
        llm.call("unsupported-model-xyz", [], max_retries=3)
