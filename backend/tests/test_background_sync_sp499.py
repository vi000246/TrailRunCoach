"""SP-499: new activities synced in the background (sync/background.py) and what the pages read
computed before a page is opened — the tenants staggered, one sync at a time for the server, the
COROS limits respected with a back-off, the job queue's backlog respected; and the charts drawn
lately drawn again on the new data (api/prerender.py), so a page finds them in the cache.

No network: the sync runs are fakes (a `start` that records, or a task that waits on an event)."""
from __future__ import annotations

import asyncio
import datetime as dt
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend import tenancy
from backend.sync import background as BG

TPE = timezone(timedelta(hours=8))
NOON = datetime(2026, 10, 12, 12, 0, tzinfo=TPE).timestamp()        # not in the quiet hours


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    BG.reset()
    monkeypatch.delenv(BG.ENV, raising=False)
    yield
    BG.reset()


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Multi-user mode in tmp (as test_accounts_sp384): the owner + users u2.. with their own DBs."""
    from backend import accounts
    from backend.accounts import store, tenants as TN
    root = tmp_path / "server"
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(root))
    monkeypatch.setenv(accounts.ENV_ENABLED, "1")
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    store.migrate_owner()
    return root


def _users(n: int, plan: str = "subscriber") -> list[str]:
    from backend.accounts import store
    out = []
    for i in range(n):
        u = store.create_user(email=f"u{i}@x.tw")
        if plan:
            store.set_plan(u.id, plan)
        out.append(f"u{u.id}")
    return out


async def _prepare(settings: dict | None = None) -> None:
    """Every tenant's DB, its time zone (Taipei) and `settings`."""
    from backend.accounts import tenants as TN
    from backend.db import database
    from backend.settings.repository import SettingsRepository
    for t in TN.sync_tenants():
        with tenancy.use(t):
            await TN.ensure_ready(t)
            if t.id == "owner":
                await database.init_db()           # the owner's (made at start-up in the app)
            async with database.AsyncSessionLocal() as db:
                repo = SettingsRepository(db, 1)
                await repo.set("athlete.timezone", "Asia/Taipei")
                await repo.set("sync.background.enabled", True)
                for k, v in (settings or {}).get(t.id, {}).items():
                    await repo.set(k, v)
                await db.commit()


@pytest.fixture
def ready(monkeypatch):
    """Every tenant's COROS is ready and was never synced (the network side is the test's)."""
    from backend.sync import runner

    async def auto_plan(db, athlete_id=1):
        return list(ready.sources), {}

    async def last_sync_at(db, source, athlete_id=1):
        return ready.last.get(tenancy.current().id)
    ready.sources = ["coros"]
    ready.last = {}
    monkeypatch.setattr(runner, "auto_plan", auto_plan)
    monkeypatch.setattr(runner, "last_sync_at", last_sync_at)
    return ready


def _recorder():
    started = []

    def start(src, aid, trigger, sf):
        started.append((tenancy.current().id, src, trigger))
        return object()                     # not a task: never "running"
    return started, start


def _visits(monkeypatch, **days_ago):
    from backend.accounts import tenants as TN
    now = NOON
    monkeypatch.setattr(TN, "last_visits", lambda: {tid: now - d * 86400 for tid, d in days_ago.items()})


def _first_due(tid: str, every: int, after: float) -> float:
    """The first minute after `after` when tenant `tid` starts a new interval."""
    s = BG.slot_of(after, tid, every)
    return BG.slot_start(s + 1, tid, every) + 1


# ---------------------------------------------------------------------------- the schedule


def test_the_interval_follows_how_recently_the_account_was_used():
    now = NOON
    assert BG.every_min("owner", None, now) == BG.OWNER_EVERY_MIN == 30
    assert BG.every_min("u2", now - 3600, now) == 30
    assert BG.every_min("u2", now - 10 * 86400, now) == 120
    assert BG.every_min("u2", now - 20 * 86400, now) == 360
    assert BG.every_min("u2", now - 40 * 86400, now) is None          # the daily sync only
    assert BG.every_min("u2", None, now) is None


