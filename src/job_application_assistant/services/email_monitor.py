"""
Email Monitor Service
======================
Background thread that polls your IMAP inbox every N minutes,
matches incoming emails to submitted applications, and uses
Claude to classify them as interview invite / rejection / follow-up / etc.

On detection:
  → Updates job log status in SQLite
  → Saves event to email_events table
  → Sends push (Pushover) + email (SMTP) notification

Config (.env)
-------------
  IMAP_HOST                   e.g. imap.gmail.com
  IMAP_PORT                   993 (SSL default)
  EMAIL_ADDRESS               your email
  EMAIL_PASSWORD              Gmail App Password or provider password
  EMAIL_POLL_INTERVAL_SECONDS default 300 (5 min)
  ANTHROPIC_API_KEY           used for classification
"""

import email
import imaplib
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from email.header import decode_header
from typing import Optional
from urllib.parse import urlparse

import anthropic

from ..infrastructure.job_log import job_log as log_store
from .email_sender import notify_email_received
from ..tools.push_tool import PushNotificationTool

# ── Config ─────────────────────────────────────────────────────────────────

IMAP_HOST      = os.getenv("IMAP_HOST",     "imap.gmail.com")
IMAP_PORT      = int(os.getenv("IMAP_PORT", "993"))
EMAIL_ADDRESS  = os.getenv("EMAIL_ADDRESS",  "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
POLL_INTERVAL  = int(os.getenv("EMAIL_POLL_INTERVAL_SECONDS", "300"))
CLAUDE_MODEL   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

CLASSIFICATION_TO_STATUS = {
    "interview_invite": "interview_invited",
    "rejection":        "rejected_by_company",
    "follow_up":        "awaiting_response",
    "acknowledgement":  None,
    "other":            None,
}

# ── email_events table ─────────────────────────────────────────────────────

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


def _init_email_table():
    conn = sqlite3.connect(log_store.DB_PATH)
    conn.execute(_CREATE_EMAIL_EVENTS)
    conn.commit()
    conn.close()


def _already_processed(msg_id: str) -> bool:
    conn = sqlite3.connect(log_store.DB_PATH)
    row = conn.execute(
        "SELECT id FROM email_events WHERE message_id=?", (msg_id,)
    ).fetchone()
    conn.close()
    return row is not None


def _save_event(
    application_id, msg_id, sender, subject,
    received_at, snippet, classification, note,
):
    conn = sqlite3.connect(log_store.DB_PATH)
    conn.execute(
        """INSERT OR IGNORE INTO email_events
           (application_id,message_id,sender,subject,received_at,
            snippet,classification,confidence_note,processed_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (application_id, msg_id, sender, subject,
         received_at, snippet, classification, note,
         datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def list_email_events(application_id: Optional[int] = None, limit: int = 50) -> list[dict]:
    _init_email_table()
    conn = sqlite3.connect(log_store.DB_PATH)
    conn.row_factory = sqlite3.Row
    if application_id:
        rows = conn.execute(
            "SELECT * FROM email_events WHERE application_id=? ORDER BY received_at DESC LIMIT ?",
            (application_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM email_events ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Domain matching ────────────────────────────────────────────────────────

def _root_domain(s: str) -> Optional[str]:
    try:
        if "://" not in s:
            s = "https://" + s
        parts = urlparse(s).netloc.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else None
    except Exception:
        return None


def _domain_from_email_addr(addr: str) -> Optional[str]:
    m = re.search(r"@([\w.\-]+)", addr)
    return _root_domain(m.group(1)) if m else None


def _find_matching_application(sender: str, subject: str) -> Optional[dict]:
    sender_domain = _domain_from_email_addr(sender)
    pool = (
        log_store.list_all(status="submitted", limit=200)
        + log_store.list_all(status="approved",  limit=200)
        + log_store.list_all(status="interview_invited", limit=200)
    )

    # pass 1: domain match
    if sender_domain:
        for app in pool:
            app_domain = _root_domain(app.get("url") or "")
            if app_domain and sender_domain == app_domain:
                return app

    # pass 2: company name in subject
    subj_lower = subject.lower()
    for app in pool:
        company = (app.get("company_name") or "").lower()
        if company and len(company) > 3 and company in subj_lower:
            return app

    return None


# ── Claude classifier ──────────────────────────────────────────────────────

def _classify(sender: str, subject: str, snippet: str) -> tuple[str, str]:
    """Returns (label, reasoning). Falls back to 'other' on error."""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = f"""Classify this email for a job seeker. Choose EXACTLY ONE label:
  interview_invite   — company invites applicant to interview / assessment
  rejection          — application unsuccessful / position filled
  follow_up          — company needs more info or intent is unclear
  acknowledgement    — automated receipt / "we'll be in touch" with no next step
  other              — not related to a job application

From:    {sender}
Subject: {subject}
Body:    {snippet[:500]}

Reply in this exact JSON format only:
{{"classification": "<label>", "reasoning": "<one sentence>"}}"""

    try:
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        data = json.loads(msg.content[0].text.strip())
        return data.get("classification", "other"), data.get("reasoning", "")
    except Exception as exc:
        print(f"⚠️  Claude classify failed: {exc}")
        return "other", "Classification failed — manual review needed."


# ── IMAP fetcher ───────────────────────────────────────────────────────────

def _decode_val(v: str) -> str:
    parts = decode_header(v)
    out = []
    for part, enc in parts:
        out.append(part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part)
    return " ".join(out)


def _body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    payload = msg.get_payload(decode=True)
    return (payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            if payload else "")


def _fetch_unseen():
    """Yield unseen emails as dicts. Raises RuntimeError on IMAP failure."""
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise RuntimeError("EMAIL_ADDRESS and EMAIL_PASSWORD must be set in .env")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    mail.select("INBOX")
    _, nums = mail.search(None, "UNSEEN")
    for num in nums[0].split():
        _, data = mail.fetch(num, "(RFC822)")
        raw = data[0][1]
        m = email.message_from_bytes(raw)
        yield {
            "message_id": (m.get("Message-ID") or "").strip(),
            "sender":     _decode_val(m.get("From", "")),
            "subject":    _decode_val(m.get("Subject", "")),
            "date":       m.get("Date", ""),
            "body":       _body(m).strip(),
        }
    mail.logout()


# ── One polling cycle ──────────────────────────────────────────────────────

def poll_once() -> list[dict]:
    """Fetch unseen emails, classify relevant ones, update job log. Returns processed events."""
    _init_email_table()
    processed = []

    try:
        for e in _fetch_unseen():
            msg_id = e["message_id"]
            if not msg_id or _already_processed(msg_id):
                continue

            app = _find_matching_application(e["sender"], e["subject"])
            if not app:
                continue  # not related to a known application

            classification, reasoning = _classify(e["sender"], e["subject"], e["body"])

            new_status = CLASSIFICATION_TO_STATUS.get(classification)
            if new_status:
                conn = sqlite3.connect(log_store.DB_PATH)
                conn.execute(
                    "UPDATE job_applications SET status=? WHERE id=?",
                    (new_status, app["id"]),
                )
                conn.commit()
                conn.close()

            _save_event(
                app["id"], msg_id, e["sender"], e["subject"],
                e["date"], e["body"][:500], classification, reasoning,
            )

            _notify(app, classification, e["sender"], e["subject"], reasoning)

            event = {
                "application_id": app["id"],
                "company":        app["company_name"],
                "role":           app["job_role"],
                "classification": classification,
                "sender":         e["sender"],
                "subject":        e["subject"],
            }
            processed.append(event)
            print(f"📧 [{classification}] {e['subject'][:60]} → {app['company_name']}")

    except Exception as exc:
        print(f"⚠️  Email poll failed: {exc}")

    return processed


def _notify(app: dict, classification: str, sender: str, subject: str, note: str):
    labels = {
        "interview_invite": "INTERVIEW INVITE",
        "rejection":        "REJECTION",
        "follow_up":        "FOLLOW-UP NEEDED",
        "acknowledgement":  "ACKNOWLEDGEMENT",
    }
    label = labels.get(classification, "EMAIL")
    msg = (
        f"[Job Assistant] {label}\n"
        f"{app['company_name']} — {app['job_role']}\n"
        f"From: {sender}\n"
        f"Subject: {subject[:80]}\n"
        f"Reason: {note}"
    )
    try:
        PushNotificationTool()._run(message=msg)
    except Exception:
        pass
    notify_email_received(
        app["id"], app["company_name"], app["job_role"],
        classification, sender, subject,
    )


# ── Background thread ──────────────────────────────────────────────────────

class EmailMonitor:
    """
    Runs poll_once() in a daemon thread every POLL_INTERVAL seconds.

    Usage:
        monitor = EmailMonitor()
        monitor.start()   # call once at app startup
        monitor.stop()    # call on shutdown (or let the daemon die with the process)
    """

    def __init__(self, interval: int = POLL_INTERVAL):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_poll: Optional[str] = None
        self.total_processed: int = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="EmailMonitor")
        self._thread.start()
        print(f"📬 Email monitor started (interval={self.interval}s)")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        print("📬 Email monitor stopped")

    def status(self) -> dict:
        return {
            "running":         self._thread.is_alive() if self._thread else False,
            "interval_secs":   self.interval,
            "last_poll":       self.last_poll,
            "total_processed": self.total_processed,
            "configured":      bool(EMAIL_ADDRESS and EMAIL_PASSWORD),
        }

    def _run(self):
        while not self._stop.is_set():
            events = poll_once()
            self.total_processed += len(events)
            self.last_poll = datetime.utcnow().isoformat()
            self._stop.wait(self.interval)


# Module-level singleton — started by main.py on app startup
monitor = EmailMonitor()
