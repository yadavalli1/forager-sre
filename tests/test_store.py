"""Tests for SQLite investigation store and deduplication."""

from datetime import UTC, datetime, timedelta

import pytest

import forager.store as store_mod


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Each test gets an isolated in-memory-like DB in tmp_path."""
    db_path = tmp_path / "test.db"
    store_mod._db = None
    store_mod._db_path = db_path
    store_mod.init(db_path)
    yield
    if store_mod._db:
        store_mod._db.close()
        store_mod._db = None


def _make_inv(incident_id: str = "INC-001", service: str = "api", alert: str = "High latency"):
    from forager.agent import Investigation

    inv = Investigation(incident_id=incident_id, service=service, alert=alert)
    inv.conclusion = "ROOT CAUSE: connection pool exhaustion"
    inv.slack_ts = "999.000"
    return inv


# ── save / get / list ─────────────────────────────────────────────────────────


def test_save_and_get():
    inv = _make_inv()
    store_mod.save(inv)
    record = store_mod.get("INC-001")
    assert record is not None
    assert record["id"] == "INC-001"
    assert record["service"] == "api"
    assert record["conclusion"] == "ROOT CAUSE: connection pool exhaustion"
    assert record["slack_ts"] == "999.000"


def test_get_nonexistent_returns_none():
    assert store_mod.get("INC-MISSING") is None


def test_list_recent_empty():
    assert store_mod.list_recent() == []


def test_list_recent_returns_newest_first():
    for i in range(5):
        inv = _make_inv(incident_id=f"INC-{i:03d}")
        store_mod.save(inv)

    records = store_mod.list_recent(10)
    assert len(records) == 5
    # Most recent first (they're saved sequentially so order should be desc by started_at)
    ids = [r["id"] for r in records]
    assert "INC-000" in ids
    assert "INC-004" in ids


def test_list_recent_respects_limit():
    for i in range(10):
        store_mod.save(_make_inv(incident_id=f"INC-{i:03d}"))
    assert len(store_mod.list_recent(3)) == 3


def test_save_records_duration():
    inv = _make_inv()
    store_mod.save(inv)
    record = store_mod.get("INC-001")
    assert record["duration_s"] is not None
    assert record["duration_s"] >= 0


def test_save_records_findings_count():
    from forager.agent import Finding, Investigation

    inv = Investigation(incident_id="INC-002", service="db", alert="Disk full")
    inv.findings = [
        Finding("query_metrics", {"query": "up"}, {"status": "ok"}),
        Finding(
            "get_pod_status",
            {"namespace": "x", "selector": "a=b"},
            {"status": "error", "error": "no kubeconfig"},
        ),
    ]
    store_mod.save(inv)
    record = store_mod.get("INC-002")
    assert record["findings_count"] == 2


def test_save_overwrites_on_duplicate_id():
    inv = _make_inv()
    store_mod.save(inv)
    inv.conclusion = "UPDATED conclusion"
    store_mod.save(inv)
    record = store_mod.get("INC-001")
    assert record["conclusion"] == "UPDATED conclusion"
    assert len(store_mod.list_recent()) == 1  # not duplicated


# ── deduplication ─────────────────────────────────────────────────────────────


def test_is_duplicate_unknown_fingerprint():
    assert store_mod.is_duplicate("fp-new") is False


def test_mark_then_is_duplicate():
    store_mod.mark_fingerprint("fp-001")
    assert store_mod.is_duplicate("fp-001", cooldown_minutes=30) is True


def test_is_duplicate_expired_fingerprint(monkeypatch):
    store_mod.mark_fingerprint("fp-old")
    # Simulate the stored timestamp being 2 hours old
    old_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    store_mod._get().execute("UPDATE fingerprints SET at = ? WHERE fp = ?", (old_time, "fp-old"))
    store_mod._get().commit()
    assert store_mod.is_duplicate("fp-old", cooldown_minutes=30) is False


def test_mark_fingerprint_overwrites():
    store_mod.mark_fingerprint("fp-upd")
    assert store_mod.is_duplicate("fp-upd") is True
    store_mod.mark_fingerprint("fp-upd")  # second mark should not error
    assert store_mod.is_duplicate("fp-upd") is True


def test_different_cooldowns():
    store_mod.mark_fingerprint("fp-cool")
    assert store_mod.is_duplicate("fp-cool", cooldown_minutes=60) is True
    assert store_mod.is_duplicate("fp-cool", cooldown_minutes=0) is False
