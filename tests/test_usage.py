"""Usage tracking, the shared model queue and the daily spend cap."""
import asyncio
import time

import pytest

from taskpilot.hosted import RateLimited
from taskpilot.model import ModelContext, ModelRuntime
from taskpilot.modelgate import ModelGate
from taskpilot.store import Store
from taskpilot.usage import BudgetExceeded, Limits, UsageMeter, report_usage

from conftest import act, done


def ctx(ws="w1", goal="g", task="t1"):
    return ModelContext(goal=goal, state="running", history=[], screenshot_b64=None, screenshot_size=None, workspace=ws, task_id=task)


class Fake(ModelRuntime):
    """A model that takes a moment, reports tokens the way a real runtime does, and tracks how many calls overlap."""

    def __init__(self, delay=0.05, tokens=(100, 20), fail=None):
        self.delay, self.tokens, self.fail = delay, tokens, fail
        self.now = self.peak = 0
        self.by_ws: dict[str, int] = {}
        self.peak_ws: dict[str, int] = {}
        self.calls = 0

    async def propose(self, c):
        self.calls += 1
        self.now += 1
        self.peak = max(self.peak, self.now)
        self.by_ws[c.workspace] = self.by_ws.get(c.workspace, 0) + 1
        self.peak_ws[c.workspace] = max(self.peak_ws.get(c.workspace, 0), self.by_ws[c.workspace])
        try:
            await asyncio.sleep(self.delay)
            if self.fail:
                raise self.fail
            report_usage("fake-model", *self.tokens)
            return "{}"
        finally:
            self.now -= 1
            self.by_ws[c.workspace] -= 1


# ------------------------------------------------------------------ the meter
def test_every_call_is_recorded_priced_and_survives_a_restart():
    store = Store()
    m = UsageMeter(store, Limits(price_in=2.0, price_out=10.0))                 # $2 / $10 per million tokens
    cost = m.record(workspace="w1", task_id="t", model="x", kind="decide", prompt=1_000_000, completion=100_000,
                    latency_ms=900, status="ok")
    assert cost == pytest.approx(3.0)
    m.record(workspace="w2", task_id="u", model="x", kind="decide", prompt=10, completion=5, latency_ms=10, status="error")
    t = m.today()
    assert t["calls"] == 2 and t["errors"] == 1 and t["tokens"] == 1_100_015 and t["cost_usd"] == pytest.approx(3.0, abs=1e-3)
    assert m.today("w1")["tokens"] == 1_100_000
    fresh = UsageMeter(store, Limits(price_in=2.0, price_out=10.0))             # a new process reads today's totals back
    assert fresh.today()["calls"] == 2 and fresh.today("w2")["calls"] == 1
    top = store.usage_since(0)["by_workspace"]
    assert [w["workspace"] for w in top] == ["w1", "w2"]                        # busiest first


def test_the_daily_budget_stops_further_calls_with_a_plain_message():
    m = UsageMeter(Store(), Limits(price_in=1.0, price_out=1.0, daily_budget_usd=1.0))
    m.check("w")                                                                # nothing spent yet
    m.record(workspace="w", task_id="", model="x", kind="d", prompt=600_000, completion=500_000, latency_ms=1, status="ok")
    with pytest.raises(BudgetExceeded, match="budget for today"):
        m.check("w")


def test_a_token_cap_works_even_when_no_prices_are_set_and_a_workspace_cap_only_stops_that_workspace():
    m = UsageMeter(Store(), Limits(daily_token_cap=1000, ws_tokens_per_day=300))
    m.record(workspace="busy", task_id="", model="x", kind="d", prompt=250, completion=100, latency_ms=1, status="ok")
    with pytest.raises(BudgetExceeded, match="This workspace"):
        m.check("busy")
    m.check("quiet")                                                            # others are not affected
    m.record(workspace="quiet", task_id="", model="x", kind="d", prompt=700, completion=0, latency_ms=1, status="ok")
    with pytest.raises(BudgetExceeded, match="daily usage limit"):
        m.check("quiet")                                                        # but the service-wide cap stops everyone