def test_the_tenants_are_spread_over_their_interval_and_keep_their_minute():
    phases = [BG.phase_min(f"u{i}", 30) for i in range(2, 62)]
    assert set(phases) <= set(range(30)) and len(set(phases)) >= 20       # 60 users: ≥ 20 different minutes
    assert max(phases.count(p) for p in set(phases)) <= 6                 # never a crowd on one minute
    assert BG.phase_min("u7", 30) == BG.phase_min("u7", 30)
    # one look per interval: the slot number moves once every `every` minutes, at the tenant's minute
    t0 = BG.slot_start(BG.slot_of(NOON, "u7", 30), "u7", 30)
    assert BG.slot_of(t0 + 29 * 60, "u7", 30) == BG.slot_of(t0, "u7", 30)
    assert BG.slot_of(t0 + 30 * 60, "u7", 30) == BG.slot_of(t0, "u7", 30) + 1


def test_after_a_start_nobody_is_due_at_once_then_each_at_its_own_minute(server, monkeypatch, ready):
    ids = _users(4)
    _visits(monkeypatch, **{t: 1 for t in ids})
    asyncio.run(_prepare())
    started, start = _recorder()
    first = asyncio.run(BG.tick_all(now=NOON, start=start))
    assert set(first.values()) == {"scheduled"} and started == []        # a restart starts nothing
    # walk minute by minute through one interval: every tenant exactly once, at its own minute
    when = {}
    for m in range(1, 31):
        now = NOON + m * 60
        out = asyncio.run(BG.tick_all(now=now, start=start))
        for tid, what in out.items():
            if what == "started":
                when[tid] = BG.slot_of(now, tid, 30)
                assert BG.slot_start(BG.slot_of(now, tid, 30), tid, 30) <= now
    assert sorted(when) == sorted(["owner", *ids])
    assert [s[2] for s in started] == ["background"] * 5
    minutes = {}
    for tid, _src, _trig in started:
        minutes.setdefault(tid, 0)
        minutes[tid] += 1
    assert set(minutes.values()) == {1}                                  # once per interval each


def test_one_start_a_minute_the_most_overdue_first(server, monkeypatch, ready):
    ids = _users(3)
    _visits(monkeypatch, **{t: 1 for t in ids})
    asyncio.run(_prepare())
    started, start = _recorder()
    asyncio.run(BG.tick_all(now=NOON, start=start))
    later = NOON + 31 * 60                                               # every tenant due now
    out = asyncio.run(BG.tick_all(now=later, start=start))
    assert list(out.values()).count("started") == BG.STARTS_PER_TICK == 1
    assert list(out.values()).count("next_minute") == 3
    order = sorted(["owner", *ids], key=lambda t: BG.slot_start(BG.slot_of(later, t, 30), t, 30))
    assert started[0][0] == order[0]
    for k in range(1, 4):                                                # then one each next minute
        out = asyncio.run(BG.tick_all(now=later + k * 60, start=start))
        assert list(out.values()).count("started") == 1
    assert [s[0] for s in started] == order


def test_at_most_one_background_sync_runs_on_the_server(server, monkeypatch, ready):
    ids = _users(1)
    _visits(monkeypatch, **{ids[0]: 1})
    asyncio.run(_prepare())

    async def go():
        gate = asyncio.Event()
        started = []

        def start(src, aid, trigger, sf):
            started.append(tenancy.current().id)

            async def run():
                await gate.wait()
                return {"status": "complete"}
            return asyncio.get_running_loop().create_task(run())
        await BG.tick_all(now=NOON, start=start)
        later = NOON + 31 * 60
        a = await BG.tick_all(now=later, start=start)
        b = await BG.tick_all(now=later + 60, start=start)               # the first still runs
        gate.set()
        await asyncio.sleep(0.05)
        c = await BG.tick_all(now=later + 120, start=start)
        return started, a, b, c
    started, a, b, c = asyncio.run(go())
    assert list(a.values()).count("started") == 1
    assert "running" in b.values() and "started" not in b.values()
    assert "started" in c.values() and len(started) == 2


