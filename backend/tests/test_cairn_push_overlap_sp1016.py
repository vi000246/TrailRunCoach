"""SP-1016: pushing a race back onto the Cairn trip it was imported from (SP-816 / SP-538), TRC does not
send its night n where that trip already has its own overnight point for night n (a visit on day n and
on day n + 1) at the same place (within cairn_push.NAME_NEAR_KM, 0.5 km).

Why (Cairn origin/main, v2/server/src/routes/trc.ts syncPoints): every point of the PUT gets the id
sha256("trc:<gpxId>:<ref>"), so TRC's own night-n maps onto itself push after push; a point of the
person's own has another id, so sending it back by its ref would make a second one. The PUT drops the
points TRC made before and did not send this time — a duplicate night pushed before goes with the next
push. The person's own point is never touched.

Cairn is SP-538's fake (httpx MockTransport) with syncPoints modelled: nothing reaches the network or
the real Cairn."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from backend.tests.test_cairn_curve_sp653 import GPX, _baiyue_result
from backend.tests.test_cairn_push_sp655 import _as, _push, _ready, status_url, stores  # noqa: F401
from backend.tests.test_cairn_trips_sp538 import NIGHT_LAT, TID, TripsCairn, _import, _race
from backend.tests.test_link_sp329 import (  # noqa: F401 — fixtures used by name
    CAIRN_URL, SECRET, _limits, app, browser, c, csrf, env, sign_up, started,
)


def _pid(tid: str, ref: str) -> str:
    return hashlib.sha256(f"trc:{tid}:{ref}".encode()).hexdigest()


class SyncCairn(TripsCairn):
    """SP-538's fake plus Cairn's syncPoints: each point of a write is stored under
    sha256("trc:<gpxId>:<ref>"); the TRC points of the last write not sent again are deleted. The
    person's own points (own[tid]["points"]) are a separate set, never written by a push."""

    def __init__(self):
        super().__init__()
        self.trc: dict[str, dict[str, dict]] = {}          # trip id → {point id: point}
        self.deleted: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        r = super().handle(request)
        if request.method in ("PUT", "POST") and r.status_code in (200, 201):
            tid = r.json()["data"]["trip"]["id"]
            sent = {_pid(tid, p["ref"]): p for p in json.loads(request.content).get("points") or []}
            before = self.trc.get(tid, {})
            self.deleted += [k for k in before if k not in sent]
            self.trc[tid] = sent
        return r


