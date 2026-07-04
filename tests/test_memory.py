"""Tests for institutional memory: store.search_similar + the search_past_incidents tool."""

from unittest.mock import MagicMock

import pytest

import forager.store as store_mod
from forager.agent import Finding, Investigation


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    store_mod._db = None
    store_mod._db_path = tmp_path / "test.db"
    store_mod.init(store_mod._db_path)
    yield
    if store_mod._db:
        store_mod._db.close()
        store_mod._db = None


def _save(incident_id, service, alert, conclusion):
    inv = Investigation(incident_id=incident_id, service=service, alert=alert)
    inv.conclusion = conclusion
    inv.findings = [Finding("query_metrics", {"query": "up"}, {"status": "ok"})]
    store_mod.save(inv)


def test_search_by_service(fresh_store):
    _save("INC-1", "api", "HighErrorRate", "ROOT CAUSE: pool")
    _save("INC-2", "worker", "QueueBacklog", "ROOT CAUSE: consumer crash")
    results = store_mod.search_similar(service="api")
    assert [r["id"] for r in results] == ["INC-1"]


def test_search_by_alert_substring(fresh_store):
    _save("INC-1", "api", "HighErrorRate", "ROOT CAUSE: pool")
    _save("INC-2", "api", "HighLatency", "ROOT CAUSE: gc")
    results = store_mod.search_similar(alert="Error")
    assert [r["id"] for r in results] == ["INC-1"]


def test_search_matches_either_service_or_alert(fresh_store):
    _save("INC-1", "api", "HighErrorRate", "ROOT CAUSE: pool")
    _save("INC-2", "worker", "HighErrorRate", "ROOT CAUSE: same alert other svc")
    results = store_mod.search_similar(service="api", alert="HighErrorRate")
    assert {r["id"] for r in results} == {"INC-1", "INC-2"}


def test_search_excludes_unconcluded(fresh_store):
    _save("INC-1", "api", "HighErrorRate", "")
    assert store_mod.search_similar(service="api") == []


def test_search_no_filters_returns_recent_concluded(fresh_store):
    _save("INC-1", "api", "A", "ROOT CAUSE: x")
    _save("INC-2", "db", "B", "ROOT CAUSE: y")
    assert len(store_mod.search_similar()) == 2


def test_search_respects_limit(fresh_store):
    for i in range(8):
        _save(f"INC-{i}", "api", "A", "ROOT CAUSE: x")
    assert len(store_mod.search_similar(service="api", limit=3)) == 3


# ── agent tool dispatch ───────────────────────────────────────────────────────


def test_agent_dispatches_search_past_incidents(fresh_store):
    import forager.agent as agent_mod

    _save("INC-OLD", "api", "HighErrorRate", "ROOT CAUSE: pool exhaustion")
    result = agent_mod._execute_tool(
        "search_past_incidents", {"service": "api", "alert": "HighErrorRate"}, MagicMock()
    )
    assert result["status"] == "ok"
    assert result["incidents"][0]["id"] == "INC-OLD"
    assert "pool exhaustion" in result["incidents"][0]["conclusion"]


def test_search_tool_in_schema():
    from forager.adapters import llm

    assert any(t["name"] == "search_past_incidents" for t in llm.TOOLS)
