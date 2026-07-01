# Contributing to forager-sre

Thanks for your interest in contributing! This guide covers everything you
need to get a change from idea to merged PR.

## Development setup

```bash
git clone https://github.com/yadavalli1/forager-sre.git
cd forager-sre
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

No external services are needed for development — the entire test suite mocks
Prometheus, Kubernetes, Slack, GitHub, and the LLM APIs.

## Running tests and lint

```bash
make test     # pytest -q
make lint     # ruff check + ruff format --check
make format   # auto-fix formatting
```

CI runs the same commands on Python 3.11 and 3.12; both must pass before a PR
can merge.

## Project layout

```
forager/
├── agent.py          # core investigation loop (LLM tool-use)
├── cli.py            # typer CLI: init / investigate / watch / serve / history
├── server.py         # FastAPI webhook server + dashboard
├── store.py          # SQLite persistence + alert deduplication
├── config.py         # forager.yaml + env-var config
└── adapters/         # one module per external system
    ├── llm.py        #   Anthropic / OpenAI with retry
    ├── prometheus.py #   PromQL instant + range queries
    ├── kubernetes.py #   pods, deploys, logs
    ├── github.py     #   commits + merged PRs
    └── slack.py      #   Block Kit reports
tests/                # 1:1 with the modules above
```

Design rules worth knowing before you change things:

- **Adapters are pure functions** returning `{"status": "ok" | "error", ...}`
  dicts — no exceptions across the adapter boundary, so one failing tool call
  never kills an investigation.
- **`agent.investigate()` does not persist.** Callers (CLI, server) decide
  what to save via `store.save(inv)`. Keep it that way — it keeps the agent
  testable in isolation.
- **New investigation tools** need three changes: a schema entry in
  `llm.TOOLS`, a dispatch branch in `agent._execute_tool()`, and tests.

## Making a change

1. Fork and create a branch: `git checkout -b feat/my-feature`
2. Write tests first (or alongside). Every adapter function and endpoint has
   test coverage; keep it that way. Mock all network calls with
   `unittest.mock` — tests must run offline.
3. Keep commits focused, with descriptive messages
   (`Add Datadog metrics adapter`, not `fix stuff`).
4. Open a PR against `main` and fill in the template.

## What makes a good PR

- **Small and focused** — one feature or fix per PR.
- **Tested** — new code paths have tests; `make test` passes.
- **Documented** — user-facing changes update the README (config table, CLI
  reference, endpoint table as appropriate).
- **No secrets** — never commit API keys, tokens, or real cluster names, even
  in tests or examples.

## Reporting bugs / requesting features

Use the [issue templates](.github/ISSUE_TEMPLATE/). For security issues,
**do not open a public issue** — see [SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind.
