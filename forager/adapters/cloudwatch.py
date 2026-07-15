"""AWS CloudWatch adapter — query metric statistics.

When the affected service runs on AWS, its metrics (Lambda invocations, API
Gateway 5xx, RDS connections, SQS backlog, etc.) live in CloudWatch. This
adapter uses boto3 to pull a metric statistic window for the agent.
"""
from __future__ import annotations
import time
from typing import Any


def _duration_to_seconds(duration: str) -> int:
    unit = duration[-1]
    val = int(duration[:-1])
    return val * {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)


def query_cloudwatch_metrics(
    region: str,
    access_key_id: str,
    secret_access_key: str,
    namespace: str,
    metric_name: str,
    dimensions: list[dict] | None = None,
    window: str = "15m",
    period: int = 60,
    statistics: list[str] | None = None,
) -> dict[str, Any]:
    """Query CloudWatch metric statistics via get_metric_statistics.

    Args:
        region: AWS region, e.g. 'us-east-1'.
        access_key_id / secret_access_key: AWS credentials.
        namespace: CloudWatch namespace, e.g. 'AWS/Lambda'.
        metric_name: e.g. 'Errors', 'Invocations', '5XXError'.
        dimensions: list of {Name, Value} dicts.
        window: Look-back window, e.g. '15m', '1h'.
        period: Resolution in seconds.
        statistics: list of statistics, e.g. ['Sum', 'Average'].
    """
    try:
        import boto3  # lazy import; optional dependency
    except ImportError:
        return {
            "status": "error",
            "error": "boto3 is not installed. Run: pip install boto3",
            "namespace": namespace, "metric": metric_name,
        }

    if not access_key_id or not secret_access_key:
        # Fall back to the default credential chain (IAM role, env, profile).
        session = boto3.Session(region_name=region)
    else:
        session = boto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )

    try:
        cw = session.client("cloudwatch")
        now = time.time()
        secs = _duration_to_seconds(window)
        resp = cw.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions or [],
            StartTime=now - secs,
            EndTime=now,
            Period=period,
            Statistics=statistics or ["Sum", "Average"],
        )
        datapoints = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
        if not datapoints:
            return {
                "status": "no_data",
                "namespace": namespace,
                "metric": metric_name,
                "window": window,
                "datapoints": [],
            }
        rows = [
            {
                "timestamp": dp["Timestamp"].isoformat(),
                "sum": dp.get("Sum"),
                "average": dp.get("Average"),
                "max": dp.get("Maximum"),
            }
            for dp in datapoints
        ]
        return {
            "status": "ok",
            "namespace": namespace,
            "metric": metric_name,
            "window": window,
            "count": len(rows),
            "datapoints": rows,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "namespace": namespace,
            "metric": metric_name,
        }