def test_a_broken_store_never_stops_the_meter():
    class Broken(Store):
        def add_usage(self, rec): raise RuntimeError("disk full")
    m = UsageMeter(Broken(), Limits())
    m.record(workspace="w", task_id="", model="x", kind="d", prompt=1, completion=1, latency_ms=1, status="ok")
    assert m.today()["calls"] == 1


# ------------------------------------------------------------------ the shared queue
async def test_no_more_than_the_limit_runs_at_once_and_everyone_is_served():
    fake = Fake()
    gate = ModelGate(fake, UsageMeter(Store()), max_concurrent=2, per_workspace=5)
    await asyncio.gather(*(gate.propose(ctx(ws=f"w{i}")) for i in range(8)))
    assert fake.peak == 2 and fake.calls == 8 and gate.served == 8 and gate.waiting == 0 and gate.in_flight == 0


async def test_one_busy_workspace_cannot_take_every_slot():
    fake = Fake(delay=0.08)
    gate = ModelGate(fake, UsageMeter(Store()), max_concurrent=4, per_workspace=1)
    noisy = [asyncio.create_task(gate.propose(ctx(ws="noisy"))) for _ in range(4)]
    await asyncio.sleep(0.01)
    t0 = time.monotonic()
    await gate.propose(ctx(ws="polite"))                                        # served at once, not after the noisy four
    assert time.monotonic() - t0 < 0.16
    await asyncio.gather(*noisy)
    assert fake.peak_ws["noisy"] == 1


async def test_a_rate_limit_makes_everyone_wait_together_instead_of_hammering_the_provider():
    fake = Fake(delay=0, fail=RateLimited("HTTP 429 after 5 attempts"))
    gate = ModelGate(fake, UsageMeter(Store()), max_concurrent=3)
    with pytest.raises(RateLimited):
        await gate.propose(ctx())
    st = gate.status()
    assert st["rate_limited"] == 1 and st["cooldown_s"] > 1                    # the whole line is now paused
    fake.fail = None
    t0 = time.monotonic()
    gate._pause_until = time.monotonic() + 0.3                                  # shorten it for the test
    await gate.propose(ctx(ws="other"))
    assert time.monotonic() - t0 >= 0.25                                        # another workspace waited out the pause


async def test_usage_comes_from_the_api_numbers_or_a_rough_estimate_when_it_gives_none():
    meter = UsageMeter(Store(), Limits(price_in=1.0, price_out=1.0))
    gate = ModelGate(Fake(tokens=(500, 60)), meter)
    await gate.propose(ctx(ws="a", task="t9"))
    assert meter.today("a")["tokens"] == 560
    quiet = Fake()
    quiet.tokens = (0, 0)                                                       # a provider that sends no usage numbers
    gate2 = ModelGate(quiet, meter)
    await gate2.propose(ctx(ws="b", goal="x" * 4000))
    assert meter.today("b")["tokens"] >= 1000                                   # estimated from the text, not zero


async def test_a_spent_budget_refuses_before_waiting_and_before_calling_the_model():
    meter = UsageMeter(Store(), Limits(daily_token_cap=10))
    meter.record(workspace="w", task_id="", model="x", kind="d", prompt=20, completion=0, latency_ms=1, status="ok")
    fake = Fake()
    gate = ModelGate(fake, meter, max_concurrent=1)
    with pytest.raises(BudgetExceeded):
        await gate.propose(ctx())
    assert fake.calls == 0 and gate.waiting == 0


async def test_errors_are_recorded_as_errors_and_free_their_slot():
    meter = UsageMeter(Store())
    gate = ModelGate(Fake(fail=RuntimeError("boom")), meter, max_concurrent=1)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await gate.propose(ctx())
    assert meter.today()["errors"] == 3 and gate.in_flight == 0


