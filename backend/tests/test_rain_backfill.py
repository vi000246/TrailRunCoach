"""SP-299 follow-up (owner 2026-10-07): the one-time backfill of the rain over the last 12 months.
The weather cache of the activities was fetched before precipitation was asked, so the old
activities never get the 「要標成濕路嗎？」 hint. engine/rain_backfill.run asks those archive days
again — through route_weather's own Fetcher and cache, one call at a time, paced — and writes the
rain into activity_weather.json; api/rain_backfill.tick runs it once from the scheduler and marks
itself done in the setting `weather.rain_backfill` (resumes when interrupted). The HTTP is fake;
never in tests (conftest) nor in the demo."""
import asyncio
import datetime as dt
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.engine import rain_backfill as RB
from backend.engine import route_weather as RW
from backend.tests.test_rain_hint import _Archive, _Track, _day

TODAY = dt.date(2026, 10, 8)
RECENT = "2026-05-01"        # inside the 12 months
RECENT2 = "2026-05-02"
OLD = "2025-06-01"           # older than 12 months: left as it is


def _old_get(url, params, timeout):
    """The HOURLY of before SP-299: no precipitation."""
    n = len(str(params["latitude"]).split(","))
    out = [{"elevation": 300.0, "timezone": "Asia/Taipei", **_day(False, params["start_date"])} for _ in range(n)]
    return out if n > 1 else out[0]


def _tracks():
    return {"recent.fit": _Track(f"{RECENT}T07:10:00", 90 * 60),
            "recent2.fit": _Track(f"{RECENT2}T07:10:00", 90 * 60, lat=23.5, lon=120.9),
            "old.fit": _Track(f"{OLD}T07:10:00", 90 * 60),
            "nogps.fit": _Track(f"{RECENT}T18:00:00", 60 * 60, gps=False)}


def _seed(root):
    """The production state: activity_weather.json and its cache built before precipitation."""
    tracks = _tracks()
    RW.fill_activities(tracks, root, _old_get, today=TODAY)
    acts = RW.load_activity_weather(root)["activities"]
    assert all(v["rain_mm"] is None for v in acts.values() if v)
    return tracks


def _acts(root):
    return RW.load_activity_weather(root)["activities"]


def _cached(root, f, tracks):
    a, b, pt, keys = RW.activity_point(tracks[f])
    doc = json.loads(RW.cache_path(root / "weather", *keys[0]).read_text("utf-8"))
    return doc["points"][RW.point_key(pt)]


def test_backfill_fills_the_last_12_months_through_the_cache(tmp_path):
    tracks = _seed(tmp_path)
    before = _acts(tmp_path)
    get, naps = _Archive({8: 2.0, 9: 1.5}), []
    res = RB.run(tmp_path, tracks.get, get, TODAY, sleep=naps.append)
    # two (cell, day) groups in the window → two calls, one at a time, paced between them
    assert len(get.calls) == 2 and res["calls"] == 2
    assert naps == [RB.PACE_S]
    assert {c["start_date"] for c in get.calls} == {RECENT, RECENT2}
    acts = _acts(tmp_path)
    assert acts["recent.fit"]["rain_mm"] == 3.5 and acts["recent2.fit"]["rain_mm"] == 3.5
    assert acts["old.fit"]["rain_mm"] is None                     # > 12 months: never asked
    # only the rain is written: the heat exposure is unchanged
    for f in ("recent.fit", "recent2.fit"):
        assert {k: v for k, v in acts[f].items() if k != "rain_mm"} == \
               {k: v for k, v in before[f].items() if k != "rain_mm"}
    # the cache itself now carries the precipitation (a later build keeps the rain)
    assert RW.has_rain(_cached(tmp_path, "recent.fit", tracks))
    assert not RW.has_rain(_cached(tmp_path, "old.fit", tracks))
    assert res["complete"] and res["filled"] == 2 and res["activities"] == 2


