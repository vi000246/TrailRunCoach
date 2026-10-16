"""SP-346: intervals.icu — OAuth connect / 中斷連接 (disconnect-app), the webhook (secret, queue, run),
the sync (original file gunzipped → FIT object → dedup), the daily poll that catches what a webhook
missed, Strava skipped, Huawei marked 不是原始檔, the Garmin attribution, the day-83 dormancy reminder
and the back-fill queued within the app's budget. intervals.icu is a fake (httpx.MockTransport through
sync/http.py): no network, no real keys."""
from __future__ import annotations

import gzip
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import SyncState, WorkoutFile
from backend.settings.repository import SettingsRepository
from backend.sync import dedup, http, intervals_client as IC, intervals_limits as IL, runner
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import collect, make_session, run

TOKEN = "icu-token-SENTINEL-1234"
CODE = "code-abc"
ICU_ID = "i777"
SECRET = "hook-secret-xyz"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


class FakeIcu:
    """intervals.icu's OAuth token endpoint, athlete, activity list, original file and disconnect-app."""

    def __init__(self):
        self.acts: list[dict] = []
        self.files: dict = {}
        self.calls: list = []
        self.last_seen = (NOW - timedelta(days=3)).isoformat()
        self.refuse = False
        self.disconnect_status = 200
        self.gzip = True

    def add(self, aid: str, start: datetime, source="GARMIN_CONNECT", device="Garmin Forerunner 965",
            file_type="fit", fit: bytes | None = None, **kw) -> dict:
        a = {"id": aid, "start_date_local": (start + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S"),
             "type": "Run", "source": source, "device_name": device, "file_type": file_type,
             "name": "Morning run (personal)", **kw}
        self.acts.append(a)
        if fit is not False:
            self.files[aid] = fit if fit is not None else build_run(start, power=220)
        return a

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "intervals.icu"
        path = request.url.path
        self.calls.append((request.method, path, dict(request.url.params)))
        if path == "/api/oauth/token":
            form = parse_qs(request.content.decode())
            if form.get("code") != [CODE] or form.get("client_secret") != ["csecret"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"token_type": "Bearer", "access_token": TOKEN, "scope": "ACTIVITY:READ",
                                             "athlete": {"id": ICU_ID, "name": "Someone"}})
        if self.refuse or request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"status": 401})
        if path == "/api/v1/disconnect-app" and request.method == "DELETE":
            return httpx.Response(self.disconnect_status)
        if path == "/api/v1/athlete/0":
            return httpx.Response(200, json={"id": ICU_ID, "icu_last_seen": self.last_seen})
        if path == "/api/v1/athlete/0/activities":
            lo, hi = request.url.params["oldest"], request.url.params["newest"]
            return httpx.Response(200, json=[a for a in self.acts if lo <= a["start_date_local"][:10] <= hi])
        m = re.fullmatch(r"/api/v1/activity/([^/]+)/file", path)
        if m:
            data = self.files.get(m.group(1))
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, content=gzip.compress(data) if self.gzip else data,
                                  headers={"Content-Type": "application/octet-stream"})
        return httpx.Response(404)

    def count(self, method, path_re) -> int:
        return sum(1 for m, p, _q in self.calls if m == method and re.fullmatch(path_re, p))


@pytest.fixture
def icu(monkeypatch):
    monkeypatch.setenv(IC.ENV_CLIENT_ID, "cid-1")
    monkeypatch.setenv(IC.ENV_CLIENT_SECRET, "csecret")
    monkeypatch.setenv(IC.ENV_WEBHOOK_SECRET, SECRET)
    fake = FakeIcu()
    with http.use_transport(httpx.MockTransport(fake.handler)):
        yield fake


async def _connect(s):
    url = IC.authorize_url(1, "http://localhost:8000")
    state = parse_qs(urlsplit(url).query)["state"][0]
    row = IC.pop_state(state)
    return await IC.finish_connect(s, row["athlete_id"], CODE, row["redirect_uri"])


async def _sync(s, since=None):
    return await collect(runner.stream(s, "intervals", 1, since=since, trigger="manual"))


def _done(evs):
    return [e for e in evs if e.get("status") == "downloaded"]


# ---------------------------------------------------------------------------- connect / disconnect

