"""Sends the sign-in emails.

The server sends them itself over SMTP, as your own mailbox (for Gmail: an *app password*, not your real password).
Deskhand never drives your Gmail website to do this, so it does not need your inbox open or logged in.

Environment (set on the server, e.g. in /etc/deskhand.env; never in the repo):
  TASKPILOT_SMTP_USER       you@gmail.com
  TASKPILOT_SMTP_PASSWORD   the 16-character app password (Google Account > Security > 2-Step Verification > App passwords)
  TASKPILOT_SMTP_HOST       default smtp.gmail.com        TASKPILOT_SMTP_PORT  default 465 (SSL)
  TASKPILOT_MAIL_FROM_NAME  default Deskhand
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("taskpilot.mail")


class MailError(RuntimeError):
    pass


class SmtpMailer:
    def __init__(self, user: str, password: str, host: str = "smtp.gmail.com", port: int = 465,
                 from_name: str = "Deskhand", timeout: float = 20):
        self.user, self.password, self.host, self.port = user, password, host, port
        self.from_name, self.timeout = from_name, timeout

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        msg = EmailMessage()
        msg["From"] = formataddr((self.from_name, self.user))
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ssl.create_default_context()) as s:
                    s.login(self.user, self.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
                    s.starttls(context=ssl.create_default_context())
                    s.login(self.user, self.password)
                    s.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"{type(e).__name__}: {str(e)[:120]}") from None       # never include the password


def from_env() -> SmtpMailer | None:
    user, pw = os.environ.get("TASKPILOT_SMTP_USER"), os.environ.get("TASKPILOT_SMTP_PASSWORD")
    if not (user and pw):
        return None
    return SmtpMailer(user, pw.replace(" ", ""), os.environ.get("TASKPILOT_SMTP_HOST", "smtp.gmail.com"),
                      int(os.environ.get("TASKPILOT_SMTP_PORT", "465")), os.environ.get("TASKPILOT_MAIL_FROM_NAME", "Deskhand"))
