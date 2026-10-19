"""SP-713: the quota rules — the calculator counts one route a day across every entry that computes
a plan, a back-test already running costs nothing, single-activity analysis follows the charts'
90 days (the overview's totals don't), the free tier keeps 1 year (told first), the plan table has
no AI chat and offline:full is checked, an admin can give one user extra uses this month."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import membership as MS, site_config as SC, site_settings as SS, tenancy
from backend.tests.test_membership_sp330 import _user, server  # noqa: F401 — fixtures

UTC = dt.timezone.utc
F, P = {"X-Test-Tenant": "free"}, {"X-Test-Tenant": "paid"}
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_site(monkeypatch):
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    SS.reset_cache()
    yield
    SS.reset_cache()


@pytest.fixture
def client(server, monkeypatch):  # noqa: F811
    """The app with a header picking the request's tenant (test_admin_sp351's client); no header =
    the owner (the admin). Multi-user data on."""
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.accounts import tenants as TN
    app_ = build_app(demo=False)
    who = {"free": _user(server), "paid": _user(server, "subscriber")}

    async def bind(scope, receive, send):
        name = dict(scope.get("headers") or []).get(b"x-test-tenant", b"").decode()
        t = who.get(name)
        if scope["type"] == "http" and t is not None:
            await TN.ensure_ready(t)
            tok = tenancy.set_current(t)
            try:
                return await app_(scope, receive, send)
            finally:
                tenancy.reset(tok)
        return await app_(scope, receive, send)
    with TestClient(bind, raise_server_exceptions=False) as cl:
        cl.who = who
        yield cl


def _used(client, h=F, key="calc"):
    return client.get("/api/v1/session", headers=h).json()["membership"]["quotas"][key]["used"]


# ---------------------------------------------------------------------------- 1. one route a day

def test_the_route_is_the_gpx_then_the_event_then_the_typed_course():
    from backend.engine.racepower.calc import PlanIn
    gpx = PlanIn(course={"course_id": "abc", "event_id": "e1"}, event_id="e2")
    assert MS.calc_route(gpx) == "gpx:abc"
    assert MS.calc_route(PlanIn(course={"event_id": "e1"})) == "event:e1"
    assert MS.calc_route(PlanIn(distance_km=10, event_id="e2")) == "event:e2"
    a, b = PlanIn(distance_km=42.195, gain_m=300), PlanIn(distance_km=42.195, gain_m=300, target_time_s=9000)
    assert MS.calc_route(a) == MS.calc_route(b)                     # other inputs: the same route
    assert MS.calc_route(a) != MS.calc_route(PlanIn(distance_km=21.1, gain_m=300))
    assert MS.calc_route(PlanIn(course={"manual": {"km": 50, "gain": 2000}})).startswith("typed:")


def test_a_free_user_pays_once_a_route_a_day_whatever_the_entry(server):  # noqa: F811
    from backend.engine.racepower.calc import PlanIn
    free = _user(server)
    day = dt.datetime(2026, 10, 12, 4, tzinfo=UTC)
    with tenancy.use(free):
        r1 = PlanIn(course={"course_id": "gpx1"})
        for _ in range(20):                                          # the page re-plans after every edit
            MS.use_calc(r1, now=day)
        assert MS.usage("calc", now=day)["used"] == 1
        MS.use_calc(PlanIn(course={"course_id": "gpx2"}), now=day)
        assert MS.usage("calc", now=day)["used"] == 2
        MS.use_calc(r1, now=day + dt.timedelta(days=1))              # the same route the next day
        assert MS.usage("calc", now=day)["used"] == 3
        for i in range(3, 10):
            MS.use_calc(PlanIn(course={"course_id": f"g{i}"}), now=day)
        MS.use_calc(r1, now=day)                                     # seen today: still fine at the limit
        with pytest.raises(HTTPException) as ei:
            MS.use_calc(PlanIn(course={"course_id": "one-more"}), now=day)
        assert ei.value.status_code == 429


def test_plan_share_export_predict_and_estimate_share_one_count(client, monkeypatch):  # noqa: F811
    from backend.api import racepower as RP
    from backend.engine.racepower import calc as CALC
    from backend.engine.racepower import race_estimate as RX

    def boom(body):                                                  # the count happens before the work
        raise HTTPException(418, "computed")
    monkeypatch.setattr(RP, "make_plan", lambda body: {"ok": True})
    monkeypatch.setattr(CALC, "predict_response", lambda live, body: {"ok": True})
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: {})
    monkeypatch.setattr(RX, "has_cp", lambda inp, body: True)
    monkeypatch.setattr(RP, "_event", lambda eid: SimpleNamespace(date="2026-11-01", name="賽", days=1, id=eid))
    route = {"type": "trail", "distance_km": 30, "gain_m": 1500}
    for _ in range(5):
        assert client.post("/api/v1/racepower/plan", json={**route, "target_time_s": 9000 + _}, headers=F).status_code == 200
    assert client.post("/api/v1/racepower/predict", json=route, headers=F).status_code == 200
    assert client.post("/api/v1/racepower/estimate", json=route, headers=F).json() == {"available": False, "model": True}
    monkeypatch.setattr(RP, "make_plan", boom)
    assert client.post("/api/v1/racepower/share", json=route, headers=F).status_code == 418
    assert _used(client) == 1                                        # one route, one use
    other = {"type": "trail", "distance_km": 50, "gain_m": 3000}
    assert client.post("/api/v1/racepower/share", json=other, headers=F).status_code == 418
    assert _used(client) == 2                                        # /share counts a new route
    ex = {"type": "road", "distance_km": 10, "event_id": "e9"}
    assert client.post("/api/v1/racepower/export/plan", json=ex, headers=F).status_code == 418
    assert _used(client) == 3                                        # /export/plan too
    assert client.post("/api/v1/racepower/predict", json={"type": "road", "distance_km": 5}, headers=F).status_code == 200
    assert client.post("/api/v1/racepower/estimate", json={"type": "road", "distance_km": 7}, headers=F).status_code == 200
    assert _used(client) == 5                                        # /predict and /estimate too
    # a subscriber: unlimited, nothing counted
    monkeypatch.setattr(RP, "make_plan", lambda body: {"ok": True})
    assert client.post("/api/v1/racepower/plan", json=route, headers=P).status_code == 200


# ---------------------------------------------------------------------------- 2. a back-test already running

def test_a_backtest_already_running_or_queued_costs_nothing(client, monkeypatch):  # noqa: F811
    from backend.engine.racepower import backtest as BT
    state = {"running": True, "queued": False}
    monkeypatch.setattr(BT, "state", lambda: dict(state))
    started = []
    monkeypatch.setattr(BT, "start_background", lambda ds: started.append(1) or True)
    r = client.post("/api/v1/racepower/backtest/run", headers=P)
    assert r.status_code == 200 and r.json()["started"] is False and r.json()["already"] is True
    assert r.json()["message"] == "已經在算" and started == []
    state.update(running=False, queued=True)
    assert client.post("/api/v1/racepower/backtest/run", headers=P).json()["already"] is True
    assert _used(client, P, "backtest") == 0                         # neither click counted
    state.update(queued=False)
    assert client.post("/api/v1/racepower/backtest/run", headers=P).json()["started"] is True
    assert _used(client, P, "backtest") == 1
    # a concurrent click queued it first: start_background says no, the use is given back
    monkeypatch.setattr(BT, "start_background", lambda ds: False)
    assert client.post("/api/v1/racepower/backtest/run", headers=P).json()["already"] is True
    assert _used(client, P, "backtest") == 1


# ---------------------------------------------------------------------------- 3. 90 days

class _W(SimpleNamespace):
    pass


def _ds(days_back):
    ws = [_W(day=1000.0 - d, idx=i) for i, d in enumerate(days_back)]
    return SimpleNamespace(today=1000.0, workouts=ws)


def test_single_activity_analysis_follows_the_charts_range(server):  # noqa: F811
    free, paid = _user(server), _user(server, "subscriber")
    ds = _ds([10, 89, 120, 800])
    with tenancy.use(free):
        MS.require_workout_age(ds, ds.workouts[0])
        MS.require_workout_age(ds, ds.workouts[1])
        for w in ds.workouts[2:]:
            with pytest.raises(HTTPException) as ei:
                MS.require_workout_age(ds, w)
            d = ei.value.detail
            assert ei.value.status_code == 403 and d["code"] == "SCOPE_REQUIRED" and d["upgrade"] is True
    with tenancy.use(paid):
        for w in ds.workouts:
            MS.require_workout_age(ds, w)


def test_review_and_compare_refuse_old_activities_and_the_overview_shows_all(client, monkeypatch):  # noqa: F811
    from backend.api import activity_compare as ACA, overview as OVA, wko5views as WV
    from backend.engine import workout_review as WR
    ds = _ds([10, 200])
    monkeypatch.setattr(WV, "_dataset", lambda parity=None, source=None: ds)
    monkeypatch.setattr(WR, "review", lambda ds_, w, s, basis="pace": {"ok": True})
    assert client.get("/api/v1/app/workouts/0/review?section=summary", headers=F).status_code == 200
    r = client.get("/api/v1/app/workouts/1/review?section=summary", headers=F)
    assert r.status_code == 403 and r.json()["detail"]["upgrade"] is True
    assert client.get("/api/v1/app/workouts/1/review?section=summary", headers=P).status_code == 200
    monkeypatch.setattr(ACA, "_ds", lambda: ds)
    r = client.get("/api/v1/app/activity-compare?a=0&b=1", headers=F)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "SCOPE_REQUIRED"
    # the overview's week / month totals: every period, for the free tier too
    seen = {}

    def summary(ds_, unit, anchor, n, detail=True):
        seen["n"] = n
        return {"buckets": []}
    monkeypatch.setattr(OVA, "_dataset", lambda: SimpleNamespace(today=OVA.O.date_to_day(dt.date(2026, 10, 19))))
    monkeypatch.setattr(OVA.O, "summary", summary)
    assert client.get("/api/v1/overview/summary?unit=month&n=60", headers=F).status_code == 200
    assert seen["n"] == 60                                           # five years of months, not cut


# ---------------------------------------------------------------------------- 4. retention by plan

def test_the_free_tier_keeps_one_year_and_is_told_before_a_longer_one_is_cut(server, monkeypatch, tmp_path):  # noqa: F811
    from backend.engine import fit_archive as FA
    import backend.engine.wko5expr.datasource as DS
    monkeypatch.setattr(FA, "_tz", lambda: dt.timezone(dt.timedelta(hours=8)))
    free, paid = _user(server), _user(server, "subscriber")
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, *a: 5 if k == FA.RETENTION_KEY else d)
    with tenancy.use(paid):
        assert FA.plan_cap() is None and FA.choices() == (2, 3, 5, 10) and FA._years() == 5
    with tenancy.use(free):
        monkeypatch.setattr(FA, "LOG_PATH", tmp_path / "free_log.json")
        assert FA.plan_cap() == 1 and FA.choices() == (1,)
        seen = dt.datetime(2026, 10, 19, 4, tzinfo=UTC)
        n = FA.cap_notice(seen)
        assert n == {"years": 1, "was": 5, "since": "2026-10-19", "from": "2026-12-01"}   # ≥ 28 days, a 1st
        assert FA._years(dt.datetime(2026, 11, 1, 0, tzinfo=UTC)) == 5      # November's run: not yet
        assert FA._years(dt.datetime(2026, 12, 1, 0, tzinfo=UTC)) == 1      # told since October: 1 year
        assert FA.cap_notice(dt.datetime(2026, 11, 5, tzinfo=UTC))["since"] == "2026-10-19"   # not moved
    # an upgrade drops the notice; the person's own choice is still there
    server.set_plan(int(free.id[1:]), "subscriber")
    with tenancy.use(free):
        assert FA._years(dt.datetime(2026, 12, 1, tzinfo=UTC)) == 5
        assert "cap_notice" not in FA._read_log()


def test_the_settings_api_offers_the_free_tier_one_year_only(client, monkeypatch):  # noqa: F811
    s = client.get("/api/v1/archive/status", headers=F).json()
    assert s["retention_years"] == 1 and s["choices"] == [1] and s["cap"] == 1
    assert s["cap_notice"]["years"] == 1 and s["cap_notice"]["was"] == 3     # the site's default was 3
    assert client.put("/api/v1/archive/settings", json={"retention_years": 1}, headers=F).status_code == 200
    r = client.put("/api/v1/archive/settings", json={"retention_years": 3}, headers=F)
    assert r.status_code == 403 and r.json()["detail"]["scope"] == "history:full" and r.json()["detail"]["upgrade"]
    assert client.put("/api/v1/archive/settings", json={"retention_years": 4}, headers=F).status_code == 400
    p = client.get("/api/v1/archive/status", headers=P).json()
    assert p["choices"] == [2, 3, 5, 10] and "cap" not in p
    r = client.put("/api/v1/archive/settings", json={"retention_years": 10}, headers=P)
    assert r.status_code == 200 and r.json()["retention_years"] == 10
    assert client.put("/api/v1/archive/settings", json={"retention_years": 1}, headers=P).status_code == 400


def test_the_settings_page_shows_the_cap_and_the_notice_in_both_languages():
    html = (ROOT / "backend" / "static" / "settings.html").read_text("utf-8")
    assert 'id="ar-cap"' in html and 'id="ar-cap-notice"' in html and "settings.arch.cap_notice" in html
    for lang in ("zh-TW", "en"):
        d = json.loads((ROOT / "backend" / "static" / "i18n" / lang / "settings.json").read_text("utf-8"))
        for k in ("arch.year_one", "arch.free_cap", "arch.cap_notice"):
            assert d.get(k), (lang, k)
        assert "{from}" in d["arch.cap_notice"] and "{was}" in d["arch.cap_notice"]


# ---------------------------------------------------------------------------- 5. the plan table, offline:full

def test_no_ai_chat_in_the_plan_table_or_the_plans_page():
    assert "ai:chat" not in MS.SCOPES
    assert not any("ai:chat" in s for s in MS.ROLE_SCOPES.values())
    assert not any("AI" in row[0] for row in MS.PLAN_TABLE)
    for p in (ROOT / "backend" / "static" / "plans.html", ROOT / "docs" / "plans" / "membership.md",
              ROOT / "backend" / "static" / "i18n" / "zh-TW" / "plans.json",
              ROOT / "backend" / "static" / "i18n" / "en" / "plans.json"):
        text = p.read_text("utf-8")
        assert "AI 對話" not in text and "ai:chat" not in text and "AI chat" not in text, p


def test_offline_full_decides_the_tiers_the_device_keeps():
    from backend.tests.test_offline_pwa import NODE, run
    if NODE is None:
        pytest.skip("node not installed")
    out = run(r"""