def test_backfill_is_idempotent(tmp_path):
    tracks = _seed(tmp_path)
    RB.run(tmp_path, tracks.get, _Archive({8: 2.0}), TODAY, sleep=lambda s: None)
    get = _Archive({8: 9.0})
    res = RB.run(tmp_path, tracks.get, get, TODAY, sleep=lambda s: None)
    assert get.calls == [] and res["calls"] == 0 and res["complete"]
    assert _acts(tmp_path)["recent.fit"]["rain_mm"] == 2.0         # never rewritten
    # a rebuild of activity_weather.json from the cache keeps the rain (no call either)
    doc = RW.fill_activities(tracks, tmp_path, get, today=TODAY)
    assert get.calls == [] and doc["activities"]["recent.fit"]["rain_mm"] == 2.0


def test_an_interrupted_backfill_resumes_where_it_stopped(tmp_path):
    tracks = _seed(tmp_path)
    ok = _Archive({8: 2.0})

    def flaky(url, params, timeout):
        if params["start_date"] == RECENT2:
            raise OSError("network down")
        return ok(url, params, timeout)
    res = RB.run(tmp_path, tracks.get, flaky, TODAY, sleep=lambda s: None)
    assert res["failed"] == 1 and not res["complete"]
    assert _acts(tmp_path)["recent.fit"]["rain_mm"] == 2.0 and _acts(tmp_path)["recent2.fit"]["rain_mm"] is None
    get = _Archive({8: 2.0})
    res = RB.run(tmp_path, tracks.get, get, TODAY, sleep=lambda s: None)
    assert [c["start_date"] for c in get.calls] == [RECENT2]       # only what is still missing
    assert res["complete"] and _acts(tmp_path)["recent2.fit"]["rain_mm"] == 2.0


def test_no_track_or_no_weather_file_asks_nothing(tmp_path):
    tracks = _seed(tmp_path)
    get = _Archive({8: 2.0})
    res = RB.run(tmp_path, lambda f: None, get, TODAY, sleep=lambda s: None)
    assert get.calls == [] and res["no_track"] == 2 and res["complete"]
    res = RB.run(tmp_path / "empty", tracks.get, get, TODAY, sleep=lambda s: None)
    assert get.calls == [] and res["no_doc"] and not res["complete"]


def test_a_build_still_never_refetches_a_day_without_rain(tmp_path):
    """Only the backfill asks again: the routes build keeps reusing the cache as before."""
    tracks = _seed(tmp_path)
    get = _Archive({8: 2.0})
    RW.fill_activities(tracks, tmp_path, get, today=TODAY)
    assert get.calls == []


# ---- the one-time job (api/rain_backfill.tick) --------------------------------------------

@pytest.fixture
def job_env(monkeypatch, tmp_path):
    from backend.api import rain_backfill as RBA
    from backend.api import routes as RA
    monkeypatch.delenv(RBA.ENV_OFF, raising=False)
    monkeypatch.setattr(RA.BUILDER, "weather_get", lambda *a: pytest.fail("no real HTTP"))
    monkeypatch.setattr(RA.BUILDER, "running", lambda: False)
    monkeypatch.setattr(RBA, "_TASK", None)
    return RBA


async def _session(tmp_path):
    from backend.tests.test_sync_e2e import make_session
    return await make_session(tmp_path)


def _factory(s):
    class _Ctx:
        async def __aenter__(self):
            return s

        async def __aexit__(self, *a):
            return False
    return lambda: _Ctx()


def _go(coro):
    return asyncio.run(coro)


def test_job_runs_once_and_marks_itself_done(tmp_path, job_env):
    from backend.settings.repository import SettingsRepository
    RBA = job_env
    runs = []

    def fake_run(today):
        runs.append(today)
        return {"activities": 2, "calls": 2, "failed": 0, "skipped": 0, "filled": 2, "no_track": 0,
                "complete": True}

    async def go():
        s = await _session(tmp_path)
        f = _factory(s)
        now = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
        t = await RBA.tick(f, now=now, run=fake_run)
        assert t is not None
        st = await t
        assert st["done"] and st["attempts"] == 1 and st["calls_total"] == 2
        assert await SettingsRepository(s, 1).get(RBA.SETTING_KEY) == st
        # done: never again, whatever the time
        assert await RBA.tick(f, now=now + timedelta(days=30), run=fake_run) is None
    _go(go())
    assert runs == [dt.date(2026, 10, 8)]


