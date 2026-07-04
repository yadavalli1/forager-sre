"""forager CLI — forager init / investigate / watch."""

from __future__ import annotations

import os
import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import agent, store
from . import config as cfg_mod

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
    console.print("\n[green]✓[/green] Config saved to [bold]forager.yaml[/bold]")
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
        Panel(
            inv.conclusion or "(no conclusion)", title=f"[bold]{inv.incident_id}[/bold]", border_style="green"
        )
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
        raise typer.Exit(1) from None

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
            r["id"],
            r["service"],
            r["alert"],
            started,
            duration,
            str(r["findings_count"]),
        )

    console.print(t)


@app.command()
def postmortem(
    incident_id: str = typer.Argument(..., help="Incident ID of a stored investigation"),
    output: str = typer.Option("", "--output", "-o", help="Write Markdown to a file instead of stdout"),
):
    """Generate a blameless postmortem from a stored investigation."""
    from . import postmortem as pm_mod

    try:
        with console.status("[green]writing postmortem…[/green]", spinner="dots"):
            md = pm_mod.generate(incident_id)
    except KeyError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from None

    if output:
        with open(output, "w") as f:
            f.write(md)
        console.print(f"[green]✓[/green] Postmortem written to [bold]{output}[/bold]")
    else:
        console.print(md)


@app.command()
def remediate(
    incident_id: str = typer.Argument(..., help="Incident this remediation belongs to"),
    action: str = typer.Option(
        ..., "--action", "-a", help="restart_deployment | scale_deployment | rollback_deployment"
    ),
    namespace: str = typer.Option(..., "--namespace", "-n"),
    deployment: str = typer.Option(..., "--deployment", "-d"),
    replicas: int = typer.Option(-1, "--replicas", help="Target replicas (scale_deployment only)"),
    yes: bool = typer.Option(False, "--yes", help="Actually execute (default is dry-run)"),
):
    """Propose and (with --yes) execute an allowlisted remediation with an undo snapshot."""
    from . import remediation

    params: dict = {"namespace": namespace, "deployment": deployment}
    if replicas >= 0:
        params["replicas"] = replicas
    try:
        rid = remediation.propose(incident_id, action, params)
    except ValueError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from None

    result = remediation.execute(rid, dry_run=not yes)
    if result["status"] == "dry_run":
        console.print(f"[yellow]◇ dry-run[/yellow] remediation [bold]#{rid}[/bold]: {action} {params}")
        console.print("  Re-run with [bold]--yes[/bold] to execute. Undo later with:")
        console.print(f"  [bold]forager remediate-undo {rid}[/bold]")
    elif result["status"] == "ok":
        console.print(f"[green]✓[/green] remediation [bold]#{rid}[/bold] executed: {action}")
        console.print(f"  Undo with: [bold]forager remediate-undo {rid}[/bold]")
    else:
        console.print(f"[red]✗[/red] remediation #{rid} failed: {result.get('error')}")
        raise typer.Exit(1)


@app.command("remediate-undo")
def remediate_undo(
    remediation_id: int = typer.Argument(..., help="ID printed when the remediation was executed"),
):
    """Revert an executed remediation using its pre-execution snapshot."""
    from . import remediation

    result = remediation.undo(remediation_id)
    if result["status"] == "ok":
        console.print(f"[green]✓[/green] remediation #{remediation_id} undone")
    else:
        console.print(f"[red]✗[/red] {result.get('error')}")
        raise typer.Exit(1)


def main() -> None:
    app()
