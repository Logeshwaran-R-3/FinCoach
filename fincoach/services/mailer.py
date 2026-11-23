"""Outgoing email for bill reminders. Stdlib-only (smtplib + email).

Configuration comes from the environment (.env): SMTP_HOST, SMTP_PORT
(default 587), SMTP_USER, SMTP_PASS, SMTP_FROM. An empty SMTP_HOST means
email is not configured: callers show a setup hint instead of sending.

Test mode: pass a list as `capture` and the message dict is appended to it
instead of touching the network. The /reminders/send route wires
app.config["MAIL_CAPTURE"] through, so the suite sends zero real mail.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from fincoach import config


def smtp_config() -> dict:
    """Current SMTP settings (read live so tests can patch config attrs)."""
    return {"host": config.SMTP_HOST, "port": config.SMTP_PORT,
            "user": config.SMTP_USER, "password": config.SMTP_PASS,
            "sender": config.SMTP_FROM or config.SMTP_USER}


def is_configured() -> bool:
    return bool(config.SMTP_HOST)


def send_email(to_email: str, subject: str, body: str,
               capture: list | None = None) -> dict:
    """Send one plain-text email; with `capture`, record it instead.

    Returns {"to", "subject", "body"} on success. Raises ValueError with a
    user-safe message when email is not configured or the send fails.
    """
    to_email = (to_email or "").strip()
    if not to_email:
        raise ValueError("No recipient email address.")
    record = {"to": to_email, "subject": subject, "body": body}
    if capture is not None:
        capture.append(record)
        return record
    cfg = smtp_config()
    if not cfg["host"]:
        raise ValueError(
            "Email is not configured. Set SMTP_HOST/SMTP_USER/SMTP_PASS in "
            ".env (see README 'Bill reminders by email').")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["sender"] or cfg["user"]
    msg["To"] = to_email
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as smtp:
            smtp.starttls()
            if cfg["user"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise ValueError(f"Could not send email ({exc}). "
                         "Check SMTP_HOST/SMTP_PORT and your connection.")
    return record


def digest_subject(count: int) -> str:
    return (f"FinCoach: {count} bill reminder" +
            ("s due soon" if count != 1 else " due soon"))


def digest_body(reminders: list[dict]) -> str:
    lines = ["Bills due soon (from your FinCoach reminders):", ""]
    for r in reminders:
        lines.append(f"- {r.get('text', '')}: "
                     f"Rs {float(r.get('amount') or 0):,.0f} "
                     f"due {r.get('due_date', '')}")
    lines += ["", "Open FinCoach -> Reminders to mark them done.",
              "FinCoach is for educational purposes only."]
    return "\n".join(lines)


def send_due_digest(to_email: str, due: list[dict],
                    capture: list | None = None) -> dict | None:
    """Email one digest for all due reminders; None when nothing is due."""
    if not due:
        return None
    return send_email(to_email, digest_subject(len(due)), digest_body(due),
                      capture=capture)
