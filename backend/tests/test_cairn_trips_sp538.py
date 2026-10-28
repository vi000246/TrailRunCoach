"""SP-538 (the "pull" half; SP-655 / 816 push): 賽事管理「從我的疊石 Cairn 行程匯入」.

A linked person's own Cairn trips are listed server to server (GET /api/internal/trc/trips?sub=, the
outbound secret, no share link) and one becomes a race's GPX with its overnight points as the day ends
(GET /trips/<id>?sub=) — stored like SP-537's import (event_gpx source kind "cairn", gpx_id) plus the
trip's version; when Cairn's version moves the page says so and 「重新同步」 reads it again. Such a
race pushes back onto that very trip (SP-816). Not linked / not set up: Cairn is never called.

Cairn is SP-655's fake with the two read routes added (httpx MockTransport): nothing here reaches the
network or the real Cairn."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.tests.test_cairn_curve_sp653 import GPX, _baiyue_result
from backend.tests.test_cairn_push_sp655 import (  # noqa: F401 — fixtures used by name
    FakeCairn, _as, _push, _ready, status_url, stores,
)
from backend.tests.test_link_sp329 import (  # noqa: F401
    CAIRN_URL, SECRET, _limits, _link, app, browser, c, csrf, env, sign_up, started,
)

TRIPS_URL = "/api/v1/account/links/cairn/trips"
TID = "g7Kq2"
NIGHT_LAT = 24.0 + 6.0 / 111.195            # the synthetic track goes north from 24.0, 121.0: km 6


def import_url(eid: str) -> str:
    return f"/api/v1/account/links/cairn/events/{eid}/import"


class TripsCairn(FakeCairn):
    """SP-655's fake plus Cairn's read routes for TrailRunCoach (Cairn SP-657 branch, the shape its
    session gave): GET /trips?sub= → {ok, data: {sub, trips: [summary]}}, GET /trips/<id>?sub= →
    {ok, data: {trip, gpx, gpx_sha256, pace_config, points}}, someone else's → 404. A PUT moves the
    version by one and answers it."""

    def __init__(self):
        super().__init__()
        self.own: dict = {}

    def add(self, sub="g-a", tid=TID, name="合歡北西峰 我的版本", version=3, nights=True, group=False):
        import hashlib
        self.own[tid] = {"sub": sub, "name": name, "version": version, "group": group}
        self.trips[tid] = {"sub": sub, "body": {"name": name, "gpx": GPX.decode("utf-8")}}
        pts = [{"ref": "p1", "name": "登山口", "kind": "poi", "distance_m": 0, "lat": 24.0, "lon": 121.0,
                "visits": [{"day": 1}]}]
        if nights:
            pts.append({"ref": "n1", "name": "黑水塘山屋", "kind": "overnight", "distance_m": 6000,
                        "lat": NIGHT_LAT, "lon": 121.0, "visits": [{"day": 1}, {"day": 2}]})
        self.own[tid]["points"] = pts
        self.own[tid]["sha"] = hashlib.sha256(GPX).hexdigest()

    def summary(self, tid):
        o = self.own[tid]
        s = {"id": tid, "name": o["name"], "version": o["version"], "source": "cairn",
             "days": 2 if any(p["kind"] == "overnight" for p in o["points"]) else 1,
             "distance_m": 12000.0, "gain_m": 1400.0, "updated_at": "2026-10-27T08:00:00Z"}
        if o["group"]:
            s["group"] = True
        return s

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.startswith("/api/internal/trc/trips"):
            self.calls.append(request)
            if self.down:
                raise httpx.ConnectError("down")
            if request.headers.get("authorization") != f"Bearer {SECRET}":
                return self.err(401, "UNAUTHORIZED", "service secret invalid")
            sub = request.url.params.get("sub")
            if sub not in self.people:
                return self.err(404, "NOT_FOUND", "no cairn account for this sub")
            if path == "/api/internal/trc/trips":
                mine = [self.summary(t) for t, o in self.own.items() if o["sub"] == sub]
                return httpx.Response(200, json={"ok": True, "data": {"sub": sub, "trips": mine}})
            tid = path[len("/api/internal/trc/trips/"):]
            o = self.own.get(tid)
            if o is None or o["sub"] != sub:
                return self.err(404, "NOT_FOUND", f"no gpx {tid}")
            return httpx.Response(200, json={"ok": True, "data": {
                "trip": self.summary(tid), "gpx": GPX.decode("utf-8"), "gpx_sha256": o["sha"],
                "pace_config": None, "points": o["points"]}})
        r = super().handle(request)
        tid = path[len("/api/internal/trc/trips/"):] if request.method == "PUT" else None
        if r.status_code == 200 and tid in self.own:
            self.own[tid]["version"] += 1
            return httpx.Response(200, json={"ok": True, "data": {"trip": {**self.summary(tid), "source": "trailruncoach"}}})
        return r


@pytest.fixture
def cairn(monkeypatch):
    from backend.accounts import links as LK
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    monkeypatch.setenv("TRC_CAIRN_URL", CAIRN_URL)
    fake = TripsCairn()
    monkeypatch.setattr(LK, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


def _race(sub="g-a", eid="race-1", days=1):
    """A season-plan race without a GPX yet, in `sub`'s tenant."""
    from backend.engine.planning import Event, Plan
    with _as(sub):
        plan = Plan.load()
        plan.events = [e for e in plan.events if e.id != eid] + [Event(id=eid, name="合歡北西峰", date="2027-03-01",
                                                                         kind="baiyue", days=days)]
        plan.save()


