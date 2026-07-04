"""Postmortem generation: a blameless incident review from a stored investigation."""

from __future__ import annotations

import json

from . import config as cfg_mod
from . import store
from .adapters import llm

_PROMPT = """\
You are writing a blameless postmortem for the incident below, based on the
investigation record of an autonomous SRE agent. Write it as Markdown with
these sections:

# Postmortem: <incident id> — <alert>
## Summary          (2-3 sentences, plain language)
## Impact           (what was affected; say "unknown" if the record doesn't show it)
## Timeline         (bullet list from the record: alert fired, evidence found, conclusion)
## Root cause       (from the investigation's conclusion)
## Detection        (how the incident was detected)
## Remediation & follow-ups   (action items as checkboxes, - [ ] style)
## Lessons learned  (what would make this faster or prevent it)

Be factual: only state what the record supports; mark everything else as
unknown or "to be confirmed". Never blame individuals.
"""


def generate(incident_id: str) -> str:
    """Generate a Markdown postmortem for a stored investigation.

    Raises KeyError if the incident is unknown.
    """
    record = store.get(incident_id)
    if not record:
        raise KeyError(f"Investigation '{incident_id}' not found")

    cfg = cfg_mod.load()
    context = {
        "incident_id": record["id"],
        "service": record["service"],
        "alert": record["alert"],
        "description": record.get("description", ""),
        "started_at": record["started_at"],
        "finished_at": record.get("finished_at"),
        "duration_s": record.get("duration_s"),
        "confidence": record.get("confidence", ""),
        "conclusion": record.get("conclusion", ""),
        "evidence": json.loads(record.get("findings_json") or "[]"),
    }
    resp = llm.call(
        cfg.model,
        [{"role": "user", "content": "Investigation record:\n" + json.dumps(context, indent=2)}],
        system=_PROMPT,
        tools=[],  # plain completion — no tool use
    )
    return resp.text