# ------------------------------------------------------------------ in the real flow
async def test_a_task_fails_with_a_clear_message_when_the_budget_is_spent(make_env):
    e = make_env([act("capture_screen"), done()])
    meter = UsageMeter(Store(), Limits(daily_token_cap=5))
    meter.record(workspace="main", task_id="", model="x", kind="d", prompt=50, completion=0, latency_ms=1, status="ok")
    e.orch.model = ModelGate(e.orch.model, meter)
    tid, t = await e.run("Do something")
    assert t["status"] == "failed" and "daily usage limit" in t["error"] and "model runtime error" not in t["error"]


async def test_the_model_is_told_whose_task_it_is(make_env):
    seen = []

    class Spy(ModelRuntime):
        def __init__(self, inner): self.inner = inner
        async def propose(self, c):
            seen.append((c.workspace, c.task_id))
            return await self.inner.propose(c)

    e = make_env([done("Notepad visible")])
    e.orch.model = Spy(e.orch.model)
    tid, _ = await e.run("g")
    assert seen and seen[0][1] == tid and seen[0][0]                            # a workspace id and the task id travel with the call


# ------------------------------------------------------------------ the API
def test_the_app_and_the_owner_can_see_usage(tmp_path):
    from fastapi.testclient import TestClient
    from taskpilot.devices import DeviceHub
    from taskpilot.model import ScriptedRuntime
    from taskpilot.orchestrator import Config, Orchestrator
    from taskpilot.policy import Policy
    from taskpilot.server import create_app
    store, hub = Store(), DeviceHub()
    app = create_app(store, hub, Orchestrator(store, hub, ScriptedRuntime([]), Policy.from_dict({}), Config()), "adminkey")
    meter = UsageMeter(store, Limits(price_in=1.0, price_out=1.0, daily_budget_usd=5.0))
    app.state.meter, app.state.gate = meter, ModelGate(Fake(), meter)
    with TestClient(app) as c:
        code = c.post("/pairing", headers={"X-API-Key": "adminkey"}).json()["code"]
        tok = c.post("/v1/pair", json={"code": code, "name": "phone"}).json()["token"]
        auth = {"Authorization": f"Bearer {tok}"}
        meter.record(workspace="main", task_id="t", model="x", kind="d", prompt=1000, completion=200, latency_ms=5, status="ok")
        mine = c.get("/v1/usage", headers=auth).json()
        assert mine["tokens_today"] == 1200 and mine["model_calls_today"] == 1 and mine["queue"] == {"waiting": 0, "running": 0}
        assert mine["paused_for_budget"] is False and "cost" not in str(mine).lower()          # users see tokens, not your spending
        owner = c.get("/usage", headers={"X-API-Key": "adminkey"}).json()
        assert owner["today"]["cost_usd"] > 0 and owner["queue"]["max_concurrent"] == 6 and owner["today"]["budget_usd"] == 5.0
        assert c.get("/usage", headers=auth).status_code in (401, 403)                         # a normal user cannot read the owner view
        assert c.get("/v1/usage").status_code == 401


def test_sign_up_is_open_unless_the_owner_closes_it(monkeypatch):
    from taskpilot.login import AuthSettings
    monkeypatch.delenv("TASKPILOT_SIGNUP", raising=False)
    assert AuthSettings.from_env().signup == "open"
    monkeypatch.setenv("TASKPILOT_SIGNUP", "closed")
    assert AuthSettings.from_env().signup == "closed"


def test_a_database_file_uses_wal_and_keeps_usage_across_restarts(tmp_path):
    path = str(tmp_path / "d.db")
    s = Store(path)
    assert s._db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"        # readers never wait for the writer
    UsageMeter(s, Limits()).record(workspace="w", task_id="t", model="m", kind="d", prompt=5, completion=5, latency_ms=1, status="ok")
    s._db.close()
    assert UsageMeter(Store(path), Limits()).today("w")["tokens"] == 10
