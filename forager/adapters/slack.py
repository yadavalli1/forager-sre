"""Slack adapter — post investigation results to a channel."""
from __future__ import annotations
from typing import Any


def post(token: str, channel: str, text: str, blocks: list | None = None) -> dict[str, Any]:
    if not token:
        return {"status": "skipped", "reason": "SLACK_TOKEN not set"}
    try:
        from slack_sdk import WebClient  # type: ignore
        from slack_sdk.errors import SlackApiError

        client = WebClient(token=token)
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        resp = client.chat_postMessage(**kwargs)
        return {"status": "ok", "ts": resp["ts"], "channel": resp["channel"]}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def investigation_blocks(incident_id: str, conclusion: str, evidence: list[str]) -> list[dict]:
    bullet_lines = "\n".join(f"• {e}" for e in evidence[:8])
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔍 forager-sre · {incident_id}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Root-cause analysis*\n{conclusion}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Evidence*\n{bullet_lines}"},
        },
        {"type": "divider"},
    ]
