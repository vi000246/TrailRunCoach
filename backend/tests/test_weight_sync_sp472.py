"""
SP-472: body weight from Apple Health — the weight API (a weight-only token), every measurement kept
with its source and nothing recomputed when one arrives; the nightly apply (≥ 0.5 kg or ≥ 7 days, the
day's last value, the user's own entries first) and only then the recompute; the COROS account weight
as a hint, never written by itself; tokens made / revoked on the 設定頁; sign-in mode: the token opens
that one POST, in its user's own tenant. Synthetic data only: an in-memory DB, plan.json in tmp_path.
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from backend import weight_auth as WA
from backend.api import weight as W
from backend.db.models import AthleteSettings, WeightMeasurement
from backend.engine import planning as P
from backend.engine import weight_sync as WS
from backend.tests.test_coros_workouts import make_db, run

TPE = ZoneInfo("Asia/Taipei")
NOW = dt.datetime(2026, 10, 13, 9, 0, tzinfo=TPE)
API = "/api/v1/weight"
INGEST = API + "/measurements"


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


class _SF:
    """A session factory over the test's one in-memory session (tick opens `async with sf()`)."""

    def __init__(self, s):
        self.s = s

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        return False


class Env:
    def __init__(self, monkeypatch, tmp_path):
        from backend.db.database import get_db
        from backend.settings.repository import SettingsRepository
        monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
        self.plan_path = tmp_path / "plan.json"
        self.db = run(make_db(logged_in=False))

        async def tz():
            await SettingsRepository(self.db).set("athlete.timezone", "Asia/Taipei")
            await self.db.commit()
        run(tz())
        self.notified = []
        monkeypatch.setattr(W, "_notify", lambda: self.notified.append(1))
        # receiving a measurement never builds a Dataset nor clears a cache: any call fails the test
        from backend.api import plan as plan_api, wko5views
        monkeypatch.setattr(wko5views, "_dataset", _boom)
        monkeypatch.setattr(wko5views, "plan_changed", _boom)
        monkeypatch.setattr(plan_api, "_notify", _boom)
        WA.reset_limits()
        self.clock = [NOW]
        monkeypatch.setattr(W, "CLOCK", lambda: self.clock[0].astimezone(dt.timezone.utc))

        async def fake_db():
            yield self.db
        app = FastAPI()
        app.include_router(W.router)
        app.dependency_overrides[get_db] = fake_db
        self.c = TestClient(app)

    def token(self, name="iPhone") -> str:
        r = self.c.post(f"{API}/tokens", json={"name": name})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def rows(self):
        return run(self.db.execute(select(WeightMeasurement).order_by(WeightMeasurement.measured_at))).scalars().all()

    def tick(self, when: dt.datetime):
        return run(W.tick(_SF(self.db), now=when))


def _boom(*a, **k):
    raise AssertionError("a recompute / Dataset was started")


@pytest.fixture
def e(monkeypatch, tmp_path):
    env = Env(monkeypatch, tmp_path)
    yield env
    WA.reset_limits()


def shortcut(kg, time, unit=None, **kw):
    b = {"kg": kg, "time": time, **kw}
    if unit:
        b["unit"] = unit
    return b


def hae(*rows, name="weight_body_mass", units="kg"):
    return {"data": {"metrics": [{"name": "step_count", "units": "count", "data": [{"qty": 9000, "date": "2026-10-12 00:00:00 +0800"}]},
                                 {"name": name, "units": units, "data": [
                                     {"qty": kg, "date": d, "source": "Withings"} for kg, d in rows]}]}}


# ---------------------------------------------------------------------------- parsing (pure)

