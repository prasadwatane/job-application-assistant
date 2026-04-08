"""
Email Sender Service
=====================
Sends SMTP email notifications.

Configure in .env:
  SMTP_HOST     — e.g. smtp.gmail.com
  SMTP_PORT     — e.g. 587  (TLS) or 465 (SSL)
  SMTP_USER     — your email address
  SMTP_PASSWORD — app password (not your account password)
  NOTIFY_TO     — recipient address (defaults to SMTP_USER)

Works with Gmail, Outlook, and any SMTP provider.
For Gmail: enable 2FA → generate an App Password → use that as SMTP_PASSWORD.
"""

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFY_TO     = os.getenv("NOTIFY_TO",    SMTP_USER)


def _configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD and NOTIFY_TO)


def send_notification(
    subject: str,
    body: str,
    to: str | None = None,
    html_body: str | None = None,
) -> bool:
    """
    Send a notification email.

    Parameters
    ----------
    subject   : Email subject line
    body      : Plain-text body
    to        : Recipient (defaults to NOTIFY_TO env var)
    html_body : Optional HTML version of the body

    Returns True on success, False on any failure (non-fatal).
    """
    if not _configured():
        print("⚠️  SMTP not configured — skipping email notification. Set SMTP_* in .env")
        return False

    recipient = to or NOTIFY_TO
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = recipient

    msg.attach(MIMEText(body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, recipient, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, recipient, msg.as_string())
        print(f"✉️  Email sent: {subject}")
        return True
    except Exception as exc:
        print(f"⚠️  Email send failed (non-fatal): {exc}")
        return False


# ── Pre-built notification templates ──────────────────────────────────────

def notify_review_ready(log_id: int, company: str, role: str, fit_score: float) -> bool:
    subject = f"[Job Assistant] Review needed #{log_id} — {company} {role}"
    body = (
        f"A new application is ready for your review.\n\n"
        f"  Company:   {company}\n"
        f"  Role:      {role}\n"
        f"  Fit score: {fit_score:.0f}/100\n"
        f"  Log ID:    #{log_id}\n\n"
        f"Action required:\n"
        f"  Approve → POST /review/{log_id}/approve\n"
        f"  Reject  → POST /review/{log_id}/reject\n\n"
        f"View all pending: GET /review/pending\n\n"
        f"— Job Application Assistant"
    )
    return send_notification(subject, body)


def notify_email_received(
    log_id: int,
    company: str,
    role: str,
    classification: str,
    sender: str,
    subject_line: str,
) -> bool:
    emoji = {
        "interview_invite": "INTERVIEW INVITE",
        "rejection":        "REJECTION",
        "follow_up":        "FOLLOW-UP NEEDED",
        "acknowledgement":  "ACKNOWLEDGEMENT",
    }.get(classification, "EMAIL")

    subject = f"[Job Assistant] {emoji} — {company} ({role})"
    body = (
        f"An email related to your application has arrived.\n\n"
        f"  Company:        {company}\n"
        f"  Role:           {role}\n"
        f"  Classification: {classification}\n"
        f"  From:           {sender}\n"
        f"  Subject:        {subject_line}\n"
        f"  Log ID:         #{log_id}\n\n"
        f"View application: GET /review/{log_id}\n\n"
        f"— Job Application Assistant"
    )
    return send_notification(subject, body)


def notify_ats_warning(log_id: int, company: str, role: str, ats_score: float, issues: list[str]) -> bool:
    subject = f"[Job Assistant] Low ATS score ({ats_score:.0f}) — {company} {role}"
    issues_text = "\n".join(f"  • {i}" for i in issues[:5])
    body = (
        f"The ATS optimizer flagged issues with application #{log_id}.\n\n"
        f"  Company:   {company}\n"
        f"  Role:      {role}\n"
        f"  ATS score: {ats_score:.0f}/100 (threshold: 60)\n\n"
        f"Top issues:\n{issues_text}\n\n"
        f"Consider adding a command to address these:\n"
        f"  POST /commands  body: {{\"command\": \"...\", \"type\": \"content\"}}\n\n"
        f"— Job Application Assistant"
    )
    return send_notification(subject, body)
