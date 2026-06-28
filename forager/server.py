"""FastAPI webhook server — receive alerts from Alertmanager or PagerDuty."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from . import agent
from . import config as cfg_mod

app = FastAPI(title="forager-sre", version="0.1.0")

# In-memory store (replace with a DB for production)
_investigations: list[dict] = []


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/", response_class=HTMLResponse)
def root():
    """Serve the landing page."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    try:
        with open(html_path) as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>forager-sre</h1><p>Landing page not found.</p>")


@app.get("/investigations")
def list_investigations() -> list[dict]:
    return _investigations[-50:]  # last 50


@app.post("/webhook/alertmanager")
async def alertmanager_webhook(request: Request) -> JSONResponse:
    """Receive Prometheus Alertmanager webhook and kick off investigation."""
    body: dict[str, Any] = await request.json()
    results = []

    for alert in body.get("alerts", []):
        labels = alert.get("labels", {})
        fingerprint = alert.get("fingerprint", "")
        name = labels.get("alertname", "UnknownAlert")
        svc = labels.get("service", labels.get("job", "unknown"))
        inc_id = f"INC-{fingerprint[:6].upper()}" if fingerprint else f"INC-{name[:6].upper()}"
        annotations = alert.get("annotations", {})
        desc = annotations.get("description", annotations.get("summary", ""))

        if alert.get("status") != "firing":
            continue

        inv = agent.investigate(inc_id, svc, name, desc)
        record = {
            "incident_id": inv.incident_id,
            "service": inv.service,
            "alert": inv.alert,
            "started_at": inv.started_at.isoformat(),
            "conclusion": inv.conclusion,
            "findings": len(inv.findings),
        }
        _investigations.append(record)
        results.append(record)

    return JSONResponse({"processed": len(results), "investigations": results})


@app.post("/webhook/pagerduty")
async def pagerduty_webhook(request: Request) -> JSONResponse:
    """Receive PagerDuty webhook v3 and kick off investigation."""
    body: dict[str, Any] = await request.json()
    results = []

    for event in body.get("events", []):
        if event.get("event_type") not in ("incident.triggered", "incident.acknowledged"):
            continue
        data = event.get("data", {})
        inc_id = data.get("number", "???")
        title = data.get("title", "Unknown alert")
        svc = data.get("service", {}).get("name", "unknown")
        desc = data.get("body", {}).get("details", "")

        inv = agent.investigate(f"PD-{inc_id}", svc, title, desc)
        record = {
            "incident_id": inv.incident_id,
            "service": inv.service,
            "alert": inv.alert,
            "started_at": inv.started_at.isoformat(),
            "conclusion": inv.conclusion,
            "findings": len(inv.findings),
        }
        _investigations.append(record)
        results.append(record)

    return JSONResponse({"processed": len(results), "investigations": results})
