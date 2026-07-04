from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PrometheusConfig:
    url: str = "http://localhost:9090"


@dataclass
class KubernetesConfig:
    context: str | None = None
    namespace: str = "default"


@dataclass
class SlackConfig:
    token: str = ""
    channel: str = "#incidents"


@dataclass
class LokiConfig:
    url: str = ""  # e.g. http://loki:3100; empty = disabled


@dataclass
class Config:
    model: str = "claude-sonnet-4-6"
    provider: str = "prometheus"
    repo: str = "."
    github_token: str = ""
    runbooks_dir: str = "runbooks"
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    kubernetes: KubernetesConfig = field(default_factory=KubernetesConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    loki: LokiConfig = field(default_factory=LokiConfig)


CONFIG_FILE = Path("forager.yaml")


def load() -> Config:
    cfg = Config()
    if CONFIG_FILE.exists():
        raw = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        for key in ("model", "provider", "repo", "runbooks_dir"):
            if key in raw:
                setattr(cfg, key, raw[key])
        if "prometheus" in raw:
            cfg.prometheus = PrometheusConfig(**raw["prometheus"])
        if "kubernetes" in raw:
            cfg.kubernetes = KubernetesConfig(
                **{k: v for k, v in raw["kubernetes"].items() if k in KubernetesConfig.__dataclass_fields__}
            )
        if "slack" in raw:
            cfg.slack = SlackConfig(
                **{k: v for k, v in raw["slack"].items() if k in SlackConfig.__dataclass_fields__}
            )
        if "loki" in raw:
            cfg.loki = LokiConfig(
                **{k: v for k, v in raw["loki"].items() if k in LokiConfig.__dataclass_fields__}
            )

    # Environment overrides
    if v := os.environ.get("FORAGER_MODEL"):
        cfg.model = v
    if v := os.environ.get("PROMETHEUS_URL"):
        cfg.prometheus.url = v
    if v := os.environ.get("SLACK_TOKEN"):
        cfg.slack.token = v
    if v := os.environ.get("SLACK_CHANNEL"):
        cfg.slack.channel = v
    if v := os.environ.get("GITHUB_TOKEN"):
        cfg.github_token = v
    if v := os.environ.get("FORAGER_RUNBOOKS_DIR"):
        cfg.runbooks_dir = v
    if v := os.environ.get("LOKI_URL"):
        cfg.loki.url = v
    return cfg


def save(cfg: Config) -> None:
    data = {
        "model": cfg.model,
        "provider": cfg.provider,
        "repo": cfg.repo,
        "runbooks_dir": cfg.runbooks_dir,
        "prometheus": {"url": cfg.prometheus.url},
        "kubernetes": {
            "context": cfg.kubernetes.context,
            "namespace": cfg.kubernetes.namespace,
        },
        "slack": {
            "channel": cfg.slack.channel,
        },
    }
    CONFIG_FILE.write_text(yaml.dump(data, default_flow_style=False))