def test_a_busy_job_queue_holds_the_background_syncs_back(server, monkeypatch, ready):
    from backend import jobs
    asyncio.run(_prepare())
    started, start = _recorder()
    asyncio.run(BG.tick_all(now=NOON, start=start))
    gate = threading.Event()
    jobs.submit(lambda j: gate.wait(10), cls="full", priority="warm", label="hold the place")
    waiting = [jobs.submit(lambda j: None, cls="full", priority="warm", label=f"w{i}") for i in range(BG.BACKLOG_MAX)]
    try:
        due = _first_due("owner", 30, NOON)
        assert asyncio.run(BG.tick_all(now=due, start=start)) == {"owner": "queue_busy"}
        assert started == []
    finally:
        gate.set()
        for w in waiting:
            w.join(5)
    # not skipped: tried again the next minute, once the queue is free
    assert asyncio.run(BG.tick_all(now=due + 60, start=start)) == {"owner": "started"}


def test_no_background_sync_in_the_first_minutes_after_a_start(server, monkeypatch, ready):
    asyncio.run(_prepare())
    started, start = _recorder()
    BG.started()
    asyncio.run(BG.tick_all(now=NOON, start=start))
    due = _first_due("owner", 30, NOON)
    assert asyncio.run(BG.tick_all(now=due, start=start)) == {"owner": "startup"}
    monkeypatch.setattr(BG, "clock", lambda: time.monotonic() + BG.STARTUP_DELAY_S + 1)
    assert asyncio.run(BG.tick_all(now=due + 60, start=start)) == {"owner": "started"}


# ---------------------------------------------------------------------------- the services' limits


def test_a_paused_coros_account_is_not_looked_at_and_a_cooldown_waits(server, monkeypatch, ready):
    from backend.sync import coros_limits as CL
    asyncio.run(_prepare())
    started, start = _recorder()
    asyncio.run(BG.tick_all(now=NOON, start=start))
    due = _first_due("owner", 30, NOON)
    with tenancy.use(tenancy.owner()):
        CL.pause("HTTP 429")
    assert asyncio.run(BG.tick_all(now=due, start=start)) == {"owner": "paused"}
    with tenancy.use(tenancy.owner()):
        CL.unpause()
    due = _first_due("owner", 30, due)
    CL._STATE["cooldown_until"] = CL.clock() + 600                      # server-wide: too many 429s
    assert asyncio.run(BG.tick_all(now=due, start=start)) == {"owner": "coros_cooldown"}
    CL._STATE["cooldown_until"] = 0.0
    assert asyncio.run(BG.tick_all(now=due + 60, start=start)) == {"owner": "started"}   # the same look
    assert started == [("owner", "coros", "background")]


def test_a_blocked_run_backs_off_and_a_good_one_ends_the_back_off(server, monkeypatch, ready):
    asyncio.run(_prepare())
    results = [{"status": "error", "error": "COROS_RATE_LIMITED"}, {"status": "error", "error": "COROS_COOLDOWN"},
               {"status": "complete", "total_downloaded": 0}]

    async def go():
        log = []

        def start(src, aid, trigger, sf):
            res = results[min(len(log), len(results) - 1)]
            log.append(res)

            async def run():
                return res
            return asyncio.get_running_loop().create_task(run())
        out = [await BG.tick_all(now=NOON, start=start)]
        t = NOON
        for _ in range(12):                    # 12 intervals of 30 min = 6 h
            t = _first_due("owner", 30, t)
            out.append(await BG.tick_all(now=t, start=start))
            await asyncio.sleep(0.01)
        return out, log
    out, log = asyncio.run(go())
    seq = [o.get("owner") for o in out[1:]]
    # blocked -> the next look is skipped (2 intervals) ; blocked again -> 4 intervals ; then it works
    assert seq[:7] == ["started", "backoff", "started", "backoff", "backoff", "backoff", "started"]
    assert seq[7:] == ["started"] * 5
    assert BG._STATE["owner"]["fails"] == 0
    assert BG.backoff_s(1, 30) == 60 * 60 and BG.backoff_s(3, 30) == 240 * 60
    assert BG.backoff_s(20, 30) == BG.BACKOFF_MAX_MIN * 60 and BG.backoff_s(1, 360) == 720 * 60


def test_garmin_is_never_synced_in_the_background(server, monkeypatch, ready):
    ready.sources = ["garmin"]
    asyncio.run(_prepare())
    started, start = _recorder()
    asyncio.run(BG.tick_all(now=NOON, start=start))
    assert asyncio.run(BG.tick_all(now=_first_due("owner", 30, NOON), start=start)) == {"owner": "garmin_daily"}
    assert started == []


