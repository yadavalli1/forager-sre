"""forager CLI — forager init / investigate / watch."""
from __future__ import annotations
import os
import sys
import time
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

from . import config as cfg_mod
from . import agent, store

app = typer.Typer(help="forager-sre: autonomous SRE investigation agent.", no_args_is_help=True)
console = Console()


# ── init ──────────────────────────────────────────────────────────────────────

@app.command()
def init(
    provider: str = typer.Option("prometheus", help="Metrics provider: prometheus | datadog"),
    repo: str = typer.Option(".", help="Path to service repo for deploy context"),
    model: str = typer.Option("", help="LLM model name (overrides FORAGER_MODEL env var)"),
):
    """Configure forager-sre for your stack."""
    cfg = cfg_mod.Config(provider=provider, repo=repo)
    if model:
        cfg.model = model
    elif env_model := os.environ.get("FORAGER_MODEL"):
        cfg.model = env_model

    # Interactive prompts for connection details
    console.print("\n[bold green]forager-sre init[/bold green]\n")

    prom_url = typer.prompt(
        "  Prometheus URL",
        default=cfg.prometheus.url,
    )
    cfg.prometheus.url = prom_url

    slack_token = typer.prompt(
        "  Slack bot token (leave blank to skip)",
        default="",
        hide_input=True,
    )
    if slack_token:
        cfg.slack.token = slack_token
        cfg.slack.channel = typer.prompt("  Slack channel", default="#incidents")

    cfg_mod.save(cfg)
    console.print(f"\n[green]✓[/green] Config saved to [bold]forager.yaml[/bold]")
    console.print(f"  model  = [cyan]{cfg.model}[/cyan]")
    console.print(f"  prom   = [cyan]{cfg.prometheus.url}[/cyan]")
    if cfg.slack.token:
        console.print(f"  slack  = [cyan]{cfg.slack.channel}[/cyan]")
    console.print("\nRun [bold]forager watch[/bold] to start monitoring.\n")


# ── investigate ───────────────────────────────────────────────────────────────

@app.command()
def investigate(
    incident_id: str = typer.Argument(..., help="Incident ID, e.g. INC-4827"),
    service: str = typer.Option(..., "--service", "-s", help="Affected service name"),
    alert: str = typer.Option(..., "--alert", "-a", help="Alert name / title"),
    description: str = typer.Option("", "--desc", "-d", help="Additional context"),
):
    """Investigate a specific incident and print root-cause analysis."""
    cfg = cfg_mod.load()
    console.print(f"\n[bold green]◐[/bold green] Investigating [bold]{incident_id}[/bold] · {alert}")
    console.print(f"  model = [cyan]{cfg.model}[/cyan]  prom = [cyan]{cfg.prometheus.url}[/cyan]\n")

    with console.status("[green]reasoning…[/green]", spinner="dots"):
        inv = agent.investigate(incident_id, service, alert, description)

    store.save(inv)
    _print_investigation(inv)


def _print_investigation(inv: agent.Investigation) -> None:
    # Tool call table
    if inv.findings:
        t = Table(title="Evidence gathered", show_header=True, header_style="bold cyan")
        t.add_column("Tool", style="green")
        t.add_column("Input", max_width=40)
        t.add_column("Status", max_width=8)
        for f in inv.findings:
            import json
            t.add_row(
                f.tool,
                json.dumps(f.input)[:60],
                f.result.get("status", "?"),
            )
        console.print(t)

    console.print(
        Panel(inv.conclusion or "(no conclusion)", title=f"[bold]{inv.incident_id}[/bold]", border_style="green")
    )
    if inv.slack_ts:
        console.print(f"[dim]Posted to Slack (ts={inv.slack_ts})[/dim]")


# ── watch ─────────────────────────────────────────────────────────────────────

@app.command()
def watch(
    poll: int = typer.Option(30, help="Alertmanager poll interval in seconds"),
    alertmanager: str = typer.Option("", help="Alertmanager URL (defaults to Prometheus host :9093)"),
):
    """Watch Alertmanager for firing alerts and investigate each one automatically."""
    import httpx

    cfg = cfg_mod.load()
    am_url = alertmanager or cfg.prometheus.url.replace(":9090", ":9093")
    seen: set[str] = set()

    console.print(f"\n[bold green]◇[/bold green] forager-sre watching · model=[cyan]{cfg.model}[/cyan]")
    console.print(f"  alertmanager = [cyan]{am_url}[/cyan]")
    console.print("  [dim]Ctrl-C to stop[/dim]\n")

    while True:
        try:
            r = httpx.get(f"{am_url}/api/v2/alerts", params={"active": "true"}, timeout=10)
            r.raise_for_status()
            alerts = r.json()
        except httpx.ConnectError:
            console.print(f"[yellow]⚠[/yellow]  Cannot reach Alertmanager at {am_url} — retrying in {poll}s")
            time.sleep(poll)
            continue
        except Exception as exc:
            console.print(f"[red]✗[/red]  {exc}")
            time.sleep(poll)
            continue

        for alert_obj in alerts:
            labels = alert_obj.get("labels", {})
            fingerprint = alert_obj.get("fingerprint", "")
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            name = labels.get("alertname", "UnknownAlert")
            svc = labels.get("service", labels.get("job", "unknown"))
            inc_id = f"INC-{fingerprint[:6].upper()}"
            annotations = alert_obj.get("annotations", {})
            desc = annotations.get("description", annotations.get("summary", ""))

            console.print(f"[green]![/green] New alert: [bold]{name}[/bold] ({svc}) → {inc_id}")

            with console.status(f"[green]investigating {inc_id}…[/green]", spinner="dots"):
                inv = agent.investigate(inc_id, svc, name, desc)

            store.save(inv)
            _print_investigation(inv)

        time.sleep(poll)


# ── server (Cloud Run) ────────────────────────────────────────────────────────

@app.command()
def serve(
    port: int = typer.Option(int(os.environ.get("PORT", "8080")), help="HTTP port"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
):
    """Run the webhook HTTP server (for Cloud Run / PagerDuty / Alertmanager webhooks)."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed. Run: pip install uvicorn[standard][/red]")
        raise typer.Exit(1)

    console.print(f"[green]◇[/green] forager-sre server on :{port}")
    uvicorn.run("forager.server:app", host=host, port=port, log_level="info")


@app.command()
def history(
    limit: int = typer.Option(20, help="Number of past investigations to show"),
):
    """Show past investigations from the local database."""
    records = store.list_recent(limit)
    if not records:
        console.print("[dim]No investigations found. Run [bold]forager investigate[/bold] first.[/dim]")
        return

    t = Table(title="Investigation history", show_header=True, header_style="bold cyan")
    t.add_column("ID", style="green")
    t.add_column("Service")
    t.add_column("Alert", max_width=30)
    t.add_column("Started", style="dim")
    t.add_column("Duration", justify="right")
    t.add_column("Findings", justify="right")

    for r in records:
        duration = f"{r['duration_s']:.1f}s" if r.get("duration_s") else "—"
        started = (r.get("started_at") or "")[:16].replace("T", " ")
        t.add_row(
            r["id"], r["service"], r["alert"], started,
            duration, str(r["findings_count"]),
        )

    console.print(t)


def main() -> None:
    app()