def test_parse_reads_the_shortcut_and_health_auto_export_and_checks_range_and_time():
    now = NOW.astimezone(dt.timezone.utc)
    p = WS.parse(shortcut(72.3, "2026-10-13T07:12:00+08:00"), TPE, now)
    assert p.source == "shortcut" and [(i.kg, i.measured_at.isoformat()) for i in p.items] == [
        (72.3, "2026-10-13T07:12:00+08:00")]
    # pounds, a comma decimal, a time without an offset (the athlete's zone), a list
    p = WS.parse({"measurements": [shortcut("159,4", "2026-10-12T07:00:00", unit="lb"),
                                   shortcut("71.9 kg", "2026-10-11T07:00:00Z")]}, TPE, now)
    assert [round(i.kg, 1) for i in p.items] == [72.3, 71.9]
    assert p.items[0].measured_at.utcoffset() == dt.timedelta(hours=8) and not p.rejected
    # Health Auto Export: only the weight metric (both of its names), 'yyyy-MM-dd HH:mm:ss Z'
    for name in ("weight_body_mass", "weight_&_body_mass"):
        p = WS.parse(hae((72.0, "2026-10-12 07:01:00 +0800"), (71.8, "2026-10-13 06:59:00 +0800"), name=name), TPE, now)
        assert p.source == "health_auto_export" and p.ignored == 1
        assert [(i.kg, i.measured_at.hour, i.device) for i in p.items] == [(72.0, 7, "Withings"), (71.8, 6, "Withings")]
    # refused one by one, with a reason
    bad = [shortcut(24.9, "2026-10-13T07:00:00+08:00"), shortcut(250.1, "2026-10-13T07:00:00+08:00"),
           shortcut(72, "2026-10-13T13:00:00+08:00"),                 # 4 h ahead of now
           shortcut(72, "2022-01-01T07:00:00+08:00"), shortcut(72, None), shortcut(72, "yesterday"),
           shortcut(72, "2026-10-13T07:00:00+08:00", unit="stone"), shortcut("abc", "2026-10-13T07:00:00+08:00"),
           shortcut(True, "2026-10-13T07:00:00+08:00")]
    p = WS.parse({"measurements": bad}, TPE, now)
    assert not p.items and [r["reason"] for r in p.rejected] == [
        "kg_out_of_range", "kg_out_of_range", "time_in_future", "time_too_old", "time_missing", "time_unreadable",
        "unit_unknown", "kg_missing", "kg_missing"]
    for body in ([], "72", {"foo": 1}, {"measurements": "x"}):
        with pytest.raises(WS.PayloadError):
            WS.parse(body, TPE, now)
    with pytest.raises(WS.PayloadError) as ex:
        WS.parse({"measurements": [shortcut(72, "2026-10-13T07:00:00+08:00")] * (WS.MAX_ITEMS + 1)}, TPE, now)
    assert ex.value.code == "too_many"


# ---------------------------------------------------------------------------- receiving: stored, nothing recomputed

def test_received_measurements_are_all_stored_with_their_source_and_nothing_is_recomputed(e):
    P.Plan(weights=[P.Weight("2026-01-10", 74.0)]).save()
    before = e.plan_path.read_bytes()
    tok = e.token()
    r = e.c.post(INGEST, json=shortcut(72.3, "2026-10-13T07:12:00+08:00", device="Withings Body+"), headers=bearer(tok))
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["source"], b["added"], b["updated"], b["unchanged"]) == ("shortcut", 1, 0, 0)
    assert b["applies_at"].startswith("2026-10-14T03:00") or "T03:00" in b["applies_at"]
    assert "72.3" in b["message"]
    # the same sample again: nothing new; Health Auto Export's copy is its own row (another source)
    assert e.c.post(INGEST, json=shortcut(72.3, "2026-10-13T07:12:00+08:00"), headers=bearer(tok)).json()["unchanged"] == 1
    r = e.c.post(INGEST, json=hae((72.3, "2026-10-13 07:12:00 +0800"), (72.9, "2026-10-12 07:05:00 +0800")),
                 headers=bearer(tok))
    assert r.status_code == 200 and r.json()["added"] == 2 and r.json()["ignored_metrics"] == 1
    rows = e.rows()
    assert [(x.day.isoformat(), x.kg, x.source, x.status) for x in rows] == [
        ("2026-10-12", 72.9, "health_auto_export", "new"),
        ("2026-10-13", 72.3, "shortcut", "new"), ("2026-10-13", 72.3, "health_auto_export", "new")]
    assert rows[1].device == "Withings Body+" and rows[1].measured_at == dt.datetime(2026, 10, 12, 23, 12)
    # a corrected value of the same sample: updated, `new` again
    r = e.c.post(INGEST, json=shortcut(72.1, "2026-10-13T07:12:00+08:00"), headers=bearer(tok))
    assert r.json()["updated"] == 1
    # plan.json untouched, no recompute (the Env makes every recompute path raise)
    assert e.plan_path.read_bytes() == before and e.notified == []
    # all refused: 422 with the reasons; not JSON: 400; too big: 413
    r = e.c.post(INGEST, json=shortcut(300, "2026-10-13T07:12:00+08:00"), headers=bearer(tok))
    assert r.status_code == 422 and r.json()["detail"]["rejected"] == [{"i": 0, "reason": "kg_out_of_range"}]
    assert e.c.post(INGEST, content=b"{nope", headers=bearer(tok)).status_code == 400
    big = b'{"measurements": [' + b",".join([b'{"kg": 72, "time": "2026-10-13T07:00:00+08:00"}'] * 30000) + b"]}"
    assert e.c.post(INGEST, content=big, headers=bearer(tok)).status_code == 413
    assert len(e.rows()) == 3


