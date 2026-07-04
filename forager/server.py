"""FastAPI webhook server — Alertmanager, PagerDuty, and investigation history."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import agent, store

app = FastAPI(title="forager-sre", version="0.1.0")

# Cooldown window: don't re-investigate the same alert fingerprint within N minutes
DEDUP_MINUTES = int(os.environ.get("FORAGER_DEDUP_MINUTES", "30"))

# Investigations block for 20–60s each; run them off the event loop so
# concurrent webhooks don't queue behind one another.
_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("FORAGER_MAX_CONCURRENCY", "4")))


def _check_webhook_token(request: Request) -> None:
    """If FORAGER_WEBHOOK_TOKEN is set, require it in the X-Forager-Token header."""
    token = os.environ.get("FORAGER_WEBHOOK_TOKEN", "")
    if token and request.headers.get("x-forager-token") != token:
        raise HTTPException(status_code=401, detail="invalid or missing X-Forager-Token header")


# ── health / meta ─────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(UTC).isoformat()}


@app.get("/", response_class=HTMLResponse)
def root():
    html_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    try:
        with open(html_path) as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>forager-sre</h1><p>Landing page not found.</p>")


# ── investigations ────────────────────────────────────────────────────────────


@app.get("/investigations")
def list_investigations(limit: int = 50) -> list[dict]:
    return store.list_recent(limit)


@app.get("/investigations/{incident_id}")
def get_investigation(incident_id: str) -> dict:
    record = store.get(incident_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Investigation '{incident_id}' not found")
    return record


# ── dashboard ─────────────────────────────────────────────────────────────────


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    records = store.list_recent(100)
    rows = ""
    for r in records:
        duration = f"{r['duration_s']:.1f}s" if r.get("duration_s") else "—"
        conclusion_preview = (r.get("conclusion") or "")[:80].replace("<", "&lt;")
        rows += (
            f"<tr>"
            f"<td>{r['id']}</td>"
            f"<td>{r['service']}</td>"
            f"<td>{r['alert']}</td>"
            f"<td>{r['started_at'][:19].replace('T', ' ')}</td>"
            f"<td>{duration}</td>"
            f"<td>{r['findings_count']}</td>"
            f"<td title='{conclusion_preview}'>{conclusion_preview}…</td>"
            f"</tr>\n"
        )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>forager-sre · dashboard</title>
<style>
  body {{ font-family: 'IBM Plex Mono', monospace; background: #07090a; color: #cdd8d3;
          margin: 0; padding: 32px; }}
  h1   {{ color: #4ade80; font-size: 20px; margin-bottom: 24px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th   {{ text-align: left; color: #4ade80; padding: 8px 12px;
          border-bottom: 1px solid #1d2724; }}
  td   {{ padding: 8px 12px; border-bottom: 1px solid #0f1614;
          max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  tr:hover td {{ background: #0c1110; }}
  .empty {{ color: #5e6b64; padding: 24px 12px; }}
</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=swap">
</head>
<body>
<h1>◇ forager-sre · investigations</h1>
<table>
  <tr><th>ID</th><th>Service</th><th>Alert</th><th>Started</th>
      <th>Duration</th><th>Findings</th><th>Conclusion</th></tr>
  {"".join(rows) if rows else '<tr><td colspan="7" class="empty">No investigations yet.</td></tr>'}
</table>
</body>
</html>"""
    return HTMLResponse(html)


# ── webhooks ──────────────────────────────────────────────────────────────────


def _run_investigation(
    incident_id: str, service: str, alert_name: str, desc: str, fingerprint: str
) -> dict[str, Any]:
    if store.is_duplicate(fingerprint, DEDUP_MINUTES):
        return {
            "incident_id": incident_id,
            "status": "deduplicated",
            "reason": f"already investigated within {DEDUP_MINUTES}m",
        }
    store.mark_fingerprint(fingerprint)
    inv = agent.investigate(incident_id, service, alert_name, desc)
    store.save(inv)
    return {
        "incident_id": inv.incident_id,
        "service": inv.service,
        "alert": inv.alert,
        "started_at": inv.started_at.isoformat(),
        "conclusion": inv.conclusion,
        "findings": len(inv.findings),
        "status": "ok",
    }


async def _run_all(jobs: list[tuple]) -> list[dict[str, Any]]:
    """Run investigations concurrently in the thread pool, off the event loop."""
    loop = asyncio.get_running_loop()
    futures = [loop.run_in_executor(_pool, _run_investigation, *job) for job in jobs]
    return list(await asyncio.gather(*futures))


@app.post("/webhook/alertmanager")
async def alertmanager_webhook(request: Request) -> JSONResponse:
    _check_webhook_token(request)
    body: dict[str, Any] = await request.json()
    jobs = []
    batch_fps: set[str] = set()
    for alert in body.get("alerts", []):
        if alert.get("status") != "firing":
            continue
        labels = alert.get("labels", {})
        fingerprint = alert.get("fingerprint", "")
        if fingerprint and fingerprint in batch_fps:
            continue
        batch_fps.add(fingerprint)
        name = labels.get("alertname", "UnknownAlert")
        svc = labels.get("service", labels.get("job", "unknown"))
        inc_id = f"INC-{fingerprint[:6].upper()}" if fingerprint else f"INC-{name[:6].upper()}"
        annotations = alert.get("annotations", {})
        desc = annotations.get("description", annotations.get("summary", ""))
        jobs.append((inc_id, svc, name, desc, fingerprint or inc_id))
    results = await _run_all(jobs)
    return JSONResponse({"processed": len(results), "investigations": results})


@app.post("/webhook/pagerduty")
async def pagerduty_webhook(request: Request) -> JSONResponse:
    _check_webhook_token(request)
    body: dict[str, Any] = await request.json()
    jobs = []
    for event in body.get("events", []):
        if event.get("event_type") not in ("incident.triggered", "incident.acknowledged"):
            continue
        data = event.get("data", {})
        number = data.get("number", "???")
        title = data.get("title", "Unknown alert")
        svc = data.get("service", {}).get("name", "unknown")
        desc = data.get("body", {}).get("details", "")
        inc_id = f"PD-{number}"
        jobs.append((inc_id, svc, title, desc, inc_id))
    results = await _run_all(jobs)
    return JSONResponse({"processed": len(results), "investigations": results})