def test_the_background_run_goes_through_the_coros_limits(monkeypatch):
    """runner.start_background with trigger "background": an automatic trigger (merged with a page
    open of the last 5 min) whose stream takes COROS's sync place — not a scheduled one, so a
    paused account stays paused."""
    from backend.sync import coros_limits as CL, runner
    assert "background" in runner.AUTOMATIC
    seen = {}
    real = CL.sync_slot

    def slot(scheduled=False):
        seen["scheduled"] = scheduled
        return real(scheduled=scheduled)
    monkeypatch.setattr(CL, "sync_slot", slot)
    with tenancy.use(tenancy.owner()):
        CL.pause("HTTP 429")

        async def go():
            class DB:
                async def rollback(self):
                    pass

                async def commit(self):
                    pass
            evs = [ev async for ev in runner.stream(DB(), "coros", trigger="background", remember=False,
                                                     client=lambda db: _never())]
            return evs
        evs = asyncio.run(go())
    assert seen == {"scheduled": False}
    assert evs[-1]["error"] == "COROS_PAUSED"


async def _never():
    if False:
        yield {}


# ---------------------------------------------------------------------------- who and when


def test_who_gets_it_the_switch_the_plan_and_the_setting(server, monkeypatch, ready):
    subs, free = _users(1), _users(1, plan=None)
    _visits(monkeypatch, **{subs[0]: 1, free[0]: 1})
    asyncio.run(_prepare({"owner": {"sync.background.enabled": False}}))
    started, start = _recorder()
    asyncio.run(BG.tick_all(now=NOON, start=start))
    out = {}
    for m in range(1, 32):
        for k, v in asyncio.run(BG.tick_all(now=NOON + m * 60, start=start)).items():
            out[k] = v
    assert out == {"owner": "off", subs[0]: "started", free[0]: "not_allowed"}    # the free tier: nightly
    monkeypatch.setenv(BG.ENV, "owner")
    BG.reset()
    assert set(asyncio.run(BG.tick_all(now=NOON, start=start))) == {"owner"}
    monkeypatch.setenv(BG.ENV, "off")
    assert asyncio.run(BG.tick_all(now=NOON + 3600, start=start)) == {}
    with tenancy.use(tenancy.owner()):
        assert BG.describe()["why"] == "off"
    monkeypatch.setenv(BG.ENV, "on")
    with tenancy.use(tenancy.owner()):
        assert BG.describe() == {"available": True, "why": None, "every_min": 30, "quiet": [1, 5]}


def test_an_account_not_used_lately_is_looked_at_less_or_not_at_all(server, monkeypatch, ready):
    a, b, c = _users(3)
    _visits(monkeypatch, **{a: 10, b: 20, c: 45})
    asyncio.run(_prepare())
    started, start = _recorder()
    asyncio.run(BG.tick_all(now=NOON, start=start))
    assert BG._STATE[a]["every"] == 120 and BG._STATE[b]["every"] == 360 and c not in BG._STATE
    for m in range(1, 6 * 60 + 1):
        asyncio.run(BG.tick_all(now=NOON + m * 60, start=start))
    n = {t: sum(1 for s in started if s[0] == t) for t in ("owner", a, b, c)}
    assert n == {"owner": 12, a: 3, b: 1, c: 0}


def test_quiet_hours_and_a_fresh_sync_skip_the_look(server, monkeypatch, ready):
    asyncio.run(_prepare())
    started, start = _recorder()
    night = datetime(2026, 10, 12, 2, 0, tzinfo=TPE).timestamp()
    asyncio.run(BG.tick_all(now=night, start=start))
    assert asyncio.run(BG.tick_all(now=_first_due("owner", 30, night), start=start)) == {"owner": "quiet"}
    BG.reset()
    asyncio.run(BG.tick_all(now=NOON, start=start))
    due = _first_due("owner", 30, NOON)
    ready.last["owner"] = datetime.fromtimestamp(due - 600, timezone.utc)      # a page open synced it
    assert asyncio.run(BG.tick_all(now=due, start=start)) == {"owner": "fresh"}
    assert started == []