def test_connect_with_oauth_stores_the_token_sealed_and_disconnect_calls_disconnect_app(tmp_path, icu):
    """設定頁 OAuth 連接、中斷；中斷時呼叫 disconnect-app."""
    url = IC.authorize_url(1, "http://localhost:8000")
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://intervals.icu/oauth/authorize?")
    assert q["client_id"] == ["cid-1"] and q["scope"] == ["ACTIVITY:READ"]
    assert q["redirect_uri"] == ["http://localhost:8000/oauth/intervals/callback"]
    state = q["state"][0]
    row = IC.pop_state(state)
    assert row["tenant"] == "owner" and IC.pop_state(state) is None              # used once

    async def go():
        s = await make_session(tmp_path)
        out = await IC.finish_connect(s, 1, CODE, row["redirect_uri"])
        assert out["authenticated"] is True
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.intervals_athlete_id == ICU_ID
        assert st.intervals_token_sealed.startswith("enc:v1:") and TOKEN not in st.intervals_token_sealed
        assert IL.tenant_for(ICU_ID) == "owner" and ICU_ID not in json.dumps(IL.SS.all())   # hashed key only
        status = await IC.status(s, 1)
        assert status["authenticated"] is True and TOKEN not in json.dumps(status)
        assert status["backfill"]["done"] is False
        out = await IC.disconnect(s, 1)
        assert out == {"logged_out": True, "remote": "ok"}
        assert icu.count("DELETE", "/api/v1/disconnect-app") == 1
        await s.refresh(st)
        assert st.intervals_token_sealed is None and st.intervals_athlete_id is None
        assert IL.tenant_for(ICU_ID) is None
        assert (await IC.status(s, 1))["status"] == "logged_out"
    run(go())


def test_disconnect_deletes_the_token_even_when_intervals_does_not_answer(tmp_path, icu):
    icu.disconnect_status = 500

    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        assert (await IC.disconnect(s, 1))["remote"] == "failed"
        assert (await s.execute(select(SyncState))).scalar_one().intervals_token_sealed is None
    run(go())


def test_a_refused_code_and_a_declined_approval_store_nothing(tmp_path, icu):
    async def go():
        s = await make_session(tmp_path)
        with pytest.raises(IC.IntervalsConnectFailed) as e:
            await IC.finish_connect(s, 1, "wrong-code", "http://x/oauth/intervals/callback")
        assert e.value.code == "INTERVALS_CODE_REFUSED"
        assert (await s.execute(select(SyncState))).scalar_one_or_none() is None
    run(go())


def test_without_the_oauth_app_the_option_says_not_open_and_nothing_breaks(tmp_path, monkeypatch):
    """Client id / secret not set: 還沒開放 (the app has not been approved yet)."""
    assert IC.configured() is False
    with pytest.raises(IC.IntervalsNotConfigured):
        IC.authorize_url(1, "http://localhost:8000")

    async def go():
        s = await make_session(tmp_path)
        assert (await IC.status(s, 1))["configured"] is False
        assert (await runner.ready_sources(s, 1))["intervals"] in ("not_configured", "not_logged_in")
        evs = await _sync(s)
        assert evs[-1]["error"] in ("INTERVALS_NOT_CONFIGURED",)
    run(go())


# ---------------------------------------------------------------------------- sync

def test_a_new_activity_is_synced_as_its_original_fit(tmp_path, icu):
    """intervals.icu 新增一筆 → 本 app 出現，存的是原始 FIT（gzip 解壓後的位元組）."""
    start = NOW - timedelta(days=2)
    fit = build_run(start, power=230)
    icu.add("i1001", start, fit=fit)

    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        evs = await _sync(s)
        assert len(_done(evs)) == 1 and evs[-1]["status"] == "complete" and evs[-1]["errors"] == []
        wf = (await s.execute(select(WorkoutFile))).scalar_one()
        assert wf.source == "intervals" and wf.intervals_activity_id == "i1001"
        assert wf.upstream_source == "GARMIN_CONNECT" and wf.device_name == "Garmin Forerunner 965"
        from backend.files import fitstore
        assert wf.file_path.startswith("intervals/objects/")
        assert fitstore.default_store().get(wf.file_path) == fit              # the original bytes
        # again: nothing downloaded twice
        evs2 = await _sync(s)
        assert not _done(evs2) and icu.count("GET", r"/api/v1/activity/.+/file") == 1
        res = await SettingsRepository(s, 1).get("sync.intervals.last_result")
        assert res["status"] == "ok"
    run(go())