def _linked(cl, sub="g-a", email="alice@example.com"):
    sign_up(cl, sub, email)
    assert _link(cl).status_code == 200


def _import(cl, eid="race-1", **body):
    return cl.post(import_url(eid), json=body, headers={"X-TRC-CSRF": csrf(cl)})


def _reads(cairn):
    return [(x.method, x.url.path) for x in cairn.calls if x.method == "GET" and "/trips" in x.url.path]


# ---------------------------------------------------------------------------- the list

def test_the_list_is_the_persons_own_trips_with_the_race_holding_each(c, app, cairn, stores):
    _linked(c)
    cairn.add()
    cairn.add(sub="g-b", tid="other1", name="別人的")
    _race()
    got = c.get(TRIPS_URL).json()
    assert got["available"] is True and got["linked"] is True and got["error"] is None
    assert [t["id"] for t in got["trips"]] == [TID]                          # g-b's never listed
    t = got["trips"][0]
    assert t == {"id": TID, "name": "合歡北西峰 我的版本", "version": "3", "source": "cairn", "days": 2,
                 "distance_m": 12000.0, "gain_m": 1400.0, "updated_at": "2026-10-27T08:00:00Z", "group": False,
                 "event_id": None}
    req = cairn.calls[-1]
    assert req.headers["authorization"] == f"Bearer {SECRET}" and req.url.params["sub"] == "g-a"
    assert _import(c, trip_id=TID).status_code == 200
    got = c.get(TRIPS_URL).json()
    assert got["trips"][0]["event_id"] == "race-1"
    assert got["races"] == {"race-1": {"trip_id": TID, "have": "3", "now": "3", "stale": False}}


def test_not_linked_or_not_set_up_never_calls_cairn(c, app, cairn, monkeypatch):
    sign_up(c, "g-a", "alice@example.com")
    got = c.get(TRIPS_URL).json()
    assert got["available"] is True and got["linked"] is False and got["trips"] == []
    assert "/api/v1/app/settings?link=cairn" in got["link_url"] and got["card_off"] is False
    assert _reads(cairn) == []
    _race()
    r = _import(c, trip_id=TID)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_LINKED"
    monkeypatch.delenv("TRC_CAIRN_URL")
    got = c.get(TRIPS_URL).json()
    assert got["available"] is False and got["reason"] == "not_open"
    assert _reads(cairn) == []


def test_held_only_asks_cairn_only_when_a_race_holds_a_trip(c, app, cairn, stores):
    _linked(c)
    cairn.add()
    _race()
    got = c.get(TRIPS_URL, params={"held_only": 1}).json()
    assert got["races"] == {} and _reads(cairn) == []
    assert _import(c, trip_id=TID).status_code == 200
    n = len(_reads(cairn))
    got = c.get(TRIPS_URL, params={"held_only": 1}).json()
    assert got["races"]["race-1"]["stale"] is False and len(_reads(cairn)) == n + 1


# ---------------------------------------------------------------------------- the import

def test_a_trip_becomes_the_races_gpx_with_its_nights_as_the_day_ends(c, app, cairn, stores):
    from backend.engine import event_gpx as EG
    _linked(c)
    cairn.add()
    _race(days=1)
    r = _import(c, trip_id=TID)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["event_days"] == 2 and got["event"]["days"] == 2
    assert got["event"]["distance_km"] == pytest.approx(12.0, abs=0.1)
    assert got["cairn"]["name"] == "合歡北西峰 我的版本" and len(got["cairn"]["nights"]) == 1
    assert got["cairn"]["nights"][0]["name"] == "黑水塘山屋"
    assert got["trip"]["id"] == TID and got["trip"]["version"] == "3"
    with _as("g-a"):
        row = EG.get("race-1")
        assert row["sha1"] and EG.read_bytes("race-1") == GPX
        assert row["day_splits"] == [pytest.approx(6.0, abs=0.1)]
        src = row["source"]
        assert src["kind"] == "cairn" and src["gpx_id"] == TID and src["version"] == "3" and src["via"] == "account"
        assert src["url"].endswith(f"?gpx={TID}") and "group" not in src
        assert "share" not in json.dumps(src) and "token" not in json.dumps(src).lower()


