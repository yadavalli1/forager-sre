"""SQLite-backed investigation store with alert deduplication."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

DB_FILE = Path("forager.db")

# Investigations run concurrently in server worker threads but share one
# connection; SQLite needs the calls serialized.
_lock = threading.Lock()

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
CREATE TABLE IF NOT EXISTS remediations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id  TEXT NOT NULL,
    action       TEXT NOT NULL,
    params_json  TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'proposed',
    snapshot_json TEXT DEFAULT '{}',
    result_json  TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL,
    executed_at  TEXT
);
CREATE TABLE IF NOT EXISTS counters (
    name   TEXT PRIMARY KEY,
    value  INTEGER NOT NULL DEFAULT 0
);
"""

# Columns added after 0.1.0; applied idempotently on connect.
_MIGRATIONS = (
    "ALTER TABLE investigations ADD COLUMN confidence TEXT DEFAULT ''",
    "ALTER TABLE investigations ADD COLUMN feedback_verdict TEXT DEFAULT ''",
    "ALTER TABLE investigations ADD COLUMN feedback_note TEXT DEFAULT ''",
)

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
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
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
    with _lock:
        db = _get()
        db.execute(
            """INSERT OR REPLACE INTO investigations
               (id, service, alert, description, started_at, finished_at, duration_s,
                conclusion, findings_count, findings_json, slack_ts, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                getattr(inv, "confidence", ""),
            ),
        )
        db.commit()


def get(incident_id: str) -> dict | None:
    with _lock:
        row = _get().execute("SELECT * FROM investigations WHERE id = ?", (incident_id,)).fetchone()
    return dict(row) if row else None


def list_recent(limit: int = 50) -> list[dict]:
    with _lock:
        rows = (
            _get()
            .execute("SELECT * FROM investigations ORDER BY started_at DESC LIMIT ?", (limit,))
            .fetchall()
        )
    return [dict(r) for r in rows]


def search_similar(service: str = "", alert: str = "", limit: int = 5) -> list[dict]:
    """Find past concluded investigations for the same service or a similar alert name.

    Downvoted conclusions are excluded — the feedback loop keeps bad memories
    out of future investigations.
    """
    query = (
        "SELECT id, service, alert, started_at, conclusion, confidence FROM investigations "
        "WHERE conclusion != '' AND feedback_verdict != 'down'"
    )
    clauses: list[str] = []
    params: list = []
    if service:
        clauses.append("service = ?")
        params.append(service)
    if alert:
        clauses.append("alert LIKE ?")
        params.append(f"%{alert}%")
    if clauses:
        query += " AND (" + " OR ".join(clauses) + ")"
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with _lock:
        rows = _get().execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── deduplication ─────────────────────────────────────────────────────────────


def is_duplicate(fingerprint: str, cooldown_minutes: int = 30) -> bool:
    """Return True if this fingerprint was investigated within the cooldown window."""
    with _lock:
        row = _get().execute("SELECT at FROM fingerprints WHERE fp = ?", (fingerprint,)).fetchone()
    if not row:
        return False
    at = datetime.fromisoformat(row["at"])
    age_s = (datetime.now(UTC) - at).total_seconds()
    return age_s < cooldown_minutes * 60


def mark_fingerprint(fingerprint: str) -> None:
    with _lock:
        db = _get()
        db.execute(
            "INSERT OR REPLACE INTO fingerprints (fp, at) VALUES (?, ?)",
            (fingerprint, datetime.now(UTC).isoformat()),
        )
        db.commit()


# ── feedback ──────────────────────────────────────────────────────────────────


def set_feedback(incident_id: str, verdict: str, note: str = "") -> bool:
    """Record 👍/👎 feedback on an investigation. Returns False if the ID is unknown."""
    if verdict not in ("up", "down"):
        raise ValueError("verdict must be 'up' or 'down'")
    with _lock:
        db = _get()
        cur = db.execute(
            "UPDATE investigations SET feedback_verdict = ?, feedback_note = ? WHERE id = ?",
            (verdict, note, incident_id),
        )
        db.commit()
    return cur.rowcount > 0


# ── counters (self-observability) ─────────────────────────────────────────────


def incr_counter(name: str, by: int = 1) -> None:
    with _lock:
        db = _get()
        db.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = value + ?",
            (name, by, by),
        )
        db.commit()


def get_counters() -> dict[str, int]:
    with _lock:
        rows = _get().execute("SELECT name, value FROM counters").fetchall()
    return {r["name"]: r["value"] for r in rows}


def investigation_stats() -> dict:
    """Aggregates for the /metrics endpoint."""
    with _lock:
        row = (
            _get()
            .execute("SELECT COUNT(*) AS n, COALESCE(SUM(duration_s), 0) AS total_s FROM investigations")
            .fetchone()
        )
        fb = (
            _get()
            .execute(
                "SELECT feedback_verdict AS v, COUNT(*) AS n FROM investigations "
                "WHERE feedback_verdict != '' GROUP BY feedback_verdict"
            )
            .fetchall()
        )
    return {
        "count": row["n"],
        "duration_sum_s": row["total_s"],
        "feedback": {r["v"]: r["n"] for r in fb},
    }


# ── remediations ──────────────────────────────────────────────────────────────


def add_remediation(incident_id: str, action: str, params: dict) -> int:
    with _lock:
        db = _get()
        cur = db.execute(
            "INSERT INTO remediations (incident_id, action, params_json, status, created_at) "
            "VALUES (?, ?, ?, 'proposed', ?)",
            (incident_id, action, json.dumps(params), datetime.now(UTC).isoformat()),
        )
        db.commit()
    return cur.lastrowid


def get_remediation(remediation_id: int) -> dict | None:
    with _lock:
        row = _get().execute("SELECT * FROM remediations WHERE id = ?", (remediation_id,)).fetchone()
    return dict(row) if row else None


def list_remediations(limit: int = 50) -> list[dict]:
    with _lock:
        rows = _get().execute("SELECT * FROM remediations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def update_remediation(
    remediation_id: int, status: str, snapshot: dict | None = None, result: dict | None = None
) -> None:
    with _lock:
        db = _get()
        db.execute(
            "UPDATE remediations SET status = ?, "
            "snapshot_json = COALESCE(?, snapshot_json), "
            "result_json = COALESCE(?, result_json), "
            "executed_at = ? WHERE id = ?",
            (
                status,
                json.dumps(snapshot) if snapshot is not None else None,
                json.dumps(result) if result is not None else None,
                datetime.now(UTC).isoformat(),
                remediation_id,
            ),
        )
        db.commit()