def test_the_webhook_secret_queue_and_its_run(tmp_path, icu):
    """webhook 收 ACTIVITY_UPLOADED／ACTIVITY_ANALYZED，用 payload 的 secret 驗證，排進佇列後下載."""
    start = NOW - timedelta(hours=5)
    a = icu.add("i2001", start)

    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        n = await IC.add_pending(s, 1, [a, a, {"id": "i2002", "source": "COROS"}])
        assert n == 2
        evs = await collect(runner.stream(s, "intervals", 1, trigger="webhook", remember=False,
                                          client=lambda d: IC.drain_pending(d, 1)))
        assert [e["activity_id"] for e in _done(evs)] == ["i2001"]
        # i2002 has no file (404): one failed answer, kept on the failed list, out of the queue
        assert await SettingsRepository(s, 1).get("sync.intervals.pending") is None
        # list-free: a webhook run never lists activities
        assert icu.count("GET", "/api/v1/athlete/0/activities") == 0
    run(go())


def test_the_daily_poll_catches_an_activity_whose_webhook_never_came(tmp_path, icu):
    """webhook 漏掉時，每日輪詢會補上."""
    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        await _sync(s)                                              # first run: nothing there yet
        icu.add("i3001", NOW - timedelta(hours=3))                   # uploaded; its webhook was lost
        evs = await collect(runner.stream(s, "intervals", 1, trigger="schedule"))
        assert [e["activity_id"] for e in _done(evs)] == ["i3001"]
        listed = [q for m, p, q in icu.calls if p == "/api/v1/athlete/0/activities"]
        assert listed[-1]["newest"] >= NOW.date().isoformat()        # the poll reaches today
    run(go())


