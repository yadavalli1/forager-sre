# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.x (latest on `main`) | ✅ |

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Instead, use
[GitHub private vulnerability reporting](https://github.com/yadavalli1/forager-sre/security/advisories/new)
on this repository. You should receive an acknowledgement within a few days.

## Scope and threat model

forager-sre holds credentials with real blast radius. Things worth knowing:

- **API keys are env-vars only.** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `SLACK_TOKEN`, and `GITHUB_TOKEN` are read from the environment. Never
  commit them to `forager.yaml`.
- **Webhook endpoints are unauthenticated by default.** If you expose
  `forager serve` publicly, put it behind an authenticating proxy (Cloud Run
  IAM, an API gateway, or network policy) — anyone who can POST to
  `/webhook/alertmanager` can trigger LLM calls and telemetry queries on your
  behalf.
- **Telemetry flows to the LLM provider.** Metric values, pod logs, and commit
  messages gathered during an investigation are sent to Anthropic/OpenAI as
  model context. Don't point the agent at systems whose logs may contain
  secrets unless that is acceptable to you.
- **Kubernetes access is read-only by design** (pod status, rollout history,
  logs). Grant the agent a correspondingly minimal RBAC role.
