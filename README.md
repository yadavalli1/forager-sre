# ◇ forager-sre

**An autonomous SRE agent that investigates production incidents for you.**

[![CI](https://github.com/yadavalli1/forager-sre/actions/workflows/ci.yml/badge.svg)](https://github.com/yadavalli1/forager-sre/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

When an alert fires, forager-sre runs a real investigation — querying Prometheus
metrics, Kubernetes pod status and logs, deploy history, and recent GitHub
commits — then posts a cited root-cause analysis to Slack. It follows the same
loop a human SRE does: **observe → correlate → hypothesize → verify**.

```
$ forager investigate INC-4827 --service api --alert "High error rate"

◐ Investigating INC-4827 · High error rate

  Evidence gathered
  ┌──────────────────────┬──────────────────────────────────┬────────┐
  │ Tool                 │ Input                            │ Status │
  ├──────────────────────┼──────────────────────────────────┼────────┤
  │ query_metrics        │ {"query": "rate(http_requests…"} │ ok     │
  │ get_pod_status       │ {"namespace": "prod", …}         │ ok     │
  │ get_recent_deploys   │ {"deployment": "api", …}         │ ok     │
  │ get_github_commits   │ {"repo": "acme/api", …}          │ ok     │
  └──────────────────────┴──────────────────────────────────┴────────┘

  ╭─────────────────────────── INC-4827 ───────────────────────────╮
  │ ROOT CAUSE: Connection pool exhausted after deploy a3f9c21     │
  │ reduced pool size from 50 to 5.                                │
  │ EVIDENCE:                                                      │
  │ - pg_pool_available dropped to 0 at 14:02 UTC                  │
  │ - Deploy a3f9c21 landed at 13:58 UTC, changed db.yaml          │
  │ REMEDIATION:                                                   │
  │ - Roll back deploy a3f9c21 or restore pool_size: 50            │
  ╰────────────────────────────────────────────────────────────────╯
```

## How it works

1. **Trigger** — an Alertmanager or PagerDuty webhook (or `forager investigate` / `forager watch`)
2. **Investigate** — an LLM (Claude or OpenAI) drives a tool-use loop against your telemetry:

   | Tool | Backend |
   |---|---|
   | `query_metrics` | Prometheus instant & range queries |
   | `get_pod_status` | Kubernetes pod phases, restarts, OOM kills |
   | `get_recent_deploys` | Kubernetes deployment rollout history |
   | `get_pod_logs` | Kubernetes log tailing |
   | `get_github_commits` | Recent commits & merged PRs for deploy correlation |

3. **Report** — a cited root-cause analysis is posted to Slack, saved to SQLite,
   and served on a built-in dashboard.

Every claim in the conclusion must cite a specific metric value, log line,
commit SHA, or deploy — the system prompt enforces it.

## Quickstart

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...        # or OPENAI_API_KEY

forager init                               # interactive setup → forager.yaml
forager investigate INC-1 -s api -a "High latency"
```

### Run as a webhook server

```bash
forager serve                              # listens on :8080
```

| Endpoint | Purpose |
|---|---|
| `POST /webhook/alertmanager` | Alertmanager webhook receiver |
| `POST /webhook/pagerduty` | PagerDuty V3 webhook receiver |
| `GET /investigations` | JSON list of past investigations |
| `GET /investigations/{id}` | Full record incl. findings |
| `GET /dashboard` | HTML dashboard |
| `GET /health` | Liveness probe |

Point Alertmanager at it:

```yaml
# alertmanager.yml
receivers:
  - name: forager
    webhook_configs:
      - url: http://forager:8080/webhook/alertmanager
```

See [`examples/`](examples/) for full Alertmanager and Docker Compose configs.

### Watch mode (no webhook needed)

```bash
forager watch --poll 30                    # polls Alertmanager directly
```

### Deploy to Cloud Run / Kubernetes

```bash
docker build -f Dockerfile.agent -t forager-sre .
gcloud run deploy forager-sre --image forager-sre --port 8080 \
  --set-env-vars ANTHROPIC_API_KEY=...,PROMETHEUS_URL=...
```

## Configuration

`forager.yaml` (created by `forager init`), with env-var overrides:

| Setting | Env override | Default |
|---|---|---|
| `model` | `FORAGER_MODEL` | `claude-sonnet-4-6` |
| `prometheus.url` | `PROMETHEUS_URL` | `http://localhost:9090` |
| `slack.token` / `slack.channel` | `SLACK_TOKEN` / `SLACK_CHANNEL` | disabled |
| `github_token` | `GITHUB_TOKEN` | unauthenticated |
| dedup window | `FORAGER_DEDUP_MINUTES` | `30` |

Both Anthropic (`claude-*`) and OpenAI (`gpt-*`, `o1`, `o3`) models are
supported; the provider is inferred from the model name.

## Features

- **Autonomous investigation loop** — up to 12 LLM tool-use iterations with a safety cap
- **Deploy correlation** — cross-references Kubernetes rollouts with GitHub commits/PRs
- **Alert deduplication** — same fingerprint within the cooldown window is skipped
- **Persistence** — every investigation saved to SQLite; browse via `forager history` or `/dashboard`
- **Resilient LLM calls** — exponential-backoff retry on 502/503/529/rate-limit errors
- **Slack reports** — Block Kit messages with conclusion and evidence
- **Zero required infra** — SQLite built in; Slack, GitHub, and Kubernetes are all optional

## CLI reference

| Command | Description |
|---|---|
| `forager init` | Interactive config setup |
| `forager investigate <id> -s <svc> -a <alert>` | Investigate one incident |
| `forager watch` | Poll Alertmanager and investigate new alerts |
| `forager serve` | Run the webhook HTTP server |
| `forager history` | Show past investigations |

## Development

```bash
pip install -e ".[dev]"
make test          # pytest (no external services needed — everything is mocked)
make lint          # ruff check + format check
```

The test suite (78 tests) mocks all external services; it runs offline in ~2 s.

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup,
style, and PR guidelines. Please read our
[Code of Conduct](CODE_OF_CONDUCT.md) first.

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Never put API keys
in `forager.yaml` committed to a repo — use environment variables.

## License

[MIT](LICENSE)
