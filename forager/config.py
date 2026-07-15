from __future__ import annotations
import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PrometheusConfig:
    url: str = "http://localhost:9090"


@dataclass
class KubernetesConfig:
    context: Optional[str] = None
    namespace: str = "default"


@dataclass
class SlackConfig:
    token: str = ""
    channel: str = "#incidents"


@dataclass
class LokiConfig:
    url: str = "http://localhost:3100"


@dataclass
class JaegerConfig:
    url: str = "http://localhost:16686"


@dataclass
class DatadogConfig:
    api_key: str = ""
    app_key: str = ""
    site: str = "datadoghq.com"


@dataclass
class CloudWatchConfig:
    region: str = "us-east-1"
    access_key_id: str = ""
    secret_access_key: str = ""


@dataclass
class SentryConfig:
    token: str = ""
    organization: str = ""
    project: str = ""


@dataclass
class ArgoCDConfig:
    url: str = "http://localhost:8080"
    token: str = ""


@dataclass
class PagerDutyConfig:
    token: str = ""


@dataclass
class JiraConfig:
    url: str = ""
    email: str = ""
    token: str = ""


@dataclass
class Config:
    model: str = "claude-sonnet-4-6"
    provider: str = "prometheus"
    repo: str = "."
    github_token: str = ""
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    kubernetes: KubernetesConfig = field(default_factory=KubernetesConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    loki: LokiConfig = field(default_factory=LokiConfig)
    jaeger: JaegerConfig = field(default_factory=JaegerConfig)
    datadog: DatadogConfig = field(default_factory=DatadogConfig)
    cloudwatch: CloudWatchConfig = field(default_factory=CloudWatchConfig)
    sentry: SentryConfig = field(default_factory=SentryConfig)
    argocd: ArgoCDConfig = field(default_factory=ArgoCDConfig)
    pagerduty: PagerDutyConfig = field(default_factory=PagerDutyConfig)
    jira: JiraConfig = field(default_factory=JiraConfig)


CONFIG_FILE = Path("forager.yaml")


_NESTED_SECTIONS = (
    "prometheus", "kubernetes", "slack", "loki", "jaeger", "datadog",
    "cloudwatch", "sentry", "argocd", "pagerduty", "jira",
)


def _coerce_section(cls, raw: dict):
    fields = getattr(cls, "__dataclass_fields__", {})
    return cls(**{k: v for k, v in raw.items() if k in fields})


def load() -> Config:
    cfg = Config()
    if CONFIG_FILE.exists():
        raw = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        for key in ("model", "provider", "repo"):
            if key in raw:
                setattr(cfg, key, raw[key])
        for section in _NESTED_SECTIONS:
            if section in raw:
                cls = type(getattr(cfg, section))
                setattr(cfg, section, _coerce_section(cls, raw[section]))

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
    if v := os.environ.get("LOKI_URL"):
        cfg.loki.url = v
    if v := os.environ.get("JAEGER_URL"):
        cfg.jaeger.url = v
    if v := os.environ.get("DATADOG_API_KEY"):
        cfg.datadog.api_key = v
    if v := os.environ.get("DATADOG_APP_KEY"):
        cfg.datadog.app_key = v
    if v := os.environ.get("DATADOG_SITE"):
        cfg.datadog.site = v
    if v := os.environ.get("AWS_REGION"):
        cfg.cloudwatch.region = v
    if v := os.environ.get("AWS_ACCESS_KEY_ID"):
        cfg.cloudwatch.access_key_id = v
    if v := os.environ.get("AWS_SECRET_ACCESS_KEY"):
        cfg.cloudwatch.secret_access_key = v
    if v := os.environ.get("SENTRY_TOKEN"):
        cfg.sentry.token = v
    if v := os.environ.get("SENTRY_ORG"):
        cfg.sentry.organization = v
    if v := os.environ.get("SENTRY_PROJECT"):
        cfg.sentry.project = v
    if v := os.environ.get("ARGOCD_URL"):
        cfg.argocd.url = v
    if v := os.environ.get("ARGOCD_TOKEN"):
        cfg.argocd.token = v
    if v := os.environ.get("PAGERDUTY_TOKEN"):
        cfg.pagerduty.token = v
    if v := os.environ.get("JIRA_URL"):
        cfg.jira.url = v
    if v := os.environ.get("JIRA_EMAIL"):
        cfg.jira.email = v
    if v := os.environ.get("JIRA_TOKEN"):
        cfg.jira.token = v
    return cfg


def save(cfg: Config) -> None:
    data = {
        "model": cfg.model,
        "provider": cfg.provider,
        "repo": cfg.repo,
        "prometheus": {"url": cfg.prometheus.url},
        "kubernetes": {
            "context": cfg.kubernetes.context,
            "namespace": cfg.kubernetes.namespace,
        },
        "slack": {
            "channel": cfg.slack.channel,
        },
        "loki": {"url": cfg.loki.url},
        "jaeger": {"url": cfg.jaeger.url},
        "datadog": {"site": cfg.datadog.site},
        "cloudwatch": {"region": cfg.cloudwatch.region},
        "sentry": {
            "organization": cfg.sentry.organization,
            "project": cfg.sentry.project,
        },
        "argocd": {"url": cfg.argocd.url},
        "pagerduty": {},
        "jira": {"url": cfg.jira.url},
    }
    CONFIG_FILE.write_text(yaml.dump(data, default_flow_style=False))