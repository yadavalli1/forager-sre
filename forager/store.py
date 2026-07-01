"""SQLite-backed investigation store with alert deduplication."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DB_FILE = Path("forager.db")

_DDL = """
CREATE TABLE IF NOT EXISTS investigations (
    id              TEXT PRIMARY KEY,
    service         TEXT NOT NULL,
    alert           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    duration_s      REAL,
    conclusion      TEXT DEFAULT '',
    findings_count  INTEGER DEFAULT 0,
    findings_json   TEXT DEFAULT '[]',
    slack_ts        TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fingerprints (
    fp  TEXT PRIMARY KEY,
    at  TEXT NOT NULL
);
"""

_db: sqlite3.Connection | None = None
_db_path: Path = DB_FILE


def init(db_path: Path = DB_FILE) -> None:
    global _db, _db_path
    _db_path = db_path
    _db = _connect(db_path)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()
    return conn


def _get() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = _connect(_db_path)
    return _db


# ── investigations ────────────────────────────────────────────────────────────


def save(inv: object) -> None:
    """Persist a forager.agent.Investigation."""
    finished = datetime.now(UTC)
    duration = (finished - inv.started_at).total_seconds()  # type: ignore[attr-defined]
    findings = [
        {"tool": f.tool, "input": f.input, "status": f.result.get("status")}
        for f in inv.findings  # type: ignore[attr-defined]
    ]
    _get().execute(
        """INSERT OR REPLACE INTO investigations
           (id, service, alert, description, started_at, finished_at, duration_s,
            conclusion, findings_count, findings_json, slack_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            inv.incident_id,
            inv.service,
            inv.alert,  # type: ignore[attr-defined]
            getattr(inv, "description", ""),
            inv.started_at.isoformat(),  # type: ignore[attr-defined]
            finished.isoformat(),
            round(duration, 2),
            inv.conclusion,  # type: ignore[attr-defined]
            len(inv.findings),  # type: ignore[attr-defined]
            json.dumps(findings),
            inv.slack_ts,  # type: ignore[attr-defined]
        ),
    )
    _get().commit()


def get(incident_id: str) -> dict | None:
    row = _get().execute("SELECT * FROM investigations WHERE id = ?", (incident_id,)).fetchone()
    return dict(row) if row else None


def list_recent(limit: int = 50) -> list[dict]:
    rows = (
        _get().execute("SELECT * FROM investigations ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    )
    return [dict(r) for r in rows]


# ── deduplication ─────────────────────────────────────────────────────────────


def is_duplicate(fingerprint: str, cooldown_minutes: int = 30) -> bool:
    """Return True if this fingerprint was investigated within the cooldown window."""
    row = _get().execute("SELECT at FROM fingerprints WHERE fp = ?", (fingerprint,)).fetchone()
    if not row:
        return False
    at = datetime.fromisoformat(row["at"])
    age_s = (datetime.now(UTC) - at).total_seconds()
    return age_s < cooldown_minutes * 60


def mark_fingerprint(fingerprint: str) -> None:
    db = _get()
    db.execute(
        "INSERT OR REPLACE INTO fingerprints (fp, at) VALUES (?, ?)",
        (fingerprint, datetime.now(UTC).isoformat()),
    )
    db.commit()
