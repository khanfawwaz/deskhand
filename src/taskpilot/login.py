"""Self-service sign-in by email: anyone can get in without you doing anything.

  POST /v1/auth/email   {email}        -> emails a sign-in link + 8-character code (valid 15 min, single use)
  POST /v1/auth/verify  {code, name}   -> redeems it: new email = a brand-new private workspace, known email = a new
                                          controller in their existing workspace. Returns the controller token.

Proof of ownership is the email itself, so there are no passwords to leak or reset. The link carries the code in the
#fragment, which browsers never send to a server, so it does not appear in any server log or referrer.

Abuse controls (it is open to the internet, so these matter): per-address and per-email rate limits, a cap on the total
number of workspaces, an optional allow-list of email domains, small default task quotas, and a Gmail-aware identity
(a.b+x@gmail.com is the same person as ab@gmail.com, so aliases cannot farm free workspaces).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .mailer import MailError, SmtpMailer, from_env

log = logging.getLogger("taskpilot.login")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")
GMAIL = {"gmail.com", "googlemail.com"}
LOGIN_TTL = 15 * 60


@dataclass
class AuthSettings:
    mailer: Any = None                                   # object with .send(to, subject, text, html)
    signup: str = "closed"                               # open = anyone may create a workspace; closed = existing users only
    allowed_domains: list[str] = field(default_factory=list)     # [] = any domain
    max_workspaces: int = 1000
    default_quota: int = 30                              # tasks/day for a self-service workspace
    public_url: str = ""                                 # origin used in the email link; blank = whatever the caller used
    per_ip_hour: int = 10
    per_email_hour: int = 5

    @classmethod
    def from_env(cls) -> "AuthSettings":
        mailer = from_env()
        mode = os.environ.get("TASKPILOT_SIGNUP") or "open"          # anyone may sign up; set TASKPILOT_SIGNUP=closed to stop it
        doms = [d.strip().lower() for d in os.environ.get("TASKPILOT_SIGNUP_DOMAINS", "").split(",") if d.strip()]
        return cls(mailer=mailer, signup=mode, allowed_domains=doms,
                   max_workspaces=int(os.environ.get("TASKPILOT_MAX_WORKSPACES", "1000")),
                   default_quota=int(os.environ.get("TASKPILOT_SIGNUP_TASKS_PER_DAY", "30")),
                   public_url=os.environ.get("TASKPILOT_PUBLIC_URL", "").rstrip("/"))


def extension_url() -> str:
    """Where to get the Deskhand Chrome extension (TASKPILOT_EXTENSION_URL), or '' while it is not published.
    Only a plain https link is ever passed to the page: nothing else can become a clickable link there."""
    u = os.environ.get("TASKPILOT_EXTENSION_URL", "").strip()
    return u if u.lower().startswith("https://") and len(u) < 400 and " " not in u else ""


def clean_email(raw: str) -> str:
    e = (raw or "").strip().lower()
    if len(e) > 254 or not EMAIL_RE.match(e) or ".." in e:
        raise HTTPException(422, "that does not look like an email address")
    return e


def identity(email: str) -> str:
    """One person = one key. Gmail ignores dots and +tags, so we do too."""
    local, _, domain = email.partition("@")
    if domain in GMAIL:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


class _Limiter:
    def __init__(self, per_hour: int):
        self.per_hour, self.hits = per_hour, defaultdict(deque)

    def check(self, key: str) -> None:
        now, q = time.time(), self.hits[key]
        while q and q[0] < now - 3600:
            q.popleft()
        if len(q) >= self.per_hour:
            raise HTTPException(429, "too many sign-in emails requested; try again in a little while")
        q.append(now)


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class VerifyIn(BaseModel):
    code: str = Field(min_length=4, max_length=20)
    name: str = Field("This device", min_length=1, max_length=60)


def _message(link: str, code: str, new: bool) -> tuple[str, str, str]:
    subject = "Your Deskhand sign-in link"
    lead = "Welcome to Deskhand." if new else "Welcome back to Deskhand."
    text = (f"{lead}\n\nOpen this link to sign in on this device:\n{link}\n\n"
            f"Or enter this code in the app: {code}\n\n"
            "It works once and expires in 15 minutes. If you did not ask for it, ignore this email: nothing happens.\n")
    html = (f"<p>{lead}</p><p><a href=\"{link}\" style=\"display:inline-block;padding:12px 18px;background:#504CA5;"
            f"color:#fff;border-radius:8px;text-decoration:none\">Sign in to Deskhand</a></p>"
            f"<p>Or enter this code in the app: <b style=\"font-size:20px;letter-spacing:2px\">{code}</b></p>"
            "<p style=\"color:#666\">It works once and expires in 15 minutes. If you did not ask for it, ignore this "
            "email: nothing happens.</p>")
    return subject, text, html


def install_login(app: FastAPI, store, pairing_cls, settings: AuthSettings | None = None):
    app.state.auth = settings or AuthSettings.from_env()
    logins = pairing_cls(ttl=LOGIN_TTL)
    app.state.logins = logins
    by_ip, by_email = _Limiter(app.state.auth.per_ip_hour), _Limiter(app.state.auth.per_email_hour)

    create_lock = threading.Lock()

    def origin(request: Request) -> str:
        """The address put in the email. It MUST come from configuration, never from the request: a forged Host header
        would otherwise make us email a victim a link to the attacker's site with a working sign-in code in it."""
        a = app.state.auth
        if not a.public_url:
            log.error("TASKPILOT_PUBLIC_URL is not set: refusing to email sign-in links")
            raise HTTPException(503, "sign-in by email is not set up on this server yet")
        return a.public_url

    @app.get("/v1/auth/config")
    def config():
        a = app.state.auth
        return {"email_login": a.signup == "open" or a.mailer is not None, "signup": a.signup,
                "extension_url": extension_url()}

    @app.post("/v1/auth/email", status_code=202)
    async def request_login(body: EmailIn, request: Request):
        a = app.state.auth
        email = clean_email(body.email)
        ident = identity(email)
        ip = request.client.host if request.client else "?"
        by_ip.check(ip)
        by_email.check(ident)
        existing = store.find_workspace_by_email(ident)
        if existing is None:
            if a.signup != "open":
                raise HTTPException(403, "new sign-ups are closed on this server")
            domain = email.rpartition("@")[2]
            if a.allowed_domains and domain not in a.allowed_domains:
                raise HTTPException(403, "sign-ups are limited to specific email domains on this server")
            if len(store.list_workspaces()) >= a.max_workspaces:
                raise HTTPException(403, "this server is full right now")
        base = origin(request) if a.mailer is not None else (a.public_url or "http://localhost")
        code = logins.create({"email": ident, "send_to": email})
        link = f"{base}/app/#code={code}"
        subject, text, html = _message(link, code, new=existing is None)
        if a.mailer is None:
            log.warning("email is not configured: sign-in link for %s is %s", email, link)    # owner can read it here
            return {"sent": False, "reason": "this server cannot send email yet"}
        try:
            await asyncio.to_thread(a.mailer.send, email, subject, text, html)
        except MailError as e:
            log.error("could not send sign-in email: %s", e)
            raise HTTPException(502, "could not send the email right now; try again shortly") from None
        return {"sent": True}

    @app.post("/v1/auth/verify")
    def verify(body: VerifyIn, request: Request):
        a = app.state.auth
        ip = request.client.host if request.client else "?"
        payload = logins.take(body.code, ip)
        if payload is None:
            raise HTTPException(403, "that code is wrong, already used, or expired: ask for a new one")
        ident = payload["email"]
        with create_lock:                                    # two codes for the same new email must not make two workspaces
            ws = store.find_workspace_by_email(ident)
            new = ws is None
            if new:
                if len(store.list_workspaces()) >= a.max_workspaces:
                    raise HTTPException(403, "this server is full right now")
                local = payload["send_to"].partition("@")[0]
                wid = store.create_workspace(f"{local}'s workspace", a.default_quota, owner_email=ident)
            else:
                wid = ws["id"]
                if ws.get("disabled"):
                    raise HTTPException(403, "this workspace is switched off; ask the person who runs the server")
            cid, token = store.create_client(body.name, workspace=wid)
        return {"client_id": cid, "token": token, "name": body.name, "workspace": wid, "new_workspace": new,
                "note": "this token is shown once and stored only on this device"}