def test_the_self_rating_passes_after_a_look_with_nothing_new_are_rationed(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(BG, "wall", lambda: t[0])
    with tenancy.use(tenancy.owner()):
        assert BG.feel_due("manual", {"downloaded": 0}) is True
        assert BG.feel_due("background", {"downloaded": 0}) is True
        assert BG.feel_due("background", {"downloaded": 0}) is False         # the next look, 30 min later
        assert BG.feel_due("background", {"downloaded": 1}) is True          # something new: always
        t[0] += BG.FEEL_EVERY_S
        assert BG.feel_due("background", {"downloaded": 0}) is True


def test_a_background_sync_does_not_make_an_account_active(monkeypatch, tmp_path):
    from backend.sync import events
    monkeypatch.setattr(events, "PATH", tmp_path / "ev.jsonl")
    events.log("coros", "sync_ok", tenant="u2", trigger="background")
    events.log("coros", "sync_ok", tenant="u3", trigger="open")
    assert set(events.last_syncs()) == {"u3"}


# ---------------------------------------------------------------------------- computed before the page


def test_after_a_background_sync_the_pages_data_is_queued_low(monkeypatch):
    from backend.api import wko5views as WV
    from backend.sync import runner
    calls = []
    monkeypatch.setattr(WV, "warm_up", lambda reason="startup": calls.append(reason))

    class DB:
        pass

    async def no_tz(db, aid):
        return None
    from backend.engine import localtime
    monkeypatch.setattr(localtime, "refresh_from_fits", no_tz)
    asyncio.run(runner.after_import(DB(), "coros", {"status": "ok", "downloaded": 1, "trigger": "background"}))
    asyncio.run(runner.after_import(DB(), "coros", {"status": "ok", "downloaded": 1, "trigger": "manual"}))
    assert calls == ["sync-bg-coros", "sync-coros"]
    # the warm-up (Dataset → overview status → the 課表's inputs) of a background run: `warm`, below
    # every page someone waits for and a sync someone pressed; the classification's hot tier too
    assert WV._priority("sync-bg-coros") == "warm" and WV._hot_priority("sync-bg-coros") == "warm"
    assert WV._priority("sync-coros") == "sync_hot"


def test_the_background_warm_up_computes_what_the_overview_and_the_plan_read(monkeypatch):
    """warm_up("sync-bg-…") through the queue: the Dataset, the overview status and the 課表's plan
    inputs (the session matching), in that order, as a `full` job at `warm`; then its low-priority
    thread (classification, the charts) — so a page opened afterwards reads them done."""
    from backend import jobs
    from backend.api import overview as OV, plan_sessions as PSA, wko5views as WV
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    order, low = [], []

    class DS:
        today = 20000.0
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: order.append("dataset") or DS())
    monkeypatch.setattr(OV, "_status", lambda ds, day: order.append("status"))
    monkeypatch.setattr(OV.O, "day_to_date", lambda d: d)
    monkeypatch.setattr(PSA, "_compute_inputs", lambda *a, **k: order.append("inputs"))
    monkeypatch.setattr(WV, "_warm_chart_code", lambda ds: None)
    monkeypatch.setattr(WV, "_start_low_priority", lambda reason: low.append(reason))
    seen = {}
    real = jobs.submit

    def submit(fn, **kw):
        seen.update(kw)
        return real(fn, **kw)
    monkeypatch.setattr(jobs, "submit", submit)
    with tenancy.use(tenancy.owner()):
        t = WV.warm_up("sync-bg-coros")
        t.join(10)
    assert seen["priority"] == "warm" and seen["cls"] == "full"
    assert order == ["dataset", "status", "inputs"] and low == ["sync-bg-coros"]


# ---- the charts drawn lately (api/prerender.py), on the chart endpoint (FakeDataset, as SP-336's tests)

from backend.engine.wko5expr.render_cache import RenderCache          # noqa: E402
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout          # noqa: E402

TODAY = dt.date(2026, 9, 30)
VIEW = {"source": "custom", "name": "v", "dashboards": [{"title": "d", "charts": [
    {"kind": "athlete", "id": "load", "title": "TSS", "series": [{"name": "tss", "expression": "tss"}]},
    {"kind": "athlete", "id": "dist", "title": "km", "series": [{"name": "km", "expression": "distance"}]}]}]}


