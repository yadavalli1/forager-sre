"""Tests for config loading, saving, and env overrides."""


def test_defaults():
    from forager.config import Config

    cfg = Config()
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.provider == "prometheus"
    assert cfg.prometheus.url == "http://localhost:9090"
    assert cfg.slack.channel == "#incidents"


def test_env_override_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORAGER_MODEL", "gpt-4o")
    import forager.config as cfg_mod

    cfg = cfg_mod.load()
    assert cfg.model == "gpt-4o"


def test_env_override_prometheus(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.internal:9090")
    import forager.config as cfg_mod

    cfg = cfg_mod.load()
    assert cfg.prometheus.url == "http://prom.internal:9090"


def test_env_override_slack(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL", "#sre-alerts")
    import forager.config as cfg_mod

    cfg = cfg_mod.load()
    assert cfg.slack.token == "xoxb-test"
    assert cfg.slack.channel == "#sre-alerts"


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import forager.config as cfg_mod

    cfg = cfg_mod.Config()
    cfg.model = "claude-opus-4-8"
    cfg.prometheus.url = "http://prom:9090"
    cfg.slack.channel = "#my-channel"
    cfg_mod.save(cfg)

    assert (tmp_path / "forager.yaml").exists()
    loaded = cfg_mod.load()
    assert loaded.model == "claude-opus-4-8"
    assert loaded.prometheus.url == "http://prom:9090"
    assert loaded.slack.channel == "#my-channel"


def test_load_partial_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "forager.yaml").write_text("model: gpt-4o\n")
    import forager.config as cfg_mod

    cfg = cfg_mod.load()
    assert cfg.model == "gpt-4o"
    # Unset keys keep defaults
    assert cfg.prometheus.url == "http://localhost:9090"