@pytest.fixture
def cairn(monkeypatch):
    from backend.accounts import links as LK
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    monkeypatch.setenv("TRC_CAIRN_URL", CAIRN_URL)
    fake = SyncCairn()
    monkeypatch.setattr(LK, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


def _night(km: float, days=(1, 2), name="黑水塘山屋", ref="n1") -> dict:
    """The person's own overnight point in Cairn at `km` of the synthetic track (it runs north)."""
    return {"ref": ref, "name": name, "kind": "overnight", "distance_m": km * 1000.0,
            "lat": 24.0 + km / 111.195, "lon": 121.0, "visits": [{"day": d} for d in days]}


def _imported(c, cairn, night: dict | None = None, group: bool = False):
    """Linked, paid, link:gpx_sync on; race-1 imported from the person's trip TID (its overnight point
    `night`, default 黑水塘山屋 at km 6 for night 1); a saved 2-day calculation ending day 1 at km 6."""
    from backend.engine import race_calc_store as RC
    _ready(c)
    cairn.add(group=group)
    if night is not None:
        cairn.own[TID]["points"] = [cairn.own[TID]["points"][0], night]
    _race(days=2)
    r = _import(c, trip_id=TID)
    assert r.status_code == 200, r.text
    with _as("g-a"):
        RC.save("race-1", {"form": {"start": "05:00"}}, _baiyue_result())


def _sent(cairn) -> list[dict]:
    return json.loads([x for x in cairn.calls if x.method in ("PUT", "POST")][-1].content)["points"]


# ---------------------------------------------------------------------------- the night Cairn has

def test_a_night_cairn_already_has_there_is_not_sent_back(c, app, cairn, stores):
    from backend.engine import event_gpx as EG
    _imported(c, cairn)
    with _as("g-a"):
        assert EG.get("race-1")["source"]["nights"][0]["overnight"] == 1     # kept with the import
    own = json.loads(json.dumps(cairn.own[TID]["points"]))
    r = _push(c)
    assert r.status_code == 200, r.text
    assert r.json()["origin"] is True and r.json()["points"] == 0
    assert _sent(cairn) == []                                    # no night-1 of ours next to 黑水塘山屋
    assert cairn.trc[TID] == {} and cairn.own[TID]["points"] == own   # Cairn's own point untouched
    # pushed again: still nothing of ours there
    assert _push(c).status_code == 200 and _sent(cairn) == []


def test_a_duplicate_pushed_before_is_gone_with_the_next_push(c, app, cairn, stores):
    """Before SP-1016 the push sent night-1 too: Cairn made a second point by 黑水塘山屋. The next push
    leaves it out, and Cairn's PUT drops the TRC point not sent again."""
    from backend.accounts import cairn_push as CP
    _imported(c, cairn)
    cairn.trc[TID] = {_pid(TID, "night-1"): {"ref": "night-1", "name": "黑水塘山屋", "distance_m": 6000.0}}
    with _as("g-a"):
        CP.remember("race-1", TID, "0" * 64)                       # pushed back onto that trip before
    assert c.get(status_url("race-1")).json()["target"] == "origin"
    r = _push(c)
    assert r.status_code == 200, r.text
    assert [(x.method, x.url.path) for x in cairn.calls if x.method == "PUT"] == [("PUT", f"/api/internal/trc/trips/{TID}")]
    assert _sent(cairn) == []
    assert cairn.deleted == [_pid(TID, "night-1")] and cairn.trc[TID] == {}
    assert [p["ref"] for p in cairn.own[TID]["points"]] == ["p1", "n1"]   # the person's own stay


# ---------------------------------------------------------------------------- the nights that still go

def test_a_night_cairn_has_elsewhere_is_sent_and_both_stay(c, app, cairn, stores):
    """The two plans differ (Cairn's night 1 at km 8, ours at km 6, more than 0.5 km apart): not a
    duplicate — ours goes, theirs stays."""
    _imported(c, cairn, night=_night(8.0))
    r = _push(c)
    assert r.status_code == 200, r.text
    pts = _sent(cairn)
    assert [p["ref"] for p in pts] == ["night-1"] and pts[0]["distance_m"] == pytest.approx(6000, abs=60)
    assert [p["ref"] for p in cairn.own[TID]["points"]] == ["p1", "n1"]


def test_only_the_same_night_counts(c, app, cairn, stores):
    """Cairn's point at km 6 is its night 2 (visits day 2 and 3): our night 1 there still goes."""
    _imported(c, cairn, night=_night(6.0, days=(2, 3)))
    assert _push(c).status_code == 200
    assert [p["ref"] for p in _sent(cairn)] == ["night-1"]


def test_a_point_without_the_next_days_visit_is_not_a_night_of_cairns(c, app, cairn, stores):
    """An overnight point visited on day 1 only says no night: ours goes."""
    from backend.engine import event_gpx as EG
    _imported(c, cairn, night=_night(6.0, days=(1,)))
    with _as("g-a"):
        assert EG.get("race-1")["source"]["nights"][0]["overnight"] is None
    assert _push(c).status_code == 200
    assert [p["ref"] for p in _sent(cairn)] == ["night-1"]


def test_a_trip_of_our_own_gets_every_night(c, app, cairn, stores):
    """Not pushed back (the group's timetable → a new trip of our own): Cairn's points are not on that
    trip, so every night goes."""
    _imported(c, cairn, group=True)
    r = _push(c)
    assert r.status_code == 200 and r.json()["action"] == "created" and r.json()["origin"] is False
    assert [p["ref"] for p in _sent(cairn)] == ["night-1"]


# ---------------------------------------------------------------------------- the rule, the kept nights

def test_the_rule_by_night_and_place():
    from backend.accounts import cairn_push as CP
    pts = [{"ref": "night-1", "distance_m": 6000.0}, {"ref": "night-2", "distance_m": 12000.0}]
    assert CP.without_cairns_nights(pts, []) == pts
    assert CP.without_cairns_nights(pts, [(1, 6.4)]) == pts[1:]            # within 0.5 km
    assert CP.without_cairns_nights(pts, [(1, 6.6)]) == pts                # 0.6 km off: both stay
    assert CP.without_cairns_nights(pts, [(2, 6.0)]) == pts                # another night
    assert CP.without_cairns_nights(pts, [(1, 5.9), (2, 12.2)]) == []


def test_the_nights_kept_with_an_import(stores, monkeypatch, tmp_path):
    """event_gpx keeps each night's `overnight`; a night kept before SP-1016 (no such key) stands by its
    place among the nights; null means no night of Cairn's."""
    from backend.accounts import cairn_push as CP
    from backend.engine import event_gpx as EG
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    src = {"kind": "cairn", "gpx_id": TID, "nights": [{"night": 1, "km": 6.0, "name": "a", "overnight": 1},
                                                      {"night": 2, "km": 9.0, "name": "b", "overnight": None},
                                                      {"night": 3, "km": 10.0, "name": "c", "overnight": True}]}
    EG.save("e1", GPX, "r.gpx", source=src)
    kept = EG.get("e1")["source"]["nights"]
    assert [n["overnight"] for n in kept] == [1, None, None]
    assert CP.cairn_nights("e1", 1.0) == [(1, 6.0)]
    EG.save("e2", GPX, "r.gpx", source={"kind": "cairn", "gpx_id": TID, "nights": [{"night": 1, "km": 6.0, "name": "a"}]})
    assert "overnight" not in EG.get("e2")["source"]["nights"][0]
    assert CP.cairn_nights("e2", 2.0) == [(1, 12.0)]                      # before SP-1016: its place, on Cairn's km
    EG.save("e3", GPX, "r.gpx")
    assert CP.cairn_nights("e3", 1.0) == []


def test_a_share_links_night_is_cairns_when_visited_both_days():
    """SP-537's share link: a night is Cairn's own (overnight n) when its point is visited on day n and
    n + 1 — the same rule as the internal API."""
    from backend.engine import cairn as CA
    both = {"name": "山屋", "lat": NIGHT_LAT, "lng": 121.0,
            "visits": [{"day": 1, "kind": "arrive", "distanceM": 6000}, {"day": 2, "kind": "depart", "distanceM": 6000}]}
    arrive = {"name": "營地", "lat": NIGHT_LAT, "lng": 121.0, "visits": [{"day": 1, "kind": "arrive", "distanceM": 6000}]}
    end = {"name": "終點", "lat": 24.1, "lng": 121.0, "visits": [{"day": 2, "kind": "arrive", "distanceM": 12000}]}
    assert [(n["day"], n["overnight"]) for n in CA.night_visits([both, end])] == [(1, 1)]
    assert [(n["day"], n["overnight"]) for n in CA.night_visits([arrive, end])] == [(1, None)]