# ---------------------------------------------------------------------------- the token

def test_the_token_only_sends_weights_is_stored_hashed_and_can_be_revoked(e, monkeypatch):
    body = shortcut(72.3, "2026-10-13T07:12:00+08:00")
    assert e.c.post(INGEST, json=body).status_code == 401                                        # none
    assert e.c.post(INGEST, json=body, headers=bearer("trcw_" + "x" * 43)).status_code == 401   # unknown
    assert e.c.post(INGEST, json=body, headers={"Authorization": "Basic abc"}).status_code == 401
    tok = e.token()
    e.c.cookies.set("token", tok)                                    # a cookie is never read
    assert e.c.post(INGEST, json=body).status_code == 401
    e.c.cookies.clear()
    # the header for a proxy that keeps Authorization for itself
    assert e.c.post(INGEST, json=body, headers={"X-TRC-Weight-Token": tok, "Authorization": "Basic cHJveHk6cHc="}
                    ).status_code == 200
    # only the SHA-256 is stored
    dump = "\n".join(str(r) for r in run(e.db.execute(text("SELECT * FROM weight_tokens"))).all())
    assert tok not in dump and WA.hash_token(tok) in dump
    st = e.c.get(f"{API}/status").json()
    assert [t["prefix"] for t in st["tokens"]] == [tok[:9]] and tok not in json.dumps(st)
    assert st["endpoint"].endswith("/api/v1/weight/measurements")
    # revoke: 401 from then on
    tid = st["tokens"][0]["id"]
    assert e.c.delete(f"{API}/tokens/{tid}").json()["revoked"] == 1
    r = e.c.post(INGEST, json=body, headers=bearer(tok))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_INVALID"
    t2, t3 = e.token("a"), e.token("b")
    assert e.c.post(f"{API}/tokens/revoke-all").json()["revoked"] == 2
    assert e.c.post(INGEST, json=body, headers=bearer(t3)).status_code == 401
    # at most MAX_ACTIVE live ones
    for i in range(WA.MAX_ACTIVE):
        e.token(f"t{i}")
    assert e.c.post(f"{API}/tokens", json={"name": "one too many"}).status_code == 400


