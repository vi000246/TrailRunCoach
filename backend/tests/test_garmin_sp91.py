"""SP-91: the Garmin trial — connect (MFA in two steps, disclaimer, sealed token, 記住密碼), sync
(list → original ZIP → .fit → the shared import, dedup with COROS / TP, a first sync spread over
days, a bad ZIP is one error), and the limits (no retry on 429 / 403, daily counts incl. the
automatic re-login, one sync and a 2-s gap server-wide, the switch, the breaker). The
garminconnect package is a fake (backend/tests/garmin_fakes.py): no network."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend import tenancy
from backend.db.models import AthleteSettings, SyncState, WorkoutFile
from backend.settings.repository import SettingsRepository
from backend.sync import dedup, garmin_client as GC, garmin_limits as GL, runner
from backend.tests.fit_builder import build_run
from backend.tests.garmin_fakes import FakeGarminServer, activity, zip_of
from backend.tests.test_sync_e2e import START, collect, make_session, run

EMAIL, PW = "runner@example.com", "hunter2-pw"


@pytest.fixture
def garmin(monkeypatch):
    server = FakeGarminServer(accounts={EMAIL: {"password": PW}})
    monkeypatch.setattr(GC, "_LIB", server.module())
    return server


def _acts(server, *days, power=0):
    for i, d in enumerate(days):
        aid = 9001 + i
        start = START + timedelta(days=d)
        server.activities.append(activity(aid, start))
        server.zips[aid] = zip_of(build_run(start, power=power), f"{aid}_ACTIVITY.fit")
    server.activities.sort(key=lambda a: a["startTimeGMT"], reverse=True)       # newest first


def _tenant(tmp_path, i):
    d = tmp_path / f"u{i}"
    return tenancy.Tenant(id=f"u{i}", kind=tenancy.USER, root=d, shared=d)


async def _sync(s, trigger="manual", since=None):
    return await collect(runner.stream(s, "garmin", 1, since=since, trigger=trigger))


def _done(evs):
    return [e for e in evs if e.get("status") == "downloaded"]


def _err(evs):
    return next((e for e in evs if e.get("status") == "error" and "activity_id" not in e), None)


# ---------------------------------------------------------------------------------------- B. sync

def test_list_download_the_original_zip_take_the_fit_out_and_import(tmp_path, garmin):
    from backend.sync import storage
    _acts(garmin, 0, 1, power=240)

    async def go():
        s = await make_session(tmp_path)
        assert (await GC.login(EMAIL, PW, s, 1))["authenticated"]
        evs = await _sync(s)
        assert evs[0]["status"] == "started" and evs[0]["since"] == GC.FIRST_SYNC_DAY.isoformat()
        assert len(_done(evs)) == 2 and evs[-1]["status"] == "complete" and evs[-1]["errors"] == []
        rows = (await s.execute(select(WorkoutFile).order_by(WorkoutFile.start_time_utc))).scalars().all()
        assert [(r.source, r.garmin_activity_id) for r in rows] == [("garmin", "9001"), ("garmin", "9002")]
        assert rows[0].workout_date == date(2026, 9, 2)              # Taipei local date
        p = storage.confined(rows[0].file_path, "garmin")             # under fit/garmin/
        assert p.parent.name == "2026" and p.name.startswith("9001_2026-09-02_") and p.suffix == ".fit"
        assert p.read_bytes()[8:12] == b".FIT"                       # the .fit, not the ZIP
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.garmin_last_sync_at is not None
        # the next run: from the cursor minus the overlap, nothing downloaded twice
        evs2 = await _sync(s)
        since = datetime.now(timezone.utc).date() - timedelta(days=GC.CURSOR_OVERLAP_DAYS)
        assert evs2[0]["since"] == since.isoformat() and not _done(evs2)
        assert garmin.count("download") == 2
        res = await SettingsRepository(s, 1).get("sync.garmin.last_result")
        assert res["status"] == "ok" and res["trigger"] == "manual"
    run(go())


def test_a_bad_zip_is_one_error_and_the_rest_of_the_batch_is_imported(tmp_path, garmin):
    from backend.sync import failures as FL
    _acts(garmin, 0, 1, 2, 3)
    garmin.zips[9002] = b"PK\x03\x04 this is not a zip"
    garmin.zips[9003] = zip_of(b"<gpx/>", "9003.gpx")                    # an uploaded GPX: no .fit

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1)
        evs = await _sync(s)
        errs = {e["activity_id"]: e["error"] for e in evs if e.get("status") == "error"}
        assert errs == {"9002": "GARMIN_BAD_ZIP", "9003": "GARMIN_NO_FIT"}
        assert sorted(e["activity_id"] for e in _done(evs)) == ["9001", "9004"]
        assert evs[-1]["status"] == "complete" and len(evs[-1]["errors"]) == 2
        assert {r.provider_id for r in await FL.due(s, 1, "garmin")} == {"9002", "9003"}
        res = await SettingsRepository(s, 1).get("sync.garmin.last_result")
        assert res["status"] == "partial" and res["downloaded"] == 2
    run(go())


def test_extract_fit_takes_a_bare_fit_and_refuses_what_is_not_one():
    fit = build_run(START)
    assert GC.extract_fit(fit) == fit
    assert GC.extract_fit(zip_of(fit)) == fit
    for bad, exc in ((b"", GC.GarminNoFit), (b"junk" * 10, GC.GarminBadFile), (zip_of(b"x", "a.tcx"), GC.GarminNoFit)):
        with pytest.raises(exc):
            GC.extract_fit(bad)


def test_the_same_activity_from_coros_is_one_activity(tmp_path, garmin):
    """dedup.py by start time (±2 min): no extra activity; the 資料來源 decides which row counts, and
    with Garmin in use neither COROS nor TP rows are counted (one source at a time)."""
    from backend.files.file_service import _import_one_file
    from backend.sync import primary as P, storage
    garmin.activities.append(activity(9001, START + timedelta(seconds=30)))
    garmin.zips[9001] = zip_of(build_run(START + timedelta(seconds=30), power=250))

    async def go():
        s = await make_session(tmp_path)
        c = storage.year_dir("coros", 2026) / "C1_2026-09-02_run.fit"
        c.write_bytes(build_run(START, power=250))
        coros = await _import_one_file(s, 1, c, source="coros", coros_activity_id="C1")
        t = storage.year_dir("tp", 2026) / "tp_2026_09_10_77.fit"
        t.write_bytes(build_run(START + timedelta(days=8)))              # a TP-only activity
        await _import_one_file(s, 1, t, source="trainingpeaks", tp_workout_id=77)
        await s.commit()
        await GC.login(EMAIL, PW, s, 1)
        await _sync(s)
        g = (await s.execute(select(WorkoutFile).where(WorkoutFile.source == "garmin"))).scalar_one()
        assert g.duplicate_of == coros.id and coros.duplicate_of is None      # COROS in use

        async def counted():
            return sorted((await s.execute(select(WorkoutFile.source).where(await dedup.in_use(s, 1))))
                          .scalars().all())
        assert await counted() == ["coros"]
        await SettingsRepository(s, 1).set(P.SETTING_KEY, "garmin")
        await dedup.rebuild(s, 1)
        assert coros.duplicate_of == g.id and g.duplicate_of is None
        assert await counted() == ["garmin"]                                  # not COROS, not TP
        assert len((await s.execute(select(WorkoutFile))).scalars().all()) == 3   # nothing deleted
    run(go())


def test_garmin_is_the_second_data_source_and_the_one_source_rule_holds():
    from backend.settings.repository import validate
    from backend.sync import primary as P, workout_targets as WT
    assert P.DB_SOURCES == ("coros", "garmin", "trainingpeaks")             # COROS → Garmin → TP
    assert P.folder("garmin") == "garmin" and P.other_folders("garmin") == ("coros", "tp")
    assert P.other_folders("coros") == ("garmin", "tp") and P.LABELS["garmin"] == "Garmin"
    validate("sync.primary_source", "garmin")
    with pytest.raises(ValueError):
        validate("sync.primary_source", "polar")
    assert "garmin" not in WT.enabled_ids()                                   # no workout push
    assert WT.get("garmin").enabled is False


def test_hr_zones_give_max_hr_and_lthr_stored_as_coros_does(tmp_path, garmin):
    _acts(garmin, 0)
    garmin.zones = [{"sport": "DEFAULT", "trainingMethod": "HR_RESERVE", "maxHeartRateUsed": 190,
                     "restingHeartRateUsed": 50, "lactateThresholdHeartRateUsed": 165},
                    {"sport": "RUNNING", "trainingMethod": "LACTATE_THRESHOLD", "maxHeartRateUsed": 192,
                     "restingHeartRateUsed": 49, "lactateThresholdHeartRateUsed": 168}]

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1)
        await _sync(s)
        prof = await SettingsRepository(s, 1).get("athlete.garmin_profile")
        assert (prof["max_hr"], prof["rest_hr"], prof["lthr"], prof["source"]) == (192, 49, 168, "garmin")
        today = datetime.now(timezone.utc).date()
        row = (await s.execute(select(AthleteSettings).where(AthleteSettings.effective_date == today))).scalar_one()
        assert row.lthr == 168
    run(go())
    assert GC.parse_hr_zones([]) is None and GC.parse_hr_zones([{"sport": "DEFAULT"}]) is None


# ---------------------------------------------------------------------------------------- A. connect

def test_token_and_password_are_sealed_and_never_shown(tmp_path, garmin):
    from backend.api import auth as AUTH
    from backend.settings import secrets
    from backend.sync import events as EV

    async def go():
        s = await make_session(tmp_path)
        r = await GC.login(EMAIL, PW, s, 1, remember=True)
        assert r == {"authenticated": True, "email": EMAIL, "password_saved": True}
        st = (await s.execute(select(SyncState))).scalar_one()
        for col in (st.garmin_token_sealed, st.garmin_password_sealed):
            assert col.startswith(secrets.PREFIX) and "SECRET" not in col and PW not in col
        assert json.loads(secrets.unseal(st.garmin_token_sealed))["di_token"].startswith("di-token-")
        assert secrets.unseal(st.garmin_password_sealed) == PW
        out = await AUTH.garmin_auth_status(1, s)
        assert out["authenticated"] is True and out["password_saved"] is True
        return json.dumps(out, default=str)
    shown = run(go())
    assert "SECRET" not in shown and PW not in shown and "di-token" not in shown
    raw = EV.path().read_text("utf-8")
    assert '"login_ok"' in raw and EMAIL not in raw and PW not in raw and "SECRET" not in raw
    assert ("sync_state", "garmin_token_sealed") in secrets.SEALED_DB_COLUMNS
    assert ("sync_state", "garmin_password_sealed") in secrets.SEALED_DB_COLUMNS


def test_remember_password_off_deletes_it_and_disconnect_keeps_the_activities(tmp_path, garmin):
    from backend.api import auth as AUTH
    _acts(garmin, 0)

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1, remember=True)
        await _sync(s)
        r = await AUTH.set_remember("garmin", AUTH.RememberBody(remember=False), s)
        assert r == {"password_saved": False}
        await GC.login(EMAIL, PW, s, 1, remember=True)
        assert (await s.execute(select(SyncState))).scalar_one().garmin_password_sealed
        await GC.login(EMAIL, PW, s, 1, remember=False)                       # unticked at login: deleted
        assert not (await s.execute(select(SyncState))).scalar_one().garmin_password_sealed
        await GC.login(EMAIL, PW, s, 1, remember=True)
        assert (await AUTH.garmin_logout(1, s)) == {"logged_out": True}
        st = (await s.execute(select(SyncState))).scalar_one()
        assert (st.garmin_token_sealed, st.garmin_password_sealed, st.garmin_email) == (None, None, None)
        assert len((await s.execute(select(WorkoutFile))).scalars().all()) == 1     # activities kept
        assert (await AUTH.garmin_auth_status(1, s))["status"] == "logged_out"
        assert not await runner.logged_in(s, "garmin", 1)
    run(go())


def test_a_refused_token_logs_in_once_with_the_remembered_password(tmp_path, garmin):
    _acts(garmin, 0)

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1, remember=True)
        garmin.revoke_all()                                                   # Garmin refuses the token
        evs = await _sync(s)
        assert any(e.get("status") == "relogin" for e in evs) and len(_done(evs)) == 1
        assert garmin.count("login") == 2                                     # one re-login, no loop
        assert GL.usage()["relogin"] == 1                                     # counts in the day
    run(go())


def test_without_a_remembered_password_it_asks_to_reconnect(tmp_path, garmin):
    from backend.api import auth as AUTH

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1)
        garmin.revoke_all()
        evs = await _sync(s)
        e = _err(evs)
        assert e["error"] == "GARMIN_AUTH_REQUIRED" and e["hint"] == "請重新連接 Garmin"
        assert garmin.count("login") == 1
        st = await AUTH.garmin_auth_status(1, s)
        assert st["expired"] is True and st["status"] == "expired"
        assert not await runner.logged_in(s, "garmin", 1)
        # the next run asks again without calling Garmin
        n = len(garmin.calls)
        assert _err(await _sync(s))["error"] == "GARMIN_AUTH_REQUIRED" and len(garmin.calls) == n
    run(go())


def test_no_re_login_while_paused(tmp_path, garmin):
    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1, remember=True)
        garmin.revoke_all()
        GL.pause("HTTP 429", status=429)
        assert _err(await _sync(s))["error"] == "GARMIN_PAUSED"
        with pytest.raises(GL.GarminBlocked):
            await GC.relogin(s, 1)
    run(go())
    assert garmin.count("login") == 1


# ---------------------------------------------------------------------------------------- C. limits

def test_a_429_is_not_retried_and_pauses_the_account_until_its_next_schedule(tmp_path, garmin):
    from backend.sync import events as EV
    _acts(garmin, 0)

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1)
        garmin.fail["list"] = 429
        assert _err(await _sync(s))["error"] == "GARMIN_RATE_LIMITED"
        assert garmin.count("list") == 1                                      # no retry
        assert GL.paused()["status"] == 429 and GL.status()["state"] == "paused"
        assert "Garmin 暫時鎖住登入，通常 2–3 天會恢復" in GL.status()["message"]
        assert (await s.execute(select(SyncState))).scalar_one().garmin_last_sync_at is None   # cursor kept
        # 立即同步 while paused: refused before any Garmin call
        evs = await _sync(s)
        assert evs == [{"status": "error", "error": "GARMIN_PAUSED", "source": "garmin", "detail": GL.PAUSED_TEXT}]
        assert garmin.count("list") == 1
        # the next scheduled sync tries once; it works again: the lock's length is logged
        garmin.fail.clear()
        assert len(_done(await _sync(s, trigger="schedule"))) == 1 and GL.paused() is None
    run(go())
    evs = EV.read("garmin")
    assert [e for e in evs if e["event"] == "http_429"] and [e for e in evs if e["event"] == "account_recovered"]


def test_a_403_on_a_download_stops_the_run_without_retry(tmp_path, garmin):
    _acts(garmin, 0, 1)
    garmin.fail["download"] = {9002: 403}

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1)
        evs = await _sync(s)
        assert _err(evs)["error"] == "GARMIN_RATE_LIMITED"
        assert garmin.count("download") == 1 and GL.paused()["status"] == 403
    run(go())


def test_a_paused_scheduled_run_refused_again_keeps_the_lock_start(tmp_path, garmin, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(GL, "wall", lambda: now[0])

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1)
        garmin.fail["list"] = 429
        await _sync(s)
        first = GL.paused()["at"]
        now[0] += 86400
        assert _err(await _sync(s, trigger="schedule"))["error"] == "GARMIN_RATE_LIMITED"
        assert GL.paused()["at"] == first                                     # still the same lock
    run(go())


def test_daily_counts_automatic_manual_and_the_re_login(tmp_path, garmin, monkeypatch):
    now = [datetime(2026, 10, 12, 9, 0).timestamp()]
    monkeypatch.setattr(GL, "wall", lambda: now[0])

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1, remember=True)
        assert _err(await _sync(s, trigger="schedule")) is None
        for _ in range(3):
            assert _err(await _sync(s)) is None
        u = GL.usage()
        assert (u["auto"], u["manual"]) == (1, 3)
        n = len(garmin.calls)
        e = _err(await _sync(s))                                              # the 4th 立即同步
        assert e["error"] == "GARMIN_DAILY_LIMIT" and len(garmin.calls) == n
        assert _err(await _sync(s, trigger="open"))["error"] == "GARMIN_DAILY_LIMIT"
        assert GL.status()["state"] == "limit" and "明天會恢復" in GL.status()["message"]
        # the next day: counts start again; an automatic re-login takes one of the day's runs
        now[0] += 86400
        garmin.revoke_all()
        evs = await _sync(s)
        assert any(e.get("status") == "relogin" for e in evs)
        assert GL.usage()["relogin"] == 1
        assert (GL.left()["auto"], GL.left()["manual"]) == (1, 2)
        await _sync(s)
        await _sync(s)
        assert (GL.left()["auto"], GL.left()["manual"]) == (0, 0)             # 3 manual + the re-login = 4
        assert _err(await _sync(s, trigger="schedule"))["error"] == "GARMIN_DAILY_LIMIT"
    run(go())


def test_the_daily_request_budget_spreads_a_first_sync_over_days(tmp_path, garmin, monkeypatch, site_garmin):
    now = [datetime(2026, 10, 12, 9, 0).timestamp()]
    monkeypatch.setattr(GL, "wall", lambda: now[0])
    site_garmin.update({"garmin.requests_per_day": 4})
    _acts(garmin, 0, 1, 2, 3, 4)

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1)
        evs = await _sync(s)
        assert len(_done(evs)) == 3 and evs[-1]["status"] == "complete" and evs[-1]["more"] is True
        assert (await s.execute(select(SyncState))).scalar_one().garmin_last_sync_at is None   # resumes
        assert (await SettingsRepository(s, 1).get("sync.garmin.last_result"))["more"] is True
        assert _err(await _sync(s))["error"] == "GARMIN_DAILY_LIMIT"          # no request left today
        now[0] += 86400
        evs = await _sync(s)
        assert len(_done(evs)) == 2 and evs[-1]["more"] is False
        assert (await s.execute(select(SyncState))).scalar_one().garmin_last_sync_at is not None
        assert len((await s.execute(select(WorkoutFile))).scalars().all()) == 5
        assert garmin.count("download") == 5                                   # none downloaded twice
    run(go())


@pytest.fixture
def site_garmin():
    from backend import site_settings as SS
    return SS


def test_one_garmin_sync_at_a_time_on_the_whole_server(tmp_path):
    peak = []

    async def sync(i):
        with tenancy.use(_tenant(tmp_path, i)):
            async with GL.sync_slot("manual"):
                peak.append(GL._SLOTS["active"])
                await asyncio.sleep(0.01)

    async def go():
        await asyncio.gather(*(sync(i) for i in range(6)))
    asyncio.run(go())
    assert len(peak) == 6 and max(peak) == 1


def test_two_seconds_between_garmin_requests(monkeypatch):
    monkeypatch.setattr(GL, "GAP_S", 2.0)
    t = [100.0]
    slept = []

    async def fake_sleep(d):
        slept.append(d)
        t[0] += d
    monkeypatch.setattr(GL, "clock", lambda: t[0])
    monkeypatch.setattr(GL, "sleep", fake_sleep)
    stamps = []

    async def go():
        for _ in range(3):
            async with GL.request():
                stamps.append(t[0])
                t[0] += 0.3                                               # the request itself
    asyncio.run(go())
    assert all(b - a >= 2.0 for a, b in zip(stamps, stamps[1:]))
    assert len(slept) == 2


def test_the_switch_off_skips_garmin_and_keeps_the_data(tmp_path, garmin):
    from backend import membership as MS
    _acts(garmin, 0)

    async def go():
        s = await make_session(tmp_path)
        await GC.login(EMAIL, PW, s, 1)
        await _sync(s)
        GL.set_enabled(False, by="owner")
        assert (await runner.ready_sources(s, 1))["garmin"] == "site_paused"
        assert _err(await _sync(s, trigger="schedule"))["error"] == "GARMIN_DISABLED"
        assert len((await s.execute(select(WorkoutFile))).scalars().all()) == 1      # data kept
        assert GL.status()["state"] == "disabled" and "sync:garmin_trial" in MS.sources_off()
        MS.set_source("sync:garmin_trial", True)                               # the owner, SP-330's switch
        assert GL.enabled() and (await runner.ready_sources(s, 1))["garmin"] == "ready"
    run(go())


def test_three_accounts_answered_429_in_a_day_trip_the_breaker(tmp_path, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(GL, "wall", lambda: now[0])
    for i in (2, 3, 2):
        assert GL.rate_limited(f"u{i}") is False
    assert GL.enabled()
    assert GL.rate_limited("u4") is True
    assert not GL.enabled() and GL.status()["tripped"]["accounts"] == 3 and GL.status()["state"] == "disabled"
    GL.reset()                                                                 # a restart does not undo it
    assert not GL.enabled()
    GL.set_enabled(True, by="owner")                                           # only the owner does
    assert GL.enabled() and GL.status()["tripped"] is None
    GL.rate_limited("u5")
    GL.rate_limited("u6")
    now[0] += GL.BREAKER_WINDOW_S + 1                                          # old ones fall out
    assert GL.rate_limited("u7") is False and GL.enabled()


def test_the_page_open_trigger_leaves_garmin_alone_once_the_automatic_run_is_used(tmp_path, garmin):
    from backend.api import sync as SYNC

    async def go():
        s = await make_session(tmp_path, sync__primary_source="garmin")
        await GC.login(EMAIL, PW, s, 1)
        GL.start_run("schedule")
        r = await SYNC.auto_sync(1, s)
        assert r["started"] == [] and r["skipped"]["garmin"] == "daily_limit"
    run(go())


def test_garmin_is_left_out_of_the_full_check(tmp_path, garmin):
    from backend.sync import check

    async def go():
        s = await make_session(tmp_path)
        return await check.status(s, 1)
    assert "garmin" not in run(go())
    assert "garmin" not in check.REMOTES


# ---------------------------------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path, monkeypatch, garmin):
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    from fastapi.testclient import TestClient
    from backend.main import build_app
    app = build_app(demo=False)
    user = _tenant(tmp_path, 2)

    async def bind(scope, receive, send):
        name = dict(scope.get("headers") or []).get(b"x-test-tenant", b"").decode()
        if scope["type"] == "http" and name == "user":
            from backend.accounts import tenants as TN
            await TN.ensure_ready(user)
            tok = tenancy.set_current(user)
            try:
                return await app(scope, receive, send)
            finally:
                tenancy.reset(tok)
        return await app(scope, receive, send)
    with TestClient(bind, raise_server_exceptions=False) as c:
        yield c


U = {"X-Test-Tenant": "user"}


def test_no_garmin_connection_without_the_disclaimer(client):
    d = client.get("/api/v1/auth/garmin/disclaimer", headers=U).json()
    assert d["version"] == GL.DISCLAIMER_VERSION and "非公開介面" in d["text"] and d["consented"] is False
    assert "暫時鎖住登入" in d["text"] and "依其條款處置帳號" in d["text"]
    r = client.post("/api/v1/auth/garmin/login", json={"email": EMAIL, "password": PW}, headers=U)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "GARMIN_CONSENT_REQUIRED"
    r = client.post("/api/v1/auth/garmin/login", headers=U,
                    json={"email": EMAIL, "password": PW, "consent_version": GL.DISCLAIMER_VERSION})
    assert r.status_code == 200 and r.json()["authenticated"] is True
    d = client.get("/api/v1/auth/garmin/disclaimer", headers=U).json()
    assert d["consented"] is True and d["consented_at"]
    # a wrong password: a plain 401, nothing personal back
    client.post("/api/v1/auth/garmin/logout", headers=U)
    r = client.post("/api/v1/auth/garmin/login", headers=U, json={"email": EMAIL, "password": "nope"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "GARMIN_LOGIN_FAILED"


def test_mfa_in_two_steps_and_a_wrong_code_can_be_typed_again(client, garmin):
    garmin.accounts[EMAIL]["mfa"] = "123456"
    body = {"email": EMAIL, "password": PW, "remember": True, "consent_version": GL.DISCLAIMER_VERSION}
    r = client.post("/api/v1/auth/garmin/login", json=body, headers=U)
    assert r.status_code == 200 and r.json() == {"authenticated": False, "mfa_required": True,
                                                 "expires_in": GC.MFA_TTL_S}
    st = client.get("/api/v1/auth/garmin/status", headers=U).json()
    assert st["mfa_pending"] is True and st["authenticated"] is False
    r = client.post("/api/v1/auth/garmin/mfa", json={"code": "000000"}, headers=U)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "GARMIN_MFA_WRONG"
    r = client.post("/api/v1/auth/garmin/mfa", json={"code": "123 456"}, headers=U)
    assert r.status_code == 200 and r.json()["authenticated"] is True and r.json()["password_saved"] is True
    st = client.get("/api/v1/auth/garmin/status", headers=U).json()
    assert st["authenticated"] is True and st["mfa_pending"] is False and st["password_saved"] is True
    assert "SECRET" not in json.dumps(st) and PW not in json.dumps(st)
    r = client.post("/api/v1/auth/garmin/mfa", json={"code": "123456"}, headers=U)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "GARMIN_MFA_EXPIRED"


def test_only_the_owner_flips_the_garmin_switch_and_connecting_is_refused_while_off(client):
    assert client.put("/api/v1/auth/garmin/site", json={"enabled": False}, headers=U).status_code == 403
    r = client.put("/api/v1/auth/garmin/site", json={"enabled": False})
    assert r.status_code == 200 and r.json()["state"] == "disabled"
    st = client.get("/api/v1/auth/garmin/site", headers=U).json()
    assert st["state"] == "disabled" and "events" not in st                  # the log is the owner's
    r = client.post("/api/v1/auth/garmin/login", headers=U,
                    json={"email": EMAIL, "password": PW, "consent_version": GL.DISCLAIMER_VERSION})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "GARMIN_DISABLED"
    assert client.put("/api/v1/auth/garmin/site", json={"enabled": True}).json()["state"] == "ok"
    assert any(e["event"] == "disabled" for e in client.get("/api/v1/auth/garmin/site").json()["events"])
    s = client.get("/api/v1/sync/settings").json()
    assert s["garmin_enabled"] is True


def test_the_app_runs_without_the_garminconnect_package(tmp_path, monkeypatch):
    """Lazy import: nothing imports garminconnect at start; the Garmin option says unavailable."""
    import importlib.util
    import sys
    monkeypatch.setattr(GC, "_LIB", None)
    monkeypatch.setitem(sys.modules, "garminconnect", None)                   # import fails
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "garminconnect" else real(name, *a))
    GL.reset()
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=False)) as c:
        st = c.get("/api/v1/auth/garmin/status").json()
        assert st["site"]["state"] == "unavailable" and st["site"]["available"] is False
        r = c.post("/api/v1/auth/garmin/login",
                   json={"email": EMAIL, "password": PW, "consent_version": GL.DISCLAIMER_VERSION})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "GARMIN_UNAVAILABLE"
        assert c.get("/api/v1/sync/sources").status_code == 200
    with pytest.raises(GC.GarminUnavailable):
        GC.lib()
