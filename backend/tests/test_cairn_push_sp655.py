"""SP-655 (SP-570 TRC-5, the "push" half of SP-538): 賽事頁「推到 Cairn」.

A season-plan event with a stored GPX and a saved calculation is pushed to 疊石 Cairn's internal
API (SP-657): the first push creates a trip (POST /api/internal/trc/trips?sub=), the trip id Cairn
answers is kept with the event, every later push updates that trip (PUT /trips/<id>) instead of
creating another. What goes: the GPX, the nights as points with their days (visits), and
pace_config = the cairn-curve (SP-653) — never an `eta`, never a Cairn user token (none exists:
the shared secret and the person's Google sub are the whole identity).

Who may: signed in, linked with Cairn (SP-329), the feature link:gpx_sync switched on (each feature
asks on its own, SP-654's links.FEATURES) and a paid plan here. Cairn's own refusals (403 its free
plan, 429 today's pushes used up, 404 no Cairn account / the trip deleted there) become plain hints.

Cairn is a fake (httpx MockTransport): nothing here reaches the network or the real Cairn."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from backend.tests.test_cairn_curve_sp653 import GPX, _baiyue_result, _trail_result
from backend.tests.test_link_curves_sp654 import _allow, _subscribe
from backend.tests.test_link_sp329 import (  # noqa: F401 — fixtures used by name
    CAIRN_URL, SECRET, _limits, _link, _uid, app, browser, c, csrf, env, sign_up, started,
)

LINKS_URL = "/api/v1/account/links"
SCOPE = "link:gpx_sync"


def push_url(eid: str) -> str:
    return f"{LINKS_URL}/cairn/events/{eid}/push"


def status_url(eid: str) -> str:
    return f"{LINKS_URL}/cairn/events/{eid}"


# ---------------------------------------------------------------------------- a fake Cairn

class FakeCairn:
    """Cairn's internal API for TrailRunCoach (SP-657, v2/server/src/routes/trc.ts): the shared
    secret, ?sub=, POST /trips → 201 {ok, data: {trip}}, PUT /trips/<id> → 200; its strict body
    (no eta anywhere); 403 a free plan, 429 the day's pushes used up, 404 no such person / trip."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.people = {"g-a": "basic", "g-b": "basic"}     # sub → plan
        self.trips: dict = {}                              # id → {sub, body}
        self.quota_left = 100
        self.down = False
        self.n = 0

    @staticmethod
    def err(status: int, code: str, message: str) -> httpx.Response:
        return httpx.Response(status, json={"ok": False, "error": code, "message": message})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.down:
            raise httpx.ConnectError("down")
        if request.headers.get("authorization") != f"Bearer {SECRET}":
            return self.err(401, "UNAUTHORIZED", "service secret invalid")
        path = request.url.path
        sub = request.url.params.get("sub")
        if path == "/api/internal/trc/account":
            if sub not in self.people:
                return self.err(404, "NOT_FOUND", "no cairn account for this sub")
            return httpx.Response(200, json={"ok": True, "sub": sub, "plan": self.people[sub], "plan_expires_at": None})
        if not path.startswith("/api/internal/trc/trips"):
            return self.err(404, "NOT_FOUND", "no route")
        if sub not in self.people:
            return self.err(404, "NOT_FOUND", "no cairn account for this sub")
        tid = path[len("/api/internal/trc/trips/"):] if path.startswith("/api/internal/trc/trips/") else None
        if request.method == "PUT":
            if tid not in self.trips or self.trips[tid]["sub"] != sub:
                return self.err(404, "NOT_FOUND", f"no gpx {tid}")
        elif request.method != "POST" or tid:
            return self.err(404, "NOT_FOUND", "no route")
        body = json.loads(request.content)
        bad = self.check(body, create=request.method == "POST")
        if bad:
            return self.err(400, "INVALID_INPUT", bad)
        if self.people[sub] == "free":
            return self.err(403, "FORBIDDEN", "這個方案沒有 TrailRunCoach 行程同步——升級到基本會員以上才能使用")
        if self.quota_left <= 0:
            return self.err(429, "RATE_LIMITED", "今日 TrailRunCoach 同步已達 20 次，明天再試")
        self.quota_left -= 1
        if request.method == "POST":
            self.n += 1
            tid = f"trip-{self.n}"
            self.trips[tid] = {"sub": sub, "body": body}
            status = 201
        else:
            self.trips[tid]["body"] = {**self.trips[tid]["body"], **body}
            status = 200
        return httpx.Response(status, json={"ok": True, "data": {"trip": {"id": tid, "name": body.get("name"),
                                                                          "source": "trailruncoach"}}})

    @staticmethod
    def check(body: dict, create: bool) -> str:
        """The parts of Cairn's strict schema this push uses (TrcTripCreateSchema, CairnCurveV1Schema)."""
        allowed = {"name", "gpx", "pace_config", "points"}
        if set(body) - allowed:
            return f"unknown keys {sorted(set(body) - allowed)}"
        if create and not (body.get("name") and body.get("gpx")):
            return "name and gpx required"
        if "<gpx" not in body.get("gpx", "<gpx"):
            return "not a gpx document"
        pc = body.get("pace_config")
        if pc is not None:
            if set(pc) != {"model"} or set(pc["model"]) != {"kind", "curve"} or pc["model"]["kind"] != "curve":
                return "pace_config shape"
            bad = cairn_curve_problem(pc["model"]["curve"])
            if bad:
                return f"curve: {bad}"
        for p in body.get("points") or []:
            if set(p) - {"ref", "name", "type", "distance_m", "lat", "lng", "visits"}:
                return "points: unknown keys"
            for v in p.get("visits") or []:
                if set(v) - {"day", "distance_m"}:
                    return "visits: unknown keys (eta?)"
        return ""