const S = (scopes) => ({ mode: "user", user: { id: 7 }, login: { required: true, signed_in: true },
  membership: scopes == null ? undefined : { role: "user", scopes } });
OUT = { rules: [O.tiersOf(S(["plan:view"])), O.tiersOf(S(["plan:view", "offline:full"])), O.tiersOf(S(null)),
  O.tiersOf({ mode: "owner", caps: [] })] };
person("alice", 7);
let scopes = ["plan:view", "offline:full"];
server.routes["/api/v1/session"] = () => json(S(scopes));
const E = mk();
OUT.paid = (await E.sync({})).done;
OUT.p2paid = caches.urls("trc-u-u7-p2");
scopes = ["plan:view"];                    // the subscription ended
clock += 25 * 3600e3;
OUT.free = (await E.sync({})).done;
OUT.p2free = caches.urls("trc-u-u7-p2");
OUT.p1free = (caches.urls("trc-u-u7-p1") || []).length;
""")
    full, spare = ["p0", "p1", "p2"], ["p0", "p1"]
    assert out["rules"] == [spare, full, full, full]      # no membership (the owner's app): as before
    assert out["paid"] == full and out["p2paid"] == []
    assert out["free"] == spare and out["p2free"] is None and out["p1free"] == 4


# ---------------------------------------------------------------------------- 6. 本月額外次數

def test_extra_uses_this_month_raise_the_limit_this_month_only(server):  # noqa: F811
    free = _user(server)
    uid = int(free.id[1:])
    oct_ = dt.datetime(2026, 10, 12, 4, tzinfo=UTC)
    server.quota_extra_set(uid, "2026-10", 5)
    with tenancy.use(free):
        u = MS.usage("calc", now=oct_)
        assert u["limit"] == 15 and u["extra"] == 5
        for i in range(15):
            MS.consume("calc", item=f"r{i}", now=oct_)
        with pytest.raises(HTTPException):
            MS.consume("calc", item="r15", now=oct_)
        assert MS.usage("calc", now=dt.datetime(2026, 11, 2, tzinfo=UTC))["limit"] == 10   # next month: none
        assert MS.usage("charts", now=oct_)["limit"] == 5 and "extra" not in MS.usage("charts", now=oct_)
    with pytest.raises(ValueError):
        server.quota_extra_set(uid, "2026-10", -1)


def test_the_admin_sets_a_users_extra_uses_on_their_row(client):  # noqa: F811
    uid = int(client.who["free"].id[1:])
    r = client.put(f"/api/v1/admin/users/{uid}", json={"extra_this_month": 3})
    assert r.status_code == 200 and r.json()["extra_this_month"] == 3
    assert r.json()["extra_month"] == MS.month_key() and r.json()["extra_quotas"] == ["calc"]
    assert _used(client) == 0
    q = client.get("/api/v1/session", headers=F).json()["membership"]["quotas"]["calc"]
    assert q["limit"] == SC.get("quota.free.calc") + 3 and q["extra"] == 3
    rows = client.get("/api/v1/admin/users").json()["users"]
    assert next(u for u in rows if u["id"] == uid)["extra_this_month"] == 3
    assert "extra_this_month" not in next(u for u in rows if u["owner"])
    assert client.put(f"/api/v1/admin/users/{uid}", json={"extra_this_month": -1}).status_code == 400
    assert client.put("/api/v1/admin/users/1", json={"extra_this_month": 2}).status_code == 400
    assert client.put(f"/api/v1/admin/users/{uid}", json={"extra_this_month": 0}).json()["extra_this_month"] == 0


def test_the_admin_page_has_the_extra_field_in_both_languages():
    html = (ROOT / "backend" / "static" / "admin.html").read_text("utf-8")
    assert "data-extra" in html and "extra_this_month" in html
    for lang in ("zh-TW", "en"):
        d = json.loads((ROOT / "backend" / "static" / "i18n" / lang / "admin.json").read_text("utf-8"))
        for k in ("extra.label", "extra.help", "audit.user.quota_extra"):
            assert d.get(k), (lang, k)