def test_a_trip_without_nights_imports_the_gpx_and_says_so(c, app, cairn, stores):
    _linked(c)
    cairn.add(nights=False)
    _race(days=1)
    got = _import(c, trip_id=TID).json()
    assert got["event_days"] == 1 and got["cairn"]["nights"] == [] and got["cairn"]["notes"]


def test_cairns_refusals_are_said_plainly(c, app, cairn, stores, monkeypatch):
    _linked(c)
    cairn.add(sub="g-b", tid="other1")
    _race()
    r = _import(c, trip_id="other1")                                        # not theirs
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CAIRN_TRIP_GONE"
    r = _import(c, trip_id="../x")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CAIRN_TRIP_GONE"
    cairn.people.pop("g-a")                                                 # no Cairn account
    assert _import(c, trip_id=TID).json()["detail"]["code"] == "CAIRN_NO_ACCOUNT"
    assert c.get(TRIPS_URL).json()["error"]["code"] == "CAIRN_NO_ACCOUNT"
    cairn.people["g-a"] = "basic"
    cairn.down = True                                                       # timeout / unreachable
    r = _import(c, trip_id=TID)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "CAIRN_DOWN"
    got = c.get(TRIPS_URL).json()
    assert got["error"]["code"] == "CAIRN_DOWN" and got["trips"] == []
    cairn.down = False
    monkeypatch.setenv("TRC_CAIRN_OUT_SECRET", "a-different-secret-not-real")   # Cairn answers 401
    assert c.get(TRIPS_URL).json()["error"]["code"] == "CAIRN_DOWN"


def test_a_failed_import_leaves_the_race_alone(c, app, cairn, stores):
    from backend.engine import event_gpx as EG
    _linked(c)
    _race()
    r = _import(c, trip_id=TID)
    assert r.status_code == 409
    with _as("g-a"):
        assert EG.get("race-1") is None


# ---------------------------------------------------------------------------- 重新同步

def test_a_moved_version_is_said_and_resync_reads_the_trip_again(c, app, cairn, stores):
    from backend.engine import event_gpx as EG
    _linked(c)
    cairn.add()
    _race()
    assert _import(c, trip_id=TID).status_code == 200
    cairn.own[TID]["version"] = 4                                           # changed in Cairn
    cairn.own[TID]["name"] = "合歡北西峰 改過"
    got = c.get(TRIPS_URL).json()
    assert got["races"]["race-1"] == {"trip_id": TID, "have": "3", "now": "4", "stale": True}
    r = _import(c)                                                          # no trip_id: the one it came from
    assert r.status_code == 200, r.text
    assert r.json()["trip"]["version"] == "4"
    with _as("g-a"):
        assert EG.get("race-1")["source"]["version"] == "4"
    assert c.get(TRIPS_URL).json()["races"]["race-1"]["stale"] is False
    assert [p for m, p in _reads(cairn)].count(f"/api/internal/trc/trips/{TID}") == 2


def test_resync_needs_a_race_from_cairn(c, app, cairn, stores):
    _linked(c)
    _race()
    r = _import(c)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_FROM_CAIRN"


def test_a_share_link_import_has_no_version_so_its_freshness_is_unknown():
    from backend.accounts import cairn_trips as CT
    listed = [{"id": TID, "version": "5"}]
    assert CT.compare(listed, {"e1": {"trip_id": TID, "version": None}}) == {
        "e1": {"trip_id": TID, "have": None, "now": "5", "stale": None}}
    assert CT.compare(listed, {"e2": {"trip_id": "gone", "version": "1"}}) == {}


# ---------------------------------------------------------------------------- pushing back (SP-816)

