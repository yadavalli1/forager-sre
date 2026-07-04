"""Slack adapter — post investigation results to a channel."""

from __future__ import annotations

from typing import Any


def post(token: str, channel: str, text: str, blocks: list | None = None) -> dict[str, Any]:
    if not token:
        return {"status": "skipped", "reason": "SLACK_TOKEN not set"}
    try:
        from slack_sdk import WebClient  # type: ignore

        client = WebClient(token=token)
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        resp = client.chat_postMessage(**kwargs)
        return {"status": "ok", "ts": resp["ts"], "channel": resp["channel"]}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def update(token: str, channel: str, ts: str, text: str, blocks: list | None = None) -> dict[str, Any]:
    """Update a previously posted message (live investigation progress)."""
    if not token or not ts:
        return {"status": "skipped", "reason": "no token or message ts"}
    try:
        from slack_sdk import WebClient  # type: ignore

        client = WebClient(token=token)
        kwargs: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        resp = client.chat_update(**kwargs)
        return {"status": "ok", "ts": resp["ts"], "channel": resp["channel"]}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def investigation_blocks(
    incident_id: str, conclusion: str, evidence: list[str], confidence: str = ""
) -> list[dict]:
    bullet_lines = "\n".join(f"• {e}" for e in evidence[:8])
    blocks = [
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
    ]
    if confidence == "low":
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "⚠️ *Low confidence* — the agent could not fully verify this; human review needed."
                    ),
                },
            }
        )
    elif confidence:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Confidence: *{confidence}*"}],
            }
        )
    blocks.append({"type": "divider"})
    return blocks
