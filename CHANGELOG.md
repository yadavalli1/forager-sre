# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Guarded remediation**: allowlisted actions (`restart_deployment`,
  `scale_deployment`, `rollback_deployment`) via `forager remediate` —
  dry-run by default, human-approved with `--yes`, prior state snapshotted,
  reversible with `forager remediate-undo`. Never exposed as LLM tools.
- **Confidence scores**: conclusions carry `CONFIDENCE: high|medium|low`;
  low-confidence reports are flagged for human review in Slack.
- **Feedback loop**: `POST /investigations/{id}/feedback` (👍/👎 + note);
  downvoted conclusions are excluded from institutional memory.
- **Postmortem generation**: `forager postmortem <id>` and
  `GET /investigations/{id}/postmortem` produce a blameless Markdown review.
- **Alert correlation**: opt-in `FORAGER_GROUP_ALERTS=1` groups a batch's
  alerts by service into one investigation per service.
- **Live Slack progress**: a placeholder is posted when the investigation
  starts and updated in place with the final report.
- **Self-observability**: `GET /metrics` in Prometheus text format
  (investigation count/duration, dedup hits, feedback verdicts).
- **Loki adapter** (`search_logs` tool, `LOKI_URL`): LogQL search across
  services, not just single-pod tails.
- **Datadog adapter**: `provider: datadog` routes `query_metrics` to the
  Datadog v1 metrics API (`DD_API_KEY`/`DD_APP_KEY`/`DD_SITE`).
- **LiteLLM routing**: any non-Claude/OpenAI model name (e.g. `bedrock/...`,
  `ollama/...`) routes through LiteLLM via the new `[litellm]` extra.
- **YAML runbooks** (`runbooks/` or `FORAGER_RUNBOOKS_DIR`): per-alert
  guidance injected into the system prompt, plus `exclude_tools` rules that
  remove tools from the agent for matching alerts.
- **Institutional memory**: new `search_past_incidents` agent tool backed by
  `store.search_similar()` — recurring incidents surface their prior root cause.
- **Investigation wall-clock budget** (`FORAGER_TIMEOUT_S`, default 300 s)
  alongside the existing 12-iteration cap.
- **Audit logging**: every tool call logged on the `forager.audit` logger.
- **Webhook authentication**: optional `FORAGER_WEBHOOK_TOKEN` shared secret
  checked against the `X-Forager-Token` header.
- **Concurrent investigations**: webhook alerts fan out across a thread pool
  (`FORAGER_MAX_CONCURRENCY`, default 4) instead of blocking the event loop;
  duplicate fingerprints within one batch are collapsed.
- Open-source project scaffolding: CI workflow, LICENSE (MIT), CONTRIBUTING,
  CODE_OF_CONDUCT, SECURITY policy, issue/PR templates, examples, Makefile.

## [0.1.0] - 2026-07-01

### Added
- Autonomous investigation loop (`forager investigate`): LLM tool-use over
  Prometheus, Kubernetes, GitHub, and logs with a 12-iteration safety cap.
- CLI: `init`, `investigate`, `watch`, `serve`, `history`.
- FastAPI webhook server with Alertmanager and PagerDuty receivers,
  `/investigations`, `/investigations/{id}`, `/dashboard`, `/health`.
- SQLite persistence for investigations and alert fingerprints.
- Alert deduplication with configurable cooldown (`FORAGER_DEDUP_MINUTES`).
- GitHub adapter: recent commits and merged PRs for deploy correlation.
- LLM adapter with Anthropic + OpenAI support and exponential-backoff retry
  on transient errors (502/503/529/rate limits).
- Slack Block Kit investigation reports.
- Dockerfiles for the agent (Cloud Run) and the static landing page.
- Test suite: 78 tests, fully offline (all external services mocked).