def _ds(n_days, extra=()):
    from backend.engine.wko5expr.config import EngineConfig
    ws = [FakeWorkout(dt.datetime.combine(TODAY - dt.timedelta(days=k), dt.time(7)),
                      metrics={"tss": 50.0, "distance": 10.0, "duration": 3600.0}) for k in range(1, n_days)]
    ws += [FakeWorkout(dt.datetime.combine(d, dt.time(18)), metrics={"tss": 99.0, "distance": 5.0,
                                                                       "duration": 1800.0}) for d in extra]
    d = FakeDataset(ws, TODAY)
    d.config = EngineConfig()
    d.dir = None
    for w in d.workouts:
        w.entry.file = f"fake/{w.entry.start:%Y%m%d%H%M}.fit"
    return d


@pytest.fixture
def charts(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import wko5views as WV
    holder = {"ds": _ds(200)}
    st = {"calls": 0}
    real = WV._render

    def render(*a, **k):
        st["calls"] += 1
        return real(*a, **k)
    monkeypatch.setattr(WV, "_render", render)
    monkeypatch.setattr(WV, "_dataset", lambda parity=None, source=None: holder["ds"])
    monkeypatch.setattr(WV, "_view", lambda name, parity=None: VIEW)
    monkeypatch.setattr(WV, "RENDER_CACHE", RenderCache(tmp_path / "render"))
    app = FastAPI()
    app.include_router(WV.router)
    return TestClient(app), holder, st


def _chart(client, c=0, days=90, extra=""):
    q = f"begin={TODAY - dt.timedelta(days=days - 1)}&end={TODAY}&stale=1{extra}"
    r = client.get(f"/api/v1/app/views/v/dashboards/0/charts/{c}?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_charts_drawn_lately_are_drawn_again_before_the_page_asks(charts):
    from backend.api import prerender as PR
    client, holder, st = charts
    with tenancy.use(tenancy.owner()):
        _chart(client, 0)
        _chart(client, 1, days=28, extra="&sports=run")
        old_range = client.get(f"/api/v1/app/views/v/dashboards/0/charts/0?begin={TODAY - dt.timedelta(days=200)}"
                               f"&end={TODAY - dt.timedelta(days=150)}&stale=1")
        assert old_range.status_code == 200
        assert len(PR.recent_slots()) == 2                  # a range in the past never changes: no replay
        holder["ds"] = _ds(200, extra=[TODAY - dt.timedelta(days=2)])       # a background sync brought one
        n = st["calls"]
        assert PR.run() == {"drawn": 2, "failed": 0}
        assert st["calls"] == n + 2
        a = _chart(client, 0)
        b = _chart(client, 1, days=28, extra="&sports=run")
    assert st["calls"] == n + 2                                          # the page asked: both in the cache
    assert "stale" not in a and "stale" not in b
    assert len(a["series"][0]["data"]["points"]) == 90


def test_the_pre_render_is_a_low_queue_job_after_the_classification(charts, monkeypatch):
    from backend import jobs
    from backend.api import prerender as PR
    waited = []

    class Job:
        def wait_for(self, tier, timeout=None):
            waited.append(tier)
    with tenancy.use(tenancy.owner()):
        job = PR.after_sync(Job())
        job.join(10)
    assert waited == ["hot"]
    assert job.cls == "single" and job.priority == jobs.PRIORITIES["cold"] and job.state == "done"
    # a demo tenant / a plan whose charts are counted per day: never drawn on its behalf
    from backend import membership as MS
    monkeypatch.setattr(MS, "quota_for", lambda key, m=None: object())
    with tenancy.use(tenancy.owner()):
        assert PR.after_sync(Job()) is None


def test_the_settings_page_has_the_switch(server, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import sync as SA
    asyncio.run(_prepare())
    app = FastAPI()
    app.include_router(SA.router)
    c = TestClient(app)
    with tenancy.use(tenancy.owner()):
        s = c.get("/api/v1/sync/settings").json()
        assert s["background_sync"] is True and s["background"]["available"] is True
        r = c.put("/api/v1/sync/settings", json={"background_sync": False})
        assert r.status_code == 200 and r.json()["background_sync"] is False
