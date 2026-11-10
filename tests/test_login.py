"""Self-service sign-in by email: anyone can get in on their own, and nobody can abuse it."""
import re
import smtplib

import pytest

from taskpilot.login import AuthSettings, clean_email, identity
from taskpilot.mailer import MailError, SmtpMailer
from test_client_api import ADMIN, auth, env  # noqa: F401

PUBLIC = "https://pilot.example"


class FakeMailer:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, to, subject, text, html=None):
        if self.fail:
            raise MailError("SMTPException: boom")
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html})

    def last_code(self):
        return re.search(r"#code=([A-Z0-9]{4}-[A-Z0-9]{4})", self.sent[-1]["text"]).group(1)


@pytest.fixture
def mail(env):
    c, store, hub, app = env
    m = FakeMailer()
    app.state.auth = AuthSettings(mailer=m, signup="open", public_url=PUBLIC, default_quota=7)
    return c, store, m, app


def signin(c, m, email, name="My phone"):
    assert c.post("/v1/auth/email", json={"email": email}).status_code == 202
    r = c.post("/v1/auth/verify", json={"code": m.last_code(), "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_anyone_can_sign_up_with_an_email_and_gets_their_own_private_workspace(mail):
    c, store, m, _ = mail
    r = c.post("/v1/auth/email", json={"email": "Sam@Example.com"})
    assert r.status_code == 202 and r.json() == {"sent": True}
    msg = m.sent[0]
    assert msg["to"] == "sam@example.com" and "Sign in to Deskhand" in msg["html"]
    assert f"{PUBLIC}/app/#code=" in msg["text"] and "15 minutes" in msg["text"]
    code = m.last_code()
    v = c.post("/v1/auth/verify", json={"code": code, "name": "Sam's phone"}).json()
    assert v["new_workspace"] is True
    me = c.get("/v1/me", headers=auth(v["token"])).json()
    assert me["workspace"]["name"] == "sam's workspace" and me["devices"] == []        # empty: not the owner's laptop
    assert c.get("/v1/workspace", headers=auth(v["token"])).json()["max_tasks_per_day"] == 7   # small default quota
    assert c.post("/v1/auth/verify", json={"code": code, "name": "again"}).status_code == 403   # single use


def test_the_same_person_signing_in_again_joins_their_own_workspace_not_a_new_one(mail):
    c, store, m, _ = mail
    first = signin(c, m, "sam@gmail.com")
    second = signin(c, m, "S.a.m+shopping@GoogleMail.com", "Sam's laptop")        # Gmail aliases are the same person
    assert second["workspace"] == first["workspace"] and second["new_workspace"] is False
    names = {x["name"] for x in c.get("/v1/members", headers=auth(first["token"])).json()}
    assert names == {"My phone", "Sam's laptop"}
    assert len([w for w in store.list_workspaces() if w["owner_email"]]) == 1


def test_two_different_people_are_fully_separate(mail):
    c, store, m, _ = mail
    a, b = signin(c, m, "alice@example.com"), signin(c, m, "bob@example.com")
    assert a["workspace"] != b["workspace"]
    code = c.post("/v1/pair-code", headers=auth(a["token"])).json()["code"]
    assert c.get("/v1/members", headers=auth(b["token"])).json()[0]["name"] == "My phone"
    assert len(c.get("/v1/members", headers=auth(b["token"])).json()) == 1


def test_closed_signup_blocks_new_people_but_not_existing_ones(mail):
    c, store, m, app = mail
    signin(c, m, "sam@example.com")
    app.state.auth.signup = "closed"
    assert c.post("/v1/auth/email", json={"email": "stranger@example.com"}).status_code == 403
    assert signin(c, m, "sam@example.com")["new_workspace"] is False


def test_domain_allowlist_and_workspace_cap(mail):
    c, store, m, app = mail
    app.state.auth.allowed_domains = ["school.edu"]
    assert c.post("/v1/auth/email", json={"email": "x@gmail.com"}).status_code == 403
    assert signin(c, m, "x@school.edu")["new_workspace"] is True
    app.state.auth.allowed_domains = []
    app.state.auth.max_workspaces = len(store.list_workspaces())
    assert c.post("/v1/auth/email", json={"email": "late@example.com"}).status_code == 403    # server is full
    assert signin(c, m, "x@school.edu")["new_workspace"] is False                              # existing user unaffected


def test_requests_are_rate_limited_per_email_and_per_address(mail):
    c, store, m, app = mail
    for _ in range(5):
        assert c.post("/v1/auth/email", json={"email": "spam@example.com"}).status_code == 202
    assert c.post("/v1/auth/email", json={"email": "spam@example.com"}).status_code == 429
    assert c.post("/v1/auth/email", json={"email": "Spam+x@example.com"}).status_code == 202   # not gmail: a different person


def test_per_address_limit(mail):
    c, store, m, app = mail
    codes = [c.post("/v1/auth/email", json={"email": f"u{i}@example.com"}).status_code for i in range(12)]
    assert codes[:10] == [202] * 10 and codes[10:] == [429, 429]


def test_wrong_codes_are_rate_limited(mail):
    c, *_ = mail
    results = [c.post("/v1/auth/verify", json={"code": "AAAA-BBBB", "name": "x"}).status_code for _ in range(10)]
    assert results[:8] == [403] * 8 and results[8:] == [429, 429]


def test_a_forged_host_header_cannot_redirect_the_sign_in_link(mail):
    c, store, m, app = mail
    c.post("/v1/auth/email", json={"email": "victim@example.com"}, headers={"Host": "evil.example",
                                                                         "X-Forwarded-Host": "evil.example"})
    assert "evil.example" not in m.sent[0]["text"] and "evil.example" not in m.sent[0]["html"]
    assert PUBLIC in m.sent[0]["text"]


def test_without_a_configured_public_url_no_email_is_sent(mail):
    c, store, m, app = mail
    app.state.auth.public_url = ""
    assert c.post("/v1/auth/email", json={"email": "a@example.com"}).status_code == 503
    assert m.sent == []


def test_bad_addresses_are_rejected(mail):
    c, *_ = mail
    for bad in ["nope", "a@b", "a b@example.com", "a..b@example.com", "x" * 300 + "@example.com", ""]:
        assert c.post("/v1/auth/email", json={"email": bad}).status_code in (422,), bad
    assert clean_email(" Sam@Example.COM ") == "sam@example.com"
    assert identity("a.b+c@gmail.com") == "ab@gmail.com" and identity("a.b+c@example.com") == "a.b+c@example.com"


def test_mail_failure_is_reported_without_leaking_details(mail):
    c, store, m, app = mail
    m.fail = True
    r = c.post("/v1/auth/email", json={"email": "a@example.com"})
    assert r.status_code == 502 and "boom" not in r.text


def test_no_mailer_configured_says_so_and_never_returns_the_code(mail):
    c, store, m, app = mail
    app.state.auth.mailer = None
    r = c.post("/v1/auth/email", json={"email": "a@example.com"})
    assert r.status_code == 202 and r.json()["sent"] is False
    assert not re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}", r.text)                       # the code only ever travels by email


def test_a_switched_off_workspace_cannot_sign_back_in(mail):
    c, store, m, app = mail
    first = signin(c, m, "sam@example.com")
    c.patch(f"/workspaces/{first['workspace']}", json={"disabled": True}, headers=ADMIN)
    c.post("/v1/auth/email", json={"email": "sam@example.com"})
    assert c.post("/v1/auth/verify", json={"code": m.last_code(), "name": "x"}).status_code == 403


def test_the_app_can_ask_whether_email_sign_in_is_available(mail):
    c, store, m, app = mail
    assert c.get("/v1/auth/config").json() == {"email_login": True, "signup": "open", "extension_url": ""}
    app.state.auth.mailer, app.state.auth.signup = None, "closed"
    assert c.get("/v1/auth/config").json()["email_login"] is False


def test_smtp_errors_never_contain_the_password(monkeypatch):
    def boom(*a, **k):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials for hunter2hunter2")

    class FakeSSL:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        login = boom
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSSL)
    with pytest.raises(MailError) as e:
        SmtpMailer("me@gmail.com", "hunter2hunter2").send("a@b.co", "s", "t")
    assert "SMTPAuthenticationError" in str(e.value)


def test_from_env_strips_the_spaces_google_shows_in_app_passwords(monkeypatch):
    from taskpilot import mailer
    monkeypatch.setenv("TASKPILOT_SMTP_USER", "me@gmail.com")
    monkeypatch.setenv("TASKPILOT_SMTP_PASSWORD", "abcd efgh ijkl mnop")
    assert mailer.from_env().password == "abcdefghijklmnop"
    monkeypatch.delenv("TASKPILOT_SMTP_PASSWORD")
    assert mailer.from_env() is None
