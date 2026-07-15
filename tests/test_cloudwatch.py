"""Tests for the CloudWatch adapter (mocked boto3)."""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


def _dp(ts, val, stat="Sum"):
    return {"Timestamp": ts, stat: val}


def test_query_cloudwatch_no_credentials_uses_default_chain():
    """When credentials are empty, boto3.Session is still created (uses IAM role / env)."""
    from forager.adapters import cloudwatch as cw_adapter
    mock_session = MagicMock()
    mock_client = MagicMock()
    mock_session.client.return_value = mock_client
    mock_client.get_metric_statistics.return_value = {
        "Datapoints": [_dp(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc), 5.0)]
    }
    with patch("boto3.Session", return_value=mock_session):
        result = cw_adapter.query_cloudwatch_metrics(
            "us-east-1", "", "", "AWS/Lambda", "Errors", window="15m"
        )
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["datapoints"][0]["sum"] == 5.0
    # Session called with only region (no explicit creds)
    assert mock_session.client.call_args[0][0] == "cloudwatch"


def test_query_cloudwatch_no_data():
    from forager.adapters import cloudwatch as cw_adapter
    mock_session = MagicMock()
    mock_client = MagicMock()
    mock_session.client.return_value = mock_client
    mock_client.get_metric_statistics.return_value = {"Datapoints": []}
    with patch("boto3.Session", return_value=mock_session):
        result = cw_adapter.query_cloudwatch_metrics(
            "us-east-1", "ak", "sk", "AWS/Lambda", "Invocations"
        )
    assert result["status"] == "no_data"
    assert result["datapoints"] == []


def test_query_cloudwatch_boto3_not_installed():
    from forager.adapters import cloudwatch as cw_adapter
    with patch("builtins.__import__", side_effect=ImportError("no boto3")):
        result = cw_adapter.query_cloudwatch_metrics(
            "us-east-1", "ak", "sk", "AWS/Lambda", "Errors"
        )
    assert result["status"] == "error"
    assert "boto3" in result["error"]


def test_query_cloudwatch_client_error():
    from forager.adapters import cloudwatch as cw_adapter
    mock_session = MagicMock()
    mock_client = MagicMock()
    mock_session.client.return_value = mock_client
    mock_client.get_metric_statistics.side_effect = Exception("AccessDenied")
    with patch("boto3.Session", return_value=mock_session):
        result = cw_adapter.query_cloudwatch_metrics(
            "us-east-1", "ak", "sk", "AWS/Lambda", "Errors"
        )
    assert result["status"] == "error"
    assert "AccessDenied" in result["error"]


def test_query_cloudwatch_sorts_by_timestamp():
    from forager.adapters import cloudwatch as cw_adapter
    mock_session = MagicMock()
    mock_client = MagicMock()
    mock_session.client.return_value = mock_client
    later = datetime(2026, 6, 28, 12, 5, tzinfo=timezone.utc)
    earlier = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
    mock_client.get_metric_statistics.return_value = {
        "Datapoints": [_dp(later, 7.0), _dp(earlier, 3.0)]
    }
    with patch("boto3.Session", return_value=mock_session):
        result = cw_adapter.query_cloudwatch_metrics(
            "us-east-1", "ak", "sk", "AWS/Lambda", "Errors"
        )
    assert result["datapoints"][0]["sum"] == 3.0
    assert result["datapoints"][1]["sum"] == 7.0