def test_an_imported_race_pushes_back_onto_that_trip_and_keeps_in_sync(c, app, cairn, stores):
    from backend.engine import event_gpx as EG, race_calc_store as RC
    _ready(c)                                                               # linked, paid, link:gpx_sync on
    cairn.add()
    _race(days=2)
    assert _import(c, trip_id=TID).status_code == 200
    with _as("g-a"):
        RC.save("race-1", {"form": {"start": "05:00"}}, _baiyue_result())
    st = c.get(status_url("race-1")).json()
    assert st["target"] == "origin" and st["origin"]["trip_id"] == TID
    r = _push(c)
    assert r.status_code == 200, r.text
    assert r.json()["origin"] is True and r.json()["trip_id"] == TID
    puts = [(x.method, x.url.path) for x in cairn.calls if x.method in ("PUT", "POST")]
    assert puts == [("PUT", f"/api/internal/trc/trips/{TID}")]
    assert set(json.loads(cairn.calls[-1].content)) == {"pace_config", "points"}
    # our own write moved Cairn's version by one: kept, so the page doesn't ask to sync our own push
    assert cairn.own[TID]["version"] == 4
    with _as("g-a"):
        assert EG.get("race-1")["source"]["version"] == "4"
    assert c.get(TRIPS_URL).json()["races"]["race-1"]["stale"] is False


def test_a_change_in_cairn_before_our_push_still_asks_to_sync(stores, monkeypatch, tmp_path):
    from backend.accounts import cairn_trips as CT
    from backend.engine import event_gpx as EG
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    EG.save("e1", GPX, "r.gpx", source={"kind": "cairn", "gpx_id": TID, "version": "3", "via": "account"})
    CT.after_push("e1", {"ok": True, "data": {"trip": {"id": TID, "version": 6}}})     # moved by more than us
    assert EG.get("e1")["source"]["version"] == "3"
    CT.after_push("e1", {"ok": True, "data": {"trip": {"id": TID}}})                  # no version answered
    assert EG.get("e1")["source"]["version"] == "3"
    CT.after_push("e1", {"ok": True, "data": {"trip": {"id": TID, "version": 4}}})
    assert EG.get("e1")["source"]["version"] == "4"


def test_a_trip_with_a_groups_timetable_is_never_pushed_back(c, app, cairn, stores):
    from backend.engine import race_calc_store as RC
    _ready(c)
    cairn.add(group=True)
    _race(days=2)
    assert _import(c, trip_id=TID).status_code == 200
    with _as("g-a"):
        RC.save("race-1", {"form": {"start": "05:00"}}, _baiyue_result())
    st = c.get(status_url("race-1")).json()
    assert st["origin"]["group"] is True and st["target"] == "new"
    r = _push(c)
    assert r.status_code == 200 and r.json()["action"] == "created" and r.json()["origin"] is False
    assert cairn.trips[TID]["body"].get("pace_config") is None


# ---------------------------------------------------------------------------- Cairn's shape, in one place

def test_cairns_answers_are_read_in_one_place():
    from backend.accounts import cairn_trips as CT
    assert CT.PATH_LIST == "/api/internal/trc/trips" and CT.PATH_TRIP.format(id="x") == "/api/internal/trc/trips/x"
    assert CT.summary({"id": "../bad"}) is None and CT.summary("x") is None
    s = CT.summary({"id": "a1", "name": "N", "version": 7.0, "source": "x", "days": True, "distance_m": "12"})
    assert s["version"] == "7" and s["source"] is None and s["days"] is None and s["distance_m"] is None
    d = CT._detail({"trip": {"id": "a1", "version": "v2"}, "gpx": "<gpx/>", "gpx_sha256": "AB" * 32, "points": [
        {"kind": "overnight", "name": "二", "distance_m": 9000, "lat": 24.1, "lng": 121.1, "visits": [{"day": 2}, {"day": 3}]},
        {"kind": "overnight", "name": "一", "distance_m": 4000, "lat": 24.0, "lon": 121.0, "visits": [{"day": 1}, {"day": 2}]},
        {"kind": "poi", "name": "水源", "distance_m": 5000}]})
    assert d["gpx_sha256"] == "ab" * 32
    assert [(n["name"], n["km"], n["day"], n["lng"]) for n in d["nights"]] == [("一", 4.0, 1, 121.0), ("二", 9.0, 2, 121.1)]


# ---------------------------------------------------------------------------- the page

def test_the_race_page_offers_the_trips_resync_and_the_link_card():
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "races.html").read_text("utf-8")
    assert "/api/v1/account/links/cairn/trips" in html and '+ "/import"' in html
    assert 'id="ct-pick"' in html and 'id="ct-sync"' in html and 'id="ct-card"' in html
    assert "/api/v1/app/xp/cairn/dismiss" in html and "held_only=1" in html
    zh = json.loads((root / "i18n" / "zh-TW" / "races.json").read_text("utf-8"))
    en = json.loads((root / "i18n" / "en" / "races.json").read_text("utf-8"))
    keys = [k for k in zh if k.startswith("ct.")]
    assert len(keys) >= 8
    for k in keys:
        assert en.get(k) and en[k] != zh[k], k