def test_the_same_run_from_coros_and_from_intervals_counts_once(tmp_path, icu):
    """同一場活動從 COROS 直連與 intervals.icu 各來一次，只算一次（dedup）."""
    from backend.files.file_service import _import_one_file
    from backend.sync import storage
    start = NOW - timedelta(days=1)
    icu.add("i4001", start + timedelta(seconds=40), source="COROS", device="COROS PACE 3",
            fit=build_run(start + timedelta(seconds=40), power=250))

    async def go():
        s = await make_session(tmp_path)
        c = storage.year_dir("coros", start.year) / "C1_run.fit"
        c.write_bytes(build_run(start, power=240))
        await _import_one_file(s, 1, c, source="coros")
        await s.commit()
        await _connect(s)
        await _sync(s)
        rows = (await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars().all()
        assert [r.source for r in rows] == ["coros", "intervals"]
        canon = [r for r in rows if r.duplicate_of is None]
        assert len(canon) == 1                                       # one activity
        # the 資料來源 decides which copy counts: intervals.icu in use -> its row
        await SettingsRepository(s, 1).set("sync.primary_source", "intervals")
        await dedup.rebuild(s, 1)
        rows = (await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars().all()
        assert [r.source for r in rows if r.duplicate_of is None] == ["intervals"]
    run(go())


def test_strava_is_skipped_without_an_error_and_huawei_is_marked_not_original(tmp_path, icu):
    """Strava 來源略過、不報錯；Huawei 來源標「不是原始檔」."""
    icu.add("i5001", NOW - timedelta(days=1, hours=2), source="STRAVA", device=None, fit=False)
    icu.add("i5002", NOW - timedelta(days=2), source="HUAWEI", device="HUAWEI WATCH GT 4")
    icu.add("i5003", NOW - timedelta(days=3), file_type="gpx", fit=False)

    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        evs = await _sync(s)
        assert evs[-1]["status"] == "complete" and evs[-1]["errors"] == []
        st = {e["activity_id"]: (e["status"], e.get("reason")) for e in evs if e.get("activity_id")
              and e["status"] not in ("checking",)}
        assert st["i5001"] == ("skipped", "strava")
        assert st["i5003"] == ("no_file", "not_fit")
        assert st["i5002"][0] == "downloaded"
        assert icu.count("GET", "/api/v1/activity/i5001/file") == 0
        wf = (await s.execute(select(WorkoutFile))).scalar_one()
        assert wf.upstream_source == "HUAWEI" and dedup.not_original(wf)
        res = await SettingsRepository(s, 1).get("sync.intervals.last_result")
        assert res["status"] == "ok" and res["errors"] == 0
    run(go())


def test_a_not_original_copy_never_wins_over_the_original():
    from types import SimpleNamespace as N
    orig = N(id=2, source="upload", file_format="fit", upstream_source=None)
    huawei = N(id=1, source="intervals", file_format="fit", upstream_source="HUAWEI")
    assert dedup.choose_canonical([huawei, orig], None) is orig


def test_garmin_attribution_text_and_marks():
    from backend.sync import garmin_attribution as GA
    assert GA.device_label("Garmin Forerunner 965") == "Garmin Forerunner 965"
    assert GA.device_label("fenix 7") == "Garmin fenix 7"
    assert GA.device_label(None) == "Garmin"                       # model unknown: Garmin
    m = GA.mark("Garmin Forerunner 965", "GARMIN_CONNECT")
    assert m["garmin"] == "Garmin Forerunner 965" and m["not_original"] is False
    assert "Garmin" in m["derived"]
    assert GA.mark(None, "GARMIN_CONNECT")["garmin"] == "Garmin"
    assert GA.mark("COROS PACE 3", "COROS") is None
    h = GA.mark("HUAWEI WATCH GT 4", "HUAWEI")
    assert h["garmin"] is None and h["not_original"] is True
    from backend import i18n
    tok = i18n.set_locale("en")
    try:
        assert GA.mark("Garmin Edge", None)["derived"] == "Insights derived in part from Garmin device-sourced data."
    finally:
        i18n.reset_locale(tok)


def test_a_refused_token_asks_to_connect_again(tmp_path, icu):
    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        icu.refuse = True                                          # the app was removed on intervals.icu
        evs = await _sync(s)
        assert evs[-1]["error"] == "INTERVALS_AUTH_REQUIRED"
        st = await IC.status(s, 1)
        assert st["expired"] is True and st["authenticated"] is False
        assert await runner.logged_in(s, "intervals", 1) is False
    run(go())


# ---------------------------------------------------------------------------- dormancy

def test_icu_last_seen_over_83_days_reminds(tmp_path, icu):
    """icu_last_seen 超過 83 天時出現提醒."""
    assert IC.dormancy({"last_seen": (NOW - timedelta(days=82)).isoformat()}, NOW)["remind"] is False
    d = IC.dormancy({"last_seen": (NOW - timedelta(days=83)).isoformat()}, NOW)
    assert d["remind"] is True and d["dormant"] is False and d["days_left"] == 7
    assert IC.dormancy({"last_seen": (NOW - timedelta(days=95)).isoformat()}, NOW)["dormant"] is True
    assert IC.dormancy(None) is None
    icu.last_seen = (NOW - timedelta(days=85)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        await _sync(s)                                              # the daily sync reads it
        st = await IC.status(s, 1)
        assert st["dormancy"]["remind"] is True and st["dormancy"]["days"] == 85
    run(go())


# ---------------------------------------------------------------------------- back-fill and limits

def test_budget_is_the_documented_app_total():
    assert IL.daily_budget(0) == 5000 and IL.daily_budget(30) == 5000
    assert IL.daily_budget(120) == 12000 and IL.daily_budget(800) == 50000
    assert IL.quarter_budget(5000) == 2500 and IL.quarter_budget(40000) == 5000


def test_the_first_backfill_goes_in_windows_and_stops_within_its_share(tmp_path, icu, monkeypatch):
    """首次回填用 oldest／newest 分段排隊，不超過 intervals.icu 的額度."""
    for i, days in enumerate((40, 80, 200, 400)):
        icu.add(f"i60{i}", NOW - timedelta(days=days))
    monkeypatch.setattr(IL, "BACKFILL_PER_RUN", 5)                 # a small share for the test

    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        evs = await _sync(s)
        assert evs[-1]["status"] == "complete" and evs[-1]["more"] is True and evs[-1]["detail"]
        bf = await SettingsRepository(s, 1).get("sync.intervals.backfill")
        assert bf["done"] is False and bf["windows"] >= 1
        # every list is one window of at most WINDOW_DAYS (oldest / newest)
        from datetime import date
        for m, p, q in icu.calls:
            if p == "/api/v1/athlete/0/activities":
                span = (date.fromisoformat(q["newest"]) - date.fromisoformat(q["oldest"])).days + 1
                assert 1 <= span <= IC.WINDOW_DAYS
        assert IL.status()["backfill"] <= 5 + 1
        # the next runs go on from where it stopped, until everything is in
        for _i in range(40):
            evs = await _sync(s)
            if not evs[-1].get("more"):
                break
        bf = await SettingsRepository(s, 1).get("sync.intervals.backfill")
        assert bf["done"] is True
        got = {r.intervals_activity_id for r in (await s.execute(select(WorkoutFile))).scalars()}
        assert got == {"i600", "i601", "i602", "i603"}
    run(go())


def test_the_server_wide_backfill_share_queues_the_rest_for_another_day(tmp_path, icu, monkeypatch):
    icu.add("i7001", NOW - timedelta(days=60))
    u = IL._usage()
    u["backfill"] = int(IL.daily_budget() * IL.BACKFILL_SHARE)    # other accounts used today's share

    async def go():
        s = await make_session(tmp_path)
        await _connect(s)
        evs = await _sync(s)
        assert evs[-1]["status"] == "complete" and evs[-1]["more"] is True and not _done(evs)
        with pytest.raises(IL.IntervalsBudget):
            IL.use(backfill=True)
        IL.use()                                                   # new activities still have room
    run(go())


def test_the_daily_total_is_never_passed(monkeypatch):
    u = IL._usage()
    u["requests"] = int(IL.daily_budget() * IL.SAFETY)
    with pytest.raises(IL.IntervalsBudget) as e:
        IL.use()
    assert e.value.code == "INTERVALS_DAILY_LIMIT"


def test_decode_file_takes_gzip_zip_or_a_bare_fit_and_refuses_the_rest():
    import io
    import zipfile
    fit = build_run(NOW)
    assert IC.decode_file(gzip.compress(fit)) == fit and IC.decode_file(fit) == fit
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("a.fit", fit)
    assert IC.decode_file(b.getvalue()) == fit
    for bad in (b"", gzip.compress(b"<gpx></gpx>"), b"\x1f\x8bnot gzip"):
        with pytest.raises(IC.NotFit):
            IC.decode_file(bad)


# ---------------------------------------------------------------------------- the API

@pytest.fixture
def client(tmp_path, monkeypatch, icu):
    """The app on its own DB under tmp_path: these tests set 資料來源 = intervals and sync rows into
    it; on the shared app DB under the faked home that leaked into later tests on the same worker
    (test_new_user_smoke then read the intervals folder instead of COROS)."""
    import asyncio
    import backend.db.database as db_mod
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    path = tmp_path / "app.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    try:
        with TestClient(build_app(demo=False), raise_server_exceptions=False) as c:
            yield c
    finally:
        asyncio.run(db_mod.dispose(path))


def _hook(client, acts, secret=SECRET, typ="ACTIVITY_UPLOADED", athlete=ICU_ID):
    return client.post("/api/v1/intervals/webhook", json={"secret": secret, "events": [
        {"athlete_id": athlete, "type": typ, "timestamp": NOW.isoformat(), "activity": a} for a in acts]})


def test_the_api_connect_callback_webhook_and_disconnect(client, icu):
    st = client.get("/api/v1/auth/intervals/status").json()
    assert st["configured"] is True and st["status"] == "logged_out"
    url = client.post("/api/v1/auth/intervals/connect").json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    r = client.get(f"/oauth/intervals/callback?state={state}&code={CODE}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?intervals=connected#sync")
    r = client.get(f"/oauth/intervals/callback?state={state}&code={CODE}", follow_redirects=False)
    assert r.headers["location"].endswith("?intervals=expired#sync")                     # once
    url = client.post("/api/v1/auth/intervals/connect").json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    r = client.get(f"/oauth/intervals/callback?state={state}&error=access_denied", follow_redirects=False)
    assert r.headers["location"].endswith("?intervals=denied#sync")
    st = client.get("/api/v1/auth/intervals/status").json()
    assert st["authenticated"] is True and TOKEN not in json.dumps(st) and ICU_ID not in json.dumps(st)

    # the webhook: a wrong secret is refused; intervals.icu not the 資料來源 yet -> nothing queued
    a = icu.add("i8001", NOW - timedelta(hours=1))
    assert _hook(client, [a], secret="nope").status_code == 401
    assert _hook(client, [a]).json() == {"ok": True, "queued": 0}
    assert client.put("/api/v1/sync/settings", json={"primary_source": "intervals"}).status_code == 200
    assert _hook(client, [a], typ="WELLNESS_UPDATED").json()["queued"] == 0
    assert _hook(client, [a], athlete="i999").json()["queued"] == 0                     # not ours
    assert _hook(client, [a]).json() == {"ok": True, "queued": 1}
    for _i in range(100):                                          # the background run downloads it
        if client.get("/api/v1/auth/intervals/status").json()["pending"] == 0 and \
                client.get("/api/v1/sync/sources").json()["intervals"]["stats"]["rows"]:
            break
        time.sleep(0.05)
    assert client.get("/api/v1/sync/sources").json()["intervals"]["stats"]["rows"] == 1
    assert icu.count("GET", "/api/v1/activity/i8001/file") == 1

    r = client.post("/api/v1/auth/intervals/logout")
    assert r.status_code == 200 and r.json()["remote"] == "ok"
    assert icu.count("DELETE", "/api/v1/disconnect-app") == 1
    assert client.get("/api/v1/auth/intervals/status").json()["status"] == "logged_out"
    assert _hook(client, [a]).json()["queued"] == 0                 # unmapped after the disconnect


def test_the_webhook_is_off_without_its_secret(client, monkeypatch):
    monkeypatch.delenv(IC.ENV_WEBHOOK_SECRET)
    assert client.post("/api/v1/intervals/webhook", json={"secret": "", "events": []}).status_code == 404


def test_connect_is_refused_before_the_app_is_approved(client, monkeypatch):
    monkeypatch.delenv(IC.ENV_CLIENT_ID)
    r = client.post("/api/v1/auth/intervals/connect")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "INTERVALS_NOT_CONFIGURED"
    assert client.get("/api/v1/auth/intervals/status").json()["configured"] is False


def test_signin_mode_lets_only_the_webhook_through_without_a_session():
    from backend import tenancy_mw
    assert tenancy_mw.INTERVALS_WEBHOOK == "/api/v1/intervals/webhook"
    from backend.accounts import login
    assert login.is_public("/oauth/intervals/callback")


def test_the_activity_rows_carry_the_garmin_mark_from_the_db(tmp_path):
    """活動頁／圖表／debug API (AI) read device_mark: workout_files.device_name / upstream_source
    through the Dataset's classification rows (fitdataset.load_classifications → file_mark)."""
    import sqlite3
    from types import SimpleNamespace as N
    from sqlalchemy import create_engine
    from backend.db.models import Base
    from backend.engine.wko5expr import fitdataset as FD
    from backend.api import wko5views as WV
    db = tmp_path / "app.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{db}"))
    con = sqlite3.connect(db)
    for i, (fp, dev, ups) in enumerate((("/x/fit/intervals/a.fit", "Garmin fenix 7", "GARMIN_CONNECT"),
                                        ("/x/fit/intervals/b.fit", "HUAWEI WATCH", "HUAWEI"),
                                        ("/x/fit/intervals/c.fit", None, None)), start=1):
        con.execute("INSERT INTO workout_files (id, athlete_id, file_path, file_format, source, device_name, "
                    "upstream_source, imported_at) VALUES (?, 1, ?, 'fit', 'intervals', ?, ?, '2026-10-01')",
                    (i, fp, dev, ups))
    con.commit()
    con.close()
    rows = FD.load_classifications(db)
    ds = N(dir=__import__("pathlib").Path("/x/fit/intervals"), _classes=rows)
    ds.file_mark = lambda w=None, file=None: FD.FitFolderDataset.file_mark(ds, w, file=file)
    a = WV._device_mark(ds, file="a.fit")
    assert a["garmin"] == "Garmin fenix 7" and a["derived"]
    b = WV._device_mark(ds, file="b.fit")
    assert b["garmin"] is None and b["not_original"] is True
    assert WV._device_mark(ds, file="c.fit") is None
    assert WV._device_mark(N(), file="a.fit") is None              # a WKO5 Dataset: no marks


def test_the_overview_banner_reminds_from_day_83(client, icu):
    """icu_last_seen ≥ 83 天：總覽／課表的 banner（/auth/session-alerts）也提醒."""
    url = client.post("/api/v1/auth/intervals/connect").json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    client.get(f"/oauth/intervals/callback?state={state}&code={CODE}", follow_redirects=False)
    client.put("/api/v1/sync/settings", json={"primary_source": "intervals"})
    assert client.get("/api/v1/auth/session-alerts").json()["intervals_dormancy"] is None
    icu.last_seen = (NOW - timedelta(days=84)).isoformat()
    evs = client.post("/api/v1/sync/intervals/start").text
    assert '"complete"' in evs
    d = client.get("/api/v1/auth/session-alerts").json()["intervals_dormancy"]
    assert d["days"] == 84 and d["days_left"] == 6 and d["url"] == "https://intervals.icu"