def test_job_retries_after_a_failure_and_gives_up_in_the_end(tmp_path, job_env):
    RBA = job_env
    res = {"activities": 2, "calls": 2, "failed": 1, "skipped": 0, "filled": 1, "no_track": 0, "complete": False}

    async def go():
        s = await _session(tmp_path)
        f = _factory(s)
        now = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
        st = await (await RBA.tick(f, now=now, run=lambda d: res))
        assert not st["done"] and st["attempts"] == 1
        # not again before RETRY_S
        assert await RBA.tick(f, now=now + timedelta(seconds=RBA.RETRY_S - 60), run=lambda d: res) is None
        for k in range(2, RBA.MAX_ATTEMPTS + 1):
            now += timedelta(seconds=RBA.RETRY_S + 1)
            st = await (await RBA.tick(f, now=now, run=lambda d: res))
            assert st["attempts"] == k
        assert st["done"] and st["gave_up"]
    _go(go())


def test_job_waits_for_the_weather_file_without_counting_an_attempt(tmp_path, job_env):
    RBA = job_env

    async def go():
        s = await _session(tmp_path)
        f = _factory(s)
        now = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
        st = await (await RBA.tick(f, now=now, run=lambda d: {"no_doc": True, "complete": False}))
        assert not st["done"] and st.get("attempts", 0) == 0 and st["waiting"]
    _go(go())


def test_job_is_off_in_tests_in_the_demo_without_weather_and_while_a_build_runs(tmp_path, job_env, monkeypatch):
    from backend import tenancy
    from backend.api import routes as RA
    RBA = job_env
    boom = lambda d: pytest.fail("must not run")

    async def go():
        s = await _session(tmp_path)
        f = _factory(s)
        monkeypatch.setenv(RBA.ENV_OFF, "1")
        assert await RBA.tick(f, run=boom) is None
        monkeypatch.delenv(RBA.ENV_OFF)
        monkeypatch.setattr(tenancy, "demo_mode", lambda: True)
        assert await RBA.tick(f, run=boom) is None
        monkeypatch.setattr(tenancy, "demo_mode", lambda: False)
        monkeypatch.setattr(RA.BUILDER, "weather_get", None)          # WKO5COACH_ROUTES_WEATHER=0
        assert await RBA.tick(f, run=boom) is None
        monkeypatch.setattr(RA.BUILDER, "weather_get", lambda *a: None)
        monkeypatch.setattr(RA.BUILDER, "running", lambda: True)       # the routes build writes the file
        assert await RBA.tick(f, run=boom) is None
    _go(go())


def test_tests_never_run_the_job():
    import os
    from backend.api import rain_backfill as RBA
    assert os.getenv(RBA.ENV_OFF)                                     # conftest turns it off


def test_scheduler_loop_starts_it_only_after_the_start_delay(monkeypatch):
    from backend.api import backup as backup_api
    from backend.api import rain_backfill as RBA
    from backend.sync import check, scheduler
    seen = []

    async def rec(*a, **k):
        seen.append("rain")
        return None

    async def nothing(*a, **k):
        return []
    monkeypatch.setattr(scheduler, "tick", nothing)
    monkeypatch.setattr(backup_api, "auto_tick", nothing)
    monkeypatch.setattr(check, "weekly_tick", nothing)
    monkeypatch.setattr(RBA, "tick", rec)
    for delay, runs in ((10 ** 6, False), (0, True)):
        seen.clear()
        monkeypatch.setattr(RBA, "STARTUP_DELAY_S", delay)

        async def go():
            t = asyncio.create_task(scheduler.loop(object(), interval=0.01))
            await asyncio.sleep(0.08)
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
        _go(go())
        assert bool(seen) is runs


def test_setting_key_is_known_and_validated():
    from backend.settings.repository import DEFAULTS, validate
    assert DEFAULTS["weather.rain_backfill"] is None
    validate("weather.rain_backfill", {"done": True})
    validate("weather.rain_backfill", None)
    with pytest.raises(ValueError):
        validate("weather.rain_backfill", "done")