def cairn_curve_problem(cv) -> str:
    """Cairn's CairnCurveV1Schema (SP-656, v2/shared/src/cairn-curve.ts at 7c65153): passthrough
    (unknown fields kept), the fields it checks checked."""
    import re
    if not isinstance(cv, dict) or cv.get("v") != 1 or cv.get("source") != "trailruncoach":
        return "v / source"
    if not re.fullmatch(r"[0-9a-f]{64}", str(cv.get("gpx_sha256"))):
        return "gpx_sha256"
    if not (isinstance(cv.get("total_m"), (int, float)) and cv["total_m"] > 0):
        return "total_m"
    pts = cv.get("points")
    if not (isinstance(pts, list) and 2 <= len(pts) <= 2000) or list(pts[0]) != [0, 0]:
        return "points"
    if any(not (b[0] > a[0] and b[1] >= a[1]) for a, b in zip(pts, pts[1:])):
        return "points order"
    if len(cv.get("day_splits_km") or []) > 60:
        return "day_splits_km"
    st = cv.get("start_times") or []
    if len(st) > 61 or any(x is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", x) for x in st):
        return "start_times"
    fb = cv.get("fallback") or {}
    if fb.get("kind") != "munter-split" or not (fb.get("upEph", 0) > 0 and fb.get("downEph", 0) > 0):
        return "fallback"
    rh = cv.get("rest_hint")
    if rh is not None and not (isinstance(rh, dict) and isinstance(rh.get("basis"), str)
                               and all(isinstance(d.get("rest_s"), (int, float)) for d in rh.get("days") or [])):
        return "rest_hint"
    fc = cv.get("fallback_check")
    if fc is not None and not isinstance(fc, dict):
        return "fallback_check"
    return ""


@pytest.fixture
def cairn(monkeypatch):
    from backend.accounts import links as LK
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    monkeypatch.setenv("TRC_CAIRN_URL", CAIRN_URL)
    fake = FakeCairn()
    monkeypatch.setattr(LK, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def stores(monkeypatch):
    """The event GPX and the saved calculations in each tenant's own DB and folder."""
    from backend import tenancy
    from backend.engine import event_gpx as EG, race_calc_store as RC
    monkeypatch.setattr(RC, "_default_db", lambda: tenancy.db_path())
    monkeypatch.setattr(EG, "_default_db", lambda: tenancy.db_path())
    monkeypatch.setattr(EG, "ROOT", None)
    EG._memo.clear()
    yield
    EG._memo.clear()


def _as(sub: str):
    from backend import tenancy
    from backend.accounts import store, tenants as TN
    return tenancy.use(TN.tenant_of(store.get_user(_uid(sub))))


def _event(sub: str, eid: str = "race-1", name: str = "合歡北西峰 兩天", result=None, start: str = "05:00"):
    """A season-plan event with the GPX and a saved calculation on it, in `sub`'s tenant."""
    from backend.engine import event_gpx as EG, race_calc_store as RC
    from backend.engine.planning import Event, Plan
    with _as(sub):
        plan = Plan.load()
        plan.events = [e for e in plan.events if e.id != eid] + [Event(id=eid, name=name, date="2027-03-01",
                                                                         kind="baiyue", days=2)]
        plan.save()
        EG.save(eid, GPX, "route.gpx")
        RC.save(eid, {"form": {"start": start}}, result or _baiyue_result())


def _ready(cl, sub: str = "g-a", email: str = "alice@example.com"):
    sign_up(cl, sub, email)
    assert _link(cl).status_code == 200
    _subscribe(sub)
    assert _allow(cl, scope=SCOPE).status_code == 200


def _push(cl, eid: str = "race-1", **body):
    return cl.post(push_url(eid), json=body, headers={"X-TRC-CSRF": csrf(cl)})


def _keys(v, acc=None):
    acc = set() if acc is None else acc
    if isinstance(v, dict):
        for k, x in v.items():
            acc.add(k)
            _keys(x, acc)
    elif isinstance(v, list):
        for x in v:
            _keys(x, acc)
    return acc


# ---------------------------------------------------------------------------- what is sent

def test_pace_config_carries_the_curve_as_it_is():
    """One place decides pace_config's shape; the curve goes untouched (Cairn passthrough):
    start_times with nulls, rest_hint as an object, fallback_check all included."""
    from backend.accounts import cairn_push as CP
    from backend.engine.racepower import cairn_curve as CC
    full = CC.build(_baiyue_result(), GPX, start_times=["05:00", None], computed_at=None)
    pc = CP.pace_config(full)
    assert pc == {"model": {"kind": "curve", "curve": full}}
    assert pc["model"]["curve"] is full and "fallback_check" in full and isinstance(full["rest_hint"], dict)


def test_the_payload_route_nights_and_curve(stores, monkeypatch, tmp_path):
    from backend.accounts import cairn_push as CP
    from backend.engine.planning import Event, Plan
    from backend.engine import event_gpx as EG, race_calc_store as RC
    monkeypatch.setattr(RC, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    from backend.engine import planning
    monkeypatch.setattr(planning, "PLAN_PATH", tmp_path / "plan.json")
    Plan(events=[Event(id="ev1", name="合歡北西峰 兩天", date="2027-03-01", kind="baiyue", days=2)]).save()
    EG.save("ev1", GPX, "route.gpx")
    RC.save("ev1", {"form": {"start": "05:00"}}, _baiyue_result())
    body = CP.payload("ev1")
    assert set(body) == {"name", "gpx", "pace_config", "points"}
    assert body["name"] == "合歡北西峰 兩天"
    assert body["gpx"] == GPX.decode("utf-8")
    curve = body["pace_config"]["model"]["curve"]
    assert body["pace_config"] == {"model": {"kind": "curve", "curve": curve}}
    from backend.engine.racepower import cairn_curve as CC
    assert curve == CC.for_saved("ev1")                                  # sent as SP-653 builds it
    # the curve is for exactly the file sent (Cairn hashes the text it gets)
    assert curve["gpx_sha256"] == hashlib.sha256(body["gpx"].encode("utf-8")).hexdigest()
    # one night: the day end of the calculation, on Cairn's own km, day 1 → day 2
    assert len(body["points"]) == 1
    p = body["points"][0]
    assert p["ref"] == "night-1" and p["distance_m"] == pytest.approx(curve["day_splits_km"][0] * 1000, abs=0.5)
    assert p["visits"] == [{"day": 1}, {"day": 2}] and p["name"]
    assert "eta" not in _keys(body) and "token" not in json.dumps(body).lower()


def test_the_body_is_what_cairn_takes(stores, monkeypatch, tmp_path):
    """The agreed request (SP-657 merged with SP-656): body {name, gpx, pace_config, points},
    pace_config = {"model": {"kind": "curve", "curve": <cairn-curve v1 as it is>}}; visits strict
    (day, distance_m only — never eta). Checked with the rules of Cairn's schemas."""
    from backend.accounts import cairn_push as CP
    from backend.engine import event_gpx as EG, planning, race_calc_store as RC
    from backend.engine.planning import Event, Plan
    monkeypatch.setattr(RC, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    monkeypatch.setattr(planning, "PLAN_PATH", tmp_path / "plan.json")
    Plan(events=[Event(id="ev1", name="兩天", date="2027-03-01", kind="baiyue", days=2)]).save()
    EG.save("ev1", GPX, "route.gpx")
    RC.save("ev1", {"form": {"start": "05:00"}}, _baiyue_result())
    body = CP.payload("ev1")
    assert list(body["pace_config"]) == ["model"] and list(body["pace_config"]["model"]) == ["kind", "curve"]
    assert body["pace_config"]["model"]["kind"] == "curve"
    cv = body["pace_config"]["model"]["curve"]
    assert {"fallback_check", "rest_hint", "start_times"} <= set(cv)          # untouched, passthrough
    assert cairn_curve_problem(cv) == ""
    assert FakeCairn.check(body, create=True) == "" and FakeCairn.check(body, create=False) == ""
    # a curve with a day without start time still goes as it is
    assert cairn_curve_problem({**cv, "start_times": ["05:00", None]}) == ""
    # the fake really refuses an eta
    bad = json.loads(json.dumps(body))
    bad["points"][0]["visits"][0]["eta"] = "08:00"
    assert FakeCairn.check(bad, create=True)


def test_a_one_day_race_has_no_points(stores, monkeypatch, tmp_path):
    from backend.accounts import cairn_push as CP
    from backend.engine import event_gpx as EG, planning, race_calc_store as RC
    from backend.engine.planning import Event, Plan
    monkeypatch.setattr(RC, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    monkeypatch.setattr(planning, "PLAN_PATH", tmp_path / "plan.json")
    Plan(events=[Event(id="ev1", name="越野 12K", date="2027-03-01")]).save()
    EG.save("ev1", GPX, "route.gpx")
    RC.save("ev1", {"form": {"start": "06:30"}}, _trail_result())
    body = CP.payload("ev1")
    assert body["points"] == [] and body["pace_config"]["model"]["curve"]["start_times"] == ["06:30"]


def test_a_night_takes_the_name_of_a_camp_there(stores, monkeypatch, tmp_path):
    from backend.accounts import cairn_push as CP
    nights = CP.night_points({"day_splits_km": [6.0, 9.5], "total_m": 12000.0},
                             names=[(6.1, "黑水塘山屋"), (11.0, "遠方")])
    assert [(p["ref"], p["name"], p["distance_m"], p["visits"]) for p in nights] == [
        ("night-1", "黑水塘山屋", 6000.0, [{"day": 1}, {"day": 2}]),
        ("night-2", nights[1]["name"], 9500.0, [{"day": 2}, {"day": 3}])]
    assert nights[1]["name"] != "遠方"


# ---------------------------------------------------------------------------- the flow

def test_first_push_creates_then_pushing_again_updates(c, app, cairn, stores):
    _ready(c)
    _event("g-a")
    r = c.get(status_url("race-1"))
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["linked"] and st["allowed"] and st["plan_ok"] and st["pushed"] is None and st["ready"] is True
    r = _push(c)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["action"] == "created" and got["trip_id"] == "trip-1" and got["url"].endswith("?gpx=trip-1")
    first = [x for x in cairn.calls if x.url.path.startswith("/api/internal/trc/trips")]
    assert len(first) == 1 and first[0].method == "POST"
    assert first[0].url.params.get("sub") == "g-a"
    assert first[0].headers["authorization"] == f"Bearer {SECRET}"
    sent = cairn.trips["trip-1"]["body"]
    assert sent["pace_config"]["model"]["kind"] == "curve" and len(sent["points"]) == 1
    assert "eta" not in _keys(sent)
    # pushed again: the same trip, updated
    r = _push(c)
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "updated" and r.json()["trip_id"] == "trip-1"
    trips = [x for x in cairn.calls if x.url.path.startswith("/api/internal/trc/trips")]
    assert [x.method for x in trips] == ["POST", "PUT"] and trips[1].url.path == "/api/internal/trc/trips/trip-1"
    assert list(cairn.trips) == ["trip-1"]
    st = c.get(status_url("race-1")).json()
    assert st["pushed"]["trip_id"] == "trip-1" and st["pushed"]["at"]


def test_nothing_of_cairns_user_is_kept(c, app, cairn, stores):
    """Only the trip id, the pushed file's hash and when: no token, no Cairn user id."""
    import sqlite3
    from backend import tenancy
    from backend.accounts import cairn_push as CP
    _ready(c)
    _event("g-a")
    assert _push(c).status_code == 200
    with _as("g-a"):
        con = sqlite3.connect(str(tenancy.db_path()))
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({CP.TABLE})")]
        rows = con.execute(f"SELECT * FROM {CP.TABLE}").fetchall()
        con.close()
    assert set(cols) == {"id", "event_id", "trip_id", "gpx_sha256", "pushed_at"}
    assert len(rows) == 1 and not any(SECRET in str(v) for v in rows[0])


def test_not_linked_says_link_first(c, app, cairn, stores):
    sign_up(c, "g-a", "alice@example.com")
    _subscribe("g-a")
    _event("g-a")
    st = c.get(status_url("race-1")).json()
    assert st["available"] and not st["linked"] and st["link_url"].endswith("?link=cairn#links")
    r = _push(c)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_LINKED"
    assert not [x for x in cairn.calls if "/trips" in x.url.path]


def test_the_feature_asks_on_its_own_and_the_push_can_give_it(c, app, cairn, stores):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    _subscribe("g-a")
    _link(c)
    _event("g-a")
    st = c.get(status_url("race-1")).json()
    assert st["linked"] and not st["allowed"] and st["consent_text"]
    r = _push(c)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
    assert not [x for x in cairn.calls if "/trips" in x.url.path]
    # 「同意並推送」 in the dialog: the same consents row as the switch in 設定
    r = _push(c, consent=True)
    assert r.status_code == 200, r.text
    acts = [(x["action"], json.loads(x["documents"] or "{}").get("scopes")) for x in store.consents(_uid("g-a"))]
    assert ("allow", [SCOPE]) in acts
    assert c.get(status_url("race-1")).json()["allowed"] is True


def test_linking_no_longer_grants_the_push(c, app, cairn):
    from backend.accounts import links as LK
    assert SCOPE in LK.FEATURES and LK.FEATURES[SCOPE]["plan"] == "subscriber"
    assert SCOPE not in LK.link_scopes(LK.CAIRN)


def test_a_free_plan_here_cannot_push(c, app, cairn, stores):
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    _allow(c, scope=SCOPE)
    _event("g-a")
    st = c.get(status_url("race-1")).json()
    assert st["plan_ok"] is False
    r = _push(c)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "PLAN_REQUIRED"
    assert not [x for x in cairn.calls if "/trips" in x.url.path]


@pytest.mark.parametrize("setup,status,code", [
    (lambda f: f.people.__setitem__("g-a", "free"), 403, "CAIRN_PLAN"),
    (lambda f: setattr(f, "quota_left", 0), 429, "CAIRN_QUOTA"),
    (lambda f: f.people.pop("g-a"), 409, "CAIRN_NO_ACCOUNT"),
    (lambda f: setattr(f, "down", True), 502, "CAIRN_DOWN"),
])
def test_cairns_refusals_are_plain_hints(c, app, cairn, stores, setup, status, code):
    _ready(c)
    _event("g-a")
    setup(cairn)
    r = _push(c)
    assert r.status_code == status, r.text
    d = r.json()["detail"]
    assert d["code"] == code and d["message"] and "Bearer" not in d["message"]
    assert c.get(status_url("race-1")).json()["pushed"] is None


def test_a_trip_deleted_in_cairn_is_forgotten_not_duplicated(c, app, cairn, stores):
    _ready(c)
    _event("g-a")
    assert _push(c).status_code == 200
    cairn.trips.clear()                                    # deleted in Cairn
    r = _push(c)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CAIRN_TRIP_GONE"
    assert [x.method for x in cairn.calls if "/trips" in x.url.path] == ["POST", "PUT"]   # no new trip by itself
    assert c.get(status_url("race-1")).json()["pushed"] is None
    r = _push(c)                                           # pushing again: a new trip
    assert r.status_code == 200 and r.json()["action"] == "created" and r.json()["trip_id"] == "trip-2"


def test_without_a_curve_nothing_is_sent(c, app, cairn, stores):
    from backend.engine import race_calc_store as RC
    _ready(c)
    _event("g-a")
    with _as("g-a"):
        RC.save("race-1", {"form": {}}, {"type": "trail", "summary": {}})     # nothing computed on the GPX
    st = c.get(status_url("race-1")).json()
    assert st["ready"] is False and st["why"]
    r = _push(c)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_RESULT"
    assert not [x for x in cairn.calls if "/trips" in x.url.path]


def test_only_the_persons_own_events(c, app, cairn, stores):
    """Each person pushes their own events: another's event id is not theirs, the trip id is kept
    in their own tenant, and the sub sent is always the signed-in person's."""
    _ready(c)
    _event("g-a")
    assert _push(c).status_code == 200
    b = browser(app)
    _ready(b, "g-b", "bob@example.com")
    r = b.get(status_url("race-1"))
    assert r.status_code == 404
    r = _push(b)
    assert r.status_code == 404
    _event("g-b", name="Bob 的賽事")
    r = _push(b)
    assert r.status_code == 200 and r.json()["action"] == "created" and r.json()["trip_id"] == "trip-2"
    subs = [x.url.params.get("sub") for x in cairn.calls if "/trips" in x.url.path]
    assert subs == ["g-a", "g-b"]
    assert cairn.trips["trip-2"]["body"]["name"] == "Bob 的賽事"


def test_not_configured_means_no_button(c, app, stores, monkeypatch):
    sign_up(c, "g-a", "alice@example.com")
    _event("g-a")
    st = c.get(status_url("race-1")).json()
    assert st["available"] is False
    assert _push(c).status_code == 409


def test_owner_mode_has_none_of_it(monkeypatch, tmp_path):
    from backend.main import build_app
    from backend.tests.test_login_sp58 import _clean_env, _client
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    with _client(build_app(demo=False)) as cl:
        assert cl.get(status_url("race-1")).status_code == 404
        assert cl.post(push_url("race-1"), json={}).status_code in (403, 404)


def test_the_push_is_in_the_race_manager_only_in_both_languages():
    # SP-699: 推到 Cairn left 賽事計算機; 賽事管理's editor popup has it (SP-692)
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "racepower.html").read_text("utf-8")
    assert 'id="cairn-push"' not in html and 'id="cp-dlg"' not in html and "/account/links/cairn/events/" not in html
    races = (root / "races.html").read_text("utf-8")
    assert 'id="cp-go"' in races and "/account/links/cairn/events/" in races
    zh = json.loads((root / "i18n" / "zh-TW" / "races.json").read_text("utf-8"))
    en = json.loads((root / "i18n" / "en" / "races.json").read_text("utf-8"))
    keys = [k for k in zh if k.startswith("cairn.")]
    assert len(keys) >= 10 and all(k in en for k in keys)
