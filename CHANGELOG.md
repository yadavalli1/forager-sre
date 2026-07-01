# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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