def test_failed_tokens_block_the_ip_but_never_a_valid_token_and_valid_ones_are_rate_limited(e, monkeypatch):
    from backend.security import ratelimit as RL
    monkeypatch.setattr(WA, "TOKEN_BUCKET", RL.Buckets(rate=3, per=3600))
    tok = e.token()
    body = shortcut(72.3, "2026-10-13T07:12:00+08:00")
    codes = [e.c.post(INGEST, json=body, headers=bearer("trcw_" + "y" * 43)).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    assert e.c.post(INGEST, json=body, headers=bearer(tok)).status_code == 200      # same IP, valid token
    assert e.c.post(INGEST, json=body, headers=bearer(tok)).status_code == 200
    assert e.c.post(INGEST, json=body, headers=bearer(tok)).status_code == 200
    r = e.c.post(INGEST, json=body, headers=bearer(tok))
    assert r.status_code == 429 and r.json()["detail"]["code"] == "RATE_LIMITED"


# ---------------------------------------------------------------------------- the nightly choice (pure)

def _w(d, kg, src=WS.AUTO):
    return P.Weight(d, kg, src)


def _make(d, kg):
    return P.Weight(d, kg, WS.AUTO)


def _days(**kv):
    return {dt.date.fromisoformat(k.replace("_", "-")[1:]): v for k, v in kv.items()}


def test_choose_adds_on_half_a_kilo_or_a_week_and_never_touches_the_users_own_entry():
    manual = P.Weight("2026-09-01", 74.0)                       # 設定頁 (no source)
    # < 0.5 kg within 7 days of the previous entry: skipped; ≥ 0.5 kg: added; ≥ 7 days: added
    out, dec = WS.choose([manual], {dt.date(2026, 9, 2): 73.6, dt.date(2026, 9, 3): 73.5,
                                    dt.date(2026, 9, 5): 73.4, dt.date(2026, 9, 10): 73.3}, _make)
    assert [(d["day"], d["action"], d["reason"]) for d in dec] == [
        ("2026-09-02", "skipped", "small"), ("2026-09-03", "added", "diff"),
        ("2026-09-05", "skipped", "small"), ("2026-09-10", "added", "gap")]
    assert [(w.date, w.kg, w.source) for w in out] == [("2026-09-01", 74.0, None), ("2026-09-03", 73.5, WS.AUTO),
                                                       ("2026-09-10", 73.3, WS.AUTO)]
    assert out[0] is manual
    # 0.49 kg is not 0.5
    _o, dec = WS.choose([manual], {dt.date(2026, 9, 2): 73.51}, _make)
    assert dec[0]["action"] == "skipped"
    # the user's own entry first: measurements on / before its day are not applied, it is never changed
    entries = [_w("2026-09-01", 74.0), P.Weight("2026-09-05", 72.0)]          # auto, then manual on 9/5
    out, dec = WS.choose(entries, {dt.date(2026, 9, 3): 75.0, dt.date(2026, 9, 5): 75.0,
                                   dt.date(2026, 9, 6): 72.2, dt.date(2026, 9, 7): 73.0}, _make)
    assert [(d["day"], d["action"], d["reason"]) for d in dec] == [
        ("2026-09-03", "skipped", "manual"), ("2026-09-05", "skipped", "manual"),
        ("2026-09-06", "skipped", "small"), ("2026-09-07", "added", "diff")]
    assert [(w.date, w.kg) for w in out] == [("2026-09-01", 74.0), ("2026-09-05", 72.0), ("2026-09-07", 73.0)]
    # an accepted COROS weight counts as the user's own too
    _o, dec = WS.choose([P.Weight("2026-09-05", 72.0, "coros")], {dt.date(2026, 9, 5): 80.0}, _make)
    assert dec[0]["reason"] == "manual"
    # an automatic entry of the same day takes the day's last value (no threshold)
    out, dec = WS.choose([manual, _w("2026-09-03", 73.5)], {dt.date(2026, 9, 3): 73.3}, _make)
    assert dec[0]["action"] == "replaced" and [(w.date, w.kg) for w in out][-1] == ("2026-09-03", 73.3)
    # no entry at all: the first value is added
    out, dec = WS.choose([], {dt.date(2026, 9, 3): 73.3}, _make)
    assert dec[0]["reason"] == "first" and out[0].kg == 73.3


def test_the_day_takes_its_last_measurement():
    class R:
        def __init__(self, i, day, hh, kg, rec=0):
            self.id, self.day, self.kg = i, dt.date.fromisoformat(day), kg
            self.measured_at = dt.datetime(2026, 9, 1, hh)
            self.received_at = dt.datetime(2026, 9, 2, rec)
    rows = [R(1, "2026-09-01", 7, 73.0), R(2, "2026-09-01", 21, 73.8), R(3, "2026-09-01", 12, 73.5),
            R(4, "2026-09-02", 7, 72.0)]
    last = WS.last_per_day(rows)
    assert {d.isoformat(): r.id for d, r in last.items()} == {"2026-09-01": 2, "2026-09-02": 4}


# ---------------------------------------------------------------------------- the nightly apply

def _at(day: str, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime.fromisoformat(day).replace(hour=hh, minute=mm, tzinfo=TPE)


def test_the_apply_runs_once_a_night_writes_only_what_passes_and_only_then_recomputes(e, monkeypatch):
    P.Plan(weights=[P.Weight("2026-09-01", 74.0)]).save()
    tok = e.token()
    send = lambda b: e.c.post(INGEST, json=b, headers=bearer(tok))        # noqa: E731
    send({"measurements": [shortcut(73.0, "2026-10-12T07:00:00+08:00"), shortcut(73.6, "2026-10-12T21:00:00+08:00"),
                           shortcut(73.4, "2026-10-12T12:00:00+08:00")]})
    # not yet night / outside the window: nothing
    assert e.tick(_at("2026-10-12", 23, 59)) is None and e.tick(_at("2026-10-13", 2, 59)) is None
    assert e.tick(_at("2026-10-13", 6, 0)) is None
    assert e.notified == []
    # busy: a sync of the tenant is running → wait
    from backend.sync import runner
    monkeypatch.setattr(runner, "is_busy", lambda s: s == "coros")
    assert e.tick(_at("2026-10-13", 3, 0)) is None
    monkeypatch.setattr(runner, "is_busy", lambda s: False)
    res = e.tick(_at("2026-10-13", 3, 1))
    assert res["changed"] and [(d["day"], d["kg"], d["action"]) for d in res["decisions"]] == [
        ("2026-10-12", 73.6, "added")]                                           # the day's LAST value
    plan = P.Plan.load()
    assert [(w.date, w.kg, w.source) for w in plan.weights] == [("2026-09-01", 74.0, None),
                                                                 ("2026-10-12", 73.6, "apple_health")]
    assert e.notified == [1]                                                      # recompute after the write
    st = {(r.kg, r.status, r.note) for r in e.rows()}
    assert st == {(73.6, "applied", "gap"), (73.0, "superseded", None), (73.4, "superseded", None)}  # 0.4 kg, 6 weeks later
    # once a night
    assert e.tick(_at("2026-10-13", 3, 30)) is None and e.notified == [1]
    # the next night with no new measurement: nothing read but the query, nothing written
    stamp = e.plan_path.stat().st_mtime_ns
    res = e.tick(_at("2026-10-14", 3, 0))
    assert res == {"status": "nothing", "changed": False, "decisions": []}
    assert e.plan_path.stat().st_mtime_ns == stamp and e.notified == [1]
    # a small change: kept as a measurement, no entry, no recompute
    e.clock[0] = _at("2026-10-14", 8, 0)
    send(shortcut(73.4, "2026-10-14T07:00:00+08:00"))
    res = e.tick(_at("2026-10-15", 3, 0))
    assert not res["changed"] and res["decisions"][0]["reason"] == "small" and e.notified == [1]
    assert e.plan_path.stat().st_mtime_ns == stamp
    assert e.c.get(f"{API}/status").json()["state"]["status"] == "unchanged"


def test_a_weight_typed_on_the_settings_page_wins_over_the_measurements(e, monkeypatch):
    from backend.api import plan as plan_api
    P.Plan(weights=[P.Weight("2026-09-01", 74.0), P.Weight("2026-10-12", 73.6, "apple_health")]).save()
    monkeypatch.setattr(plan_api, "_notify", lambda t: None)
    # the 設定頁 sends the list back: the unchanged automatic entry keeps its source, the new one is manual
    app = FastAPI()
    app.include_router(plan_api.router)
    c = TestClient(app)
    r = c.put("/api/v1/plan/profile", json={"weights": [{"date": "2026-09-01", "kg": 74.0},
                                                       {"date": "2026-10-12", "kg": 73.6},
                                                       {"date": "2026-10-14", "kg": 71.0}]})
    assert r.status_code == 200, r.text
    assert [(w.date, w.source) for w in P.Plan.load().weights] == [
        ("2026-09-01", None), ("2026-10-12", "apple_health"), ("2026-10-14", None)]
    tok = e.token()
    e.clock[0] = _at("2026-10-16", 22, 0)
    e.c.post(INGEST, json={"measurements": [shortcut(75.0, "2026-10-14T21:00:00+08:00"),
                                            shortcut(71.2, "2026-10-15T07:00:00+08:00"),
                                            shortcut(72.0, "2026-10-16T07:00:00+08:00")]}, headers=bearer(tok))
    res = e.tick(_at("2026-10-17", 3, 0))
    assert [(d["day"], d["action"], d["reason"]) for d in res["decisions"]] == [
        ("2026-10-14", "skipped", "manual"), ("2026-10-15", "skipped", "small"), ("2026-10-16", "added", "diff")]
    assert [(w.date, w.kg) for w in P.Plan.load().weights][-2:] == [("2026-10-14", 71.0), ("2026-10-16", 72.0)]


def test_plan_json_without_sources_reads_and_writes_back_the_same_and_keeps_the_estimate_key(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    raw = {"events": [], "phases": [], "thresholds": [], "weights": [{"date": "2026-01-10", "kg": 74.0}], "profile": {}}
    (tmp_path / "plan.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), "utf-8")
    before = (tmp_path / "plan.json").read_bytes()
    p = P.Plan.load()
    p.save()
    assert (tmp_path / "plan.json").read_bytes() == before
    # the source is not part of repr: the estimate cache key (fitdataset._est_inputs) does not move
    assert repr(P.Plan(weights=[P.Weight("2026-01-10", 74.0, "apple_health")])) == repr(p)


# ---------------------------------------------------------------------------- COROS account weight

def _coros(e, kg, day="2026-10-10"):
    async def go():
        e.db.add(AthleteSettings(athlete_id=1, effective_date=dt.date.fromisoformat(day), weight_kg=kg))
        await e.db.commit()
    run(go())


def test_the_coros_weight_is_only_a_hint_until_the_user_takes_it(e, monkeypatch):
    P.Plan(weights=[P.Weight("2026-09-01", 74.0)]).save()
    _coros(e, 74.8)                                     # < 1 kg: no hint
    assert e.c.get(f"{API}/coros-hint").json() == {"hint": None}
    _coros(e, 76.0, "2026-10-12")                       # ≥ 1 kg: 要更新嗎？
    h = e.c.get(f"{API}/coros-hint").json()["hint"]
    assert h == {"kg": 76.0, "date": "2026-10-12", "current_kg": 74.0, "current_date": "2026-09-01", "diff": 2.0}
    assert e.c.get(f"{API}/status").json()["coros_hint"]["kg"] == 76.0
    # never written by itself: not by the hint, nor by the nightly apply
    tok = e.token()
    e.c.post(INGEST, json=shortcut(74.1, "2026-09-03T07:00:00+08:00"), headers=bearer(tok))  # a small change
    e.tick(_at("2026-10-13", 3, 0))
    assert [w.kg for w in P.Plan.load().weights] == [74.0] and e.notified == []
    # 不用: hidden for that value; a new COROS value asks again
    assert e.c.post(f"{API}/coros/dismiss", json={"kg": 76.0}).status_code == 200
    assert e.c.get(f"{API}/coros-hint").json()["hint"] is None
    _coros(e, 77.0, "2026-10-13")
    assert e.c.get(f"{API}/coros-hint").json()["hint"]["kg"] == 77.0
    # a stale value is refused; 更新 writes today's entry (source coros) and recomputes like a 設定頁 save
    assert e.c.post(f"{API}/coros/accept", json={"kg": 76.0}).status_code == 409
    from backend.engine import localtime
    monkeypatch.setattr(localtime, "today_local", lambda *a: dt.date(2026, 10, 13))
    r = e.c.post(f"{API}/coros/accept", json={"kg": 77.0})
    assert r.status_code == 200 and e.notified == [1]
    assert [(w.date, w.kg, w.source) for w in P.Plan.load().weights][-1] == ("2026-10-13", 77.0, "coros")
    assert e.c.get(f"{API}/coros-hint").json()["hint"] is None
    # no hint without any entry (the first-run 精靈 offers the COROS weight then)
    P.Plan().save()
    assert e.c.get(f"{API}/coros-hint").json()["hint"] is None


# ---------------------------------------------------------------------------- data registry

def test_measurements_are_the_users_data_and_tokens_stay_on_the_server():
    from backend import data_registry as R
    m, t = R.table("weight_measurements"), R.table("weight_tokens")
    assert m.cls == R.USER and m in R.export_entries()[0]               # 下載我的資料 / backup
    assert t.cls == R.SECRET and t.local and "token_hash" in t.secret_fields and t not in R.export_entries()[0]


# ---------------------------------------------------------------------------- sign-in mode (SP-58)

from backend.tests.test_login_sp58 import (OWNER_EMAIL, app, browser, c, csrf, env, sign_in,  # noqa: E402,F401
                                           sign_up, started)


def _weight_token(cl) -> str:
    r = cl.post(f"{API}/tokens", json={"name": "iPhone"}, headers={"X-TRC-CSRF": csrf(cl)})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_signin_mode_the_token_opens_only_that_post_in_its_users_own_tenant(app, c):
    WA.reset_limits()
    sign_up(c, "g-alice", "alice@example.com")
    tok = _weight_token(c)
    phone = browser(app)                                   # no cookie at all, no CSRF header
    body = shortcut(61.5, "2026-10-13T07:00:00+08:00")
    r = phone.post(INGEST, json=body, headers=bearer(tok))
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1
    # the token opens nothing else: reads and other writes still want a session
    assert phone.get(f"{API}/status", headers=bearer(tok)).status_code == 401
    assert phone.get("/api/v1/plan", headers=bearer(tok)).status_code == 401
    assert phone.post(f"{API}/tokens", json={}, headers=bearer(tok)).status_code == 401
    assert phone.put("/api/v1/plan/events", json={"name": "x", "date": "2027-01-01"}, headers=bearer(tok)).status_code == 401
    # another person's view: Alice's measurement is in Alice's tenant only
    assert [m["kg"] for m in c.get(f"{API}/status").json()["measurements"]] == [61.5]
    o = browser(app)
    sign_in(o, "g-owner", OWNER_EMAIL)
    assert o.get(f"{API}/status").json()["measurements"] == []
    # the owner's own token writes into the owner's tenant
    otok = _weight_token(o)
    assert phone.post(INGEST, json=shortcut(74.0, "2026-10-13T07:00:00+08:00"), headers=bearer(otok)).status_code == 200
    assert [m["kg"] for m in o.get(f"{API}/status").json()["measurements"]] == [74.0]
    assert [m["kg"] for m in c.get(f"{API}/status").json()["measurements"]] == [61.5]
    # unknown / revoked → 401; a cookie-only POST is not let through by the token path (CSRF as before)
    assert phone.post(INGEST, json=body, headers=bearer("trcw_" + "z" * 43)).status_code == 401
    tid = c.get(f"{API}/status").json()["tokens"][0]["id"]
    assert c.delete(f"{API}/tokens/{tid}", headers={"X-TRC-CSRF": csrf(c)}).json()["revoked"] == 1
    assert phone.post(INGEST, json=body, headers=bearer(tok)).status_code == 401
    r = c.post(INGEST, json=body)                         # signed in, no token, no CSRF header
    assert r.status_code == 403 and r.json()["code"] == "CSRF"
    WA.reset_limits()


# ---------------------------------------------------------------------------- the scheduler runs it

def test_the_scheduler_loop_runs_the_nightly_apply_in_every_tenant(monkeypatch):
    import asyncio
    from backend import tenancy
    from backend.accounts import tenants as TN
    from backend.engine import fit_archive
    from backend.sync import check, purge, scheduler
    seen = []

    async def nothing(*a, **k):
        return None

    async def weight_tick(sf=None, now=None):
        seen.append(tenancy.current().id)

    async def stop(_s):
        raise asyncio.CancelledError
    monkeypatch.setattr(scheduler, "tick_all", nothing)
    monkeypatch.setattr(check, "STARTUP_DELAY_S", 0)
    monkeypatch.setattr(check, "weekly_tick", nothing)
    monkeypatch.setattr(purge, "cleanup_tick", nothing)
    monkeypatch.setattr(fit_archive, "tick_async", nothing)
    monkeypatch.setattr(W, "tick", weight_tick)
    monkeypatch.setattr(TN, "sync_tenants", lambda: [tenancy.owner()])
    monkeypatch.setenv("WKO5COACH_NO_AUTO_BACKUP", "1")
    from backend.api import rain_backfill
    monkeypatch.setattr(rain_backfill, "tick", nothing)
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        run(scheduler.loop(session_factory=lambda: None))
    assert seen == ["owner"]
