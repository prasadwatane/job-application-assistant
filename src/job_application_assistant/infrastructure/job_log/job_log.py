"""
Job Application Log — SQLite persistence layer.

Statuses
--------
  pending_review      crew finished, awaiting human approval
  approved            human approved the cover letter
  rejected            human rejected this application
  submitted           human confirmed they sent the application
  interview_invited   company replied with interview invitation
  rejected_by_company company sent a rejection
  awaiting_response   company follow-up, human action needed
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

DB_PATH = "job_applications.db"

VALID_STATUSES = {
    "pending_review", "approved", "rejected", "submitted",
    "interview_invited", "rejected_by_company", "awaiting_response",
}

_CREATE_APPLICATIONS = """
CREATE TABLE IF NOT EXISTS job_applications (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name      TEXT    NOT NULL,
    job_role          TEXT    NOT NULL,
    url               TEXT,
    job_description   TEXT,
    fit_score         REAL,
    best_resume_name  TEXT,
    skills_to_learn   TEXT,
    reasoning         TEXT,
    cover_letter_path TEXT,
    ats_score         REAL,
    ats_report        TEXT,
    status            TEXT    NOT NULL DEFAULT 'pending_review',
    reviewer_notes    TEXT,
    created_at        TEXT    NOT NULL,
    reviewed_at       TEXT,
    submitted_at      TEXT,
    source            TEXT    DEFAULT 'text'
);
"""

_CREATE_EMAIL_EVENTS = """
CREATE TABLE IF NOT EXISTS email_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id   INTEGER REFERENCES job_applications(id),
    message_id       TEXT UNIQUE,
    sender           TEXT,
    subject          TEXT,
    received_at      TEXT,
    snippet          TEXT,
    classification   TEXT,
    confidence_note  TEXT,
    processed_at     TEXT
);
"""

_CREATE_COMMANDS = """
CREATE TABLE IF NOT EXISTS user_commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command     TEXT    NOT NULL,
    type        TEXT    NOT NULL DEFAULT 'content',
    scope       TEXT    NOT NULL DEFAULT 'global',
    company     TEXT,
    job_role    TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    note        TEXT
);
"""


def init_db() -> None:
    with _conn() as conn:
        conn.execute(_CREATE_APPLICATIONS)
        conn.execute(_CREATE_EMAIL_EVENTS)
        conn.execute(_CREATE_COMMANDS)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@dataclass
class ApplicationRecord:
    company_name: str
    job_role: str
    url: Optional[str] = None
    job_description: Optional[str] = None
    fit_score: Optional[float] = None
    best_resume_name: Optional[str] = None
    skills_to_learn: list = field(default_factory=list)
    reasoning: Optional[str] = None
    cover_letter_path: Optional[str] = None
    ats_score: Optional[float] = None
    ats_report: Optional[dict] = None
    status: str = "pending_review"
    source: str = "text"


def insert_application(record: ApplicationRecord) -> int:
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO job_applications
               (company_name,job_role,url,job_description,fit_score,
                best_resume_name,skills_to_learn,reasoning,
                cover_letter_path,ats_score,ats_report,status,source,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record.company_name, record.job_role, record.url,
                record.job_description, record.fit_score,
                record.best_resume_name,
                json.dumps(record.skills_to_learn or []),
                record.reasoning, record.cover_letter_path,
                record.ats_score,
                json.dumps(record.ats_report) if record.ats_report else None,
                record.status, record.source,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def approve_application(log_id: int, reviewer_notes: Optional[str] = None) -> bool:
    return _set_status(log_id, "approved", reviewer_notes, set_reviewed=True)


def reject_application(log_id: int, reviewer_notes: Optional[str] = None) -> bool:
    return _set_status(log_id, "rejected", reviewer_notes, set_reviewed=True)


def mark_submitted(log_id: int) -> bool:
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE job_applications SET status='submitted',submitted_at=? WHERE id=? AND status='approved'",
            (now, log_id),
        )
        return cur.rowcount > 0


def update_cover_letter_path(log_id: int, path: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE job_applications SET cover_letter_path=? WHERE id=?", (path, log_id)
        )
        return cur.rowcount > 0


def _set_status(log_id, status, notes, set_reviewed=False):
    now = datetime.utcnow().isoformat() if set_reviewed else None
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE job_applications SET status=?,reviewer_notes=?,reviewed_at=? WHERE id=?",
            (status, notes, now, log_id),
        )
        return cur.rowcount > 0


def _deserialise(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in ("skills_to_learn", "ats_report"):
        if d.get(f):
            try:
                d[f] = json.loads(d[f])
            except (json.JSONDecodeError, TypeError):
                d[f] = [] if f == "skills_to_learn" else {}
    return d


def get_application(log_id: int) -> Optional[dict]:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM job_applications WHERE id=?", (log_id,)).fetchone()
        return _deserialise(row) if row else None


def list_pending() -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM job_applications WHERE status='pending_review' ORDER BY created_at DESC"
        ).fetchall()
        return [_deserialise(r) for r in rows]


def list_all(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    init_db()
    with _conn() as conn:
        if status:
            if status not in VALID_STATUSES:
                raise ValueError(f"Unknown status '{status}'. Valid: {VALID_STATUSES}")
            rows = conn.execute(
                "SELECT * FROM job_applications WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM job_applications ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_deserialise(r) for r in rows]


def summary_stats() -> dict:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM job_applications GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["count"] for r in rows}
        stats["total"] = sum(stats.values())
        avg = conn.execute(
            "SELECT AVG(fit_score) as f, AVG(ats_score) as a FROM job_applications"
        ).fetchone()
        stats["avg_fit_score"] = round(avg["f"] or 0, 1)
        stats["avg_ats_score"] = round(avg["a"] or 0, 1)
        return stats
