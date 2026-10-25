"""SP-816: a race whose GPX was imported from a Cairn trip (SP-537 / SP-676: event_gpx source kind
"cairn", gpx_id) is pushed onto THAT trip — PUT /trips/<gpx_id> — instead of a second, new one; only
the pace curve and TRC's night points go (never the trip's GPX file or its name: the person's own
trip; Cairn leaves their points and times alone). Not theirs / deleted in Cairn: nothing is created
by itself (409 CAIRN_ORIGIN_GONE); {new: true} then makes a new trip. Cairn is SP-655's fake."""
from __future__ import annotations

import json
from pathlib import Path

from backend.tests.test_cairn_curve_sp653 import GPX
from backend.tests.test_cairn_push_sp655 import (  # noqa: F401 — fixtures used by name
    _as, _event, _push, _ready, cairn, status_url, stores,
)
from backend.tests.test_link_sp329 import _limits, app, browser, c, csrf, env, sign_up, started  # noqa: F401

ORIGIN = "g7Kq2"                         # the Cairn trip (gpx) id the race's GPX came from


def _from_cairn(sub: str = "g-a", gpx_id: str = ORIGIN, eid: str = "race-1"):
    """_event's race, its GPX stored as imported from the Cairn trip `gpx_id`."""
    from backend.engine import event_gpx as EG
    _event(sub, eid)
    with _as(sub):
        EG.save(eid, GPX, "合歡北西峰.gpx", source={"kind": "cairn", "gpx_id": gpx_id, "name": "合歡北西峰 我的版本",
                                                   "url": f"https://map.example/?gpx={gpx_id}"})


def _cairn_trip(cairn, sub: str = "g-a", tid: str = ORIGIN):
    """The trip in Cairn, as its owner left it: their own name, file, points and times."""
    cairn.trips[tid] = {"sub": sub, "body": {"name": "合歡北西峰 我的版本", "gpx": GPX,
                                             "user_points": [{"name": "小溪營地", "eta": "2027-03-01T13:30"}]}}


def _trip_calls(cairn):
    return [(x.method, x.url.path) for x in cairn.calls if x.url.path.startswith("/api/internal/trc/trips")]


def test_a_race_imported_from_cairn_updates_that_trip_not_a_new_one(c, app, cairn, stores):
    _ready(c)
    _cairn_trip(cairn)
    _from_cairn()
    st = c.get(status_url("race-1")).json()
    assert st["target"] == "origin" and st["origin"]["trip_id"] == ORIGIN and st["origin"]["name"] == "合歡北西峰 我的版本"
    assert st["pushed"] is None and st["ready"] is True
    r = _push(c)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["action"] == "updated" and got["origin"] is True and got["trip_id"] == ORIGIN
    assert got["url"].endswith(f"?gpx={ORIGIN}")
    assert _trip_calls(cairn) == [("PUT", f"/api/internal/trc/trips/{ORIGIN}")]
    assert list(cairn.trips) == [ORIGIN]                                    # no second trip in Cairn
    sent = json.loads(cairn.calls[-1].content)
    assert set(sent) == {"pace_config", "points"}                           # never its file or its name
    trip = cairn.trips[ORIGIN]["body"]
    assert trip["name"] == "合歡北西峰 我的版本" and trip["gpx"] == GPX
    assert trip["user_points"] == [{"name": "小溪營地", "eta": "2027-03-01T13:30"}]   # theirs, untouched
    assert trip["pace_config"]["model"]["kind"] == "curve" and len(trip["points"]) == 1
    # kept like any push: pushing again updates the same trip, still without its file or name
    st = c.get(status_url("race-1")).json()
    assert st["pushed"]["trip_id"] == ORIGIN and st["target"] == "origin"
    r = _push(c)
    assert r.status_code == 200 and r.json()["origin"] is True
    assert _trip_calls(cairn) == [("PUT", f"/api/internal/trc/trips/{ORIGIN}")] * 2
    assert set(json.loads(cairn.calls[-1].content)) == {"pace_config", "points"}
    assert list(cairn.trips) == [ORIGIN]


def test_someone_elses_shared_trip_is_not_updated_and_nothing_is_made_by_itself(c, app, cairn, stores):
    _ready(c)
    _cairn_trip(cairn, sub="g-b")                                            # the trip is g-b's (a share link)
    _from_cairn()
    r = _push(c)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CAIRN_ORIGIN_GONE", r.text
    assert _trip_calls(cairn) == [("PUT", f"/api/internal/trc/trips/{ORIGIN}")]
    assert c.get(status_url("race-1")).json()["pushed"] is None
    r = _push(c)                                                            # pushed again: asks again
    assert r.json()["detail"]["code"] == "CAIRN_ORIGIN_GONE"
    assert [m for m, _p in _trip_calls(cairn)] == ["PUT", "PUT"]
    assert cairn.trips[ORIGIN]["body"].get("pace_config") is None            # g-b's trip untouched
    # the person asks for a new trip: created, with the file and the name, and kept from then on
    r = _push(c, new=True)
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "created" and r.json()["origin"] is False and r.json()["trip_id"] == "trip-1"
    assert _trip_calls(cairn)[-1] == ("POST", "/api/internal/trc/trips")
    st = c.get(status_url("race-1")).json()
    assert st["pushed"]["trip_id"] == "trip-1" and st["target"] == "kept"
    assert _push(c).json()["trip_id"] == "trip-1" and _trip_calls(cairn)[-1] == ("PUT", "/api/internal/trc/trips/trip-1")


def test_an_origin_trip_deleted_in_cairn_after_the_push_is_forgotten_not_duplicated(c, app, cairn, stores):
    _ready(c)
    _cairn_trip(cairn)
    _from_cairn()
    assert _push(c).status_code == 200
    cairn.trips.clear()                                                     # deleted in Cairn
    r = _push(c)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CAIRN_ORIGIN_GONE"
    assert c.get(status_url("race-1")).json()["pushed"] is None
    assert not any(m == "POST" for m, _p in _trip_calls(cairn))


def test_a_trip_answered_the_groups_timetable_is_never_written_back(c, app, cairn, stores):
    """SP-575 Q1 / SP-582: the group's timetable is read only — a race whose Cairn trip was answered
    「這是團的時刻表」 gets a trip of its own, as before SP-816."""
    from backend.engine import event_gpx as EG
    _ready(c)
    _cairn_trip(cairn)
    _from_cairn()
    with _as("g-a"):
        con_src = EG.get("race-1")["source"]
        sched = {"group": True, "points": [{"name": "登山口", "day": 1, "eta": "05:00", "km": 0},
                                           {"name": "營地", "day": 1, "eta": "13:00", "km": 6}]}
        EG.save("race-1", GPX, "合歡北西峰.gpx", source={**con_src, "schedule": sched})
        assert EG.get("race-1")["source"]["schedule"]["group"] is True
    st = c.get(status_url("race-1")).json()
    assert st["origin"]["group"] is True and st["target"] == "new"
    r = _push(c)
    assert r.status_code == 200 and r.json()["action"] == "created" and r.json()["origin"] is False
    assert _trip_calls(cairn) == [("POST", "/api/internal/trc/trips")]
    assert cairn.trips[ORIGIN]["body"].get("pace_config") is None            # the group's trip untouched


def test_an_uploaded_gpx_still_creates_its_trip(c, app, cairn, stores):
    _ready(c)
    _event("g-a")
    st = c.get(status_url("race-1")).json()
    assert st["origin"] is None and st["target"] == "new"
    r = _push(c)
    assert r.json()["action"] == "created" and r.json()["origin"] is False


def test_the_dialog_says_it_updates_the_original_trip_in_both_languages():
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "races.html").read_text("utf-8")
    assert 'st.target === "origin"' in html and '"cairn.done_origin"' in html
    assert 'e.code === "CAIRN_ORIGIN_GONE"' in html and "cpPush({ new: true })" in html
    zh = json.loads((root / "i18n" / "zh-TW" / "races.json").read_text("utf-8"))
    en = json.loads((root / "i18n" / "en" / "races.json").read_text("utf-8"))
    for k in ("cairn.what_origin", "cairn.origin", "cairn.done_origin", "cairn.new_go"):
        assert zh[k] and en[k] and zh[k] != en[k], k
    assert "{name}" in zh["cairn.origin"] and "{name}" in en["cairn.origin"]
    assert "不會另外新建" in zh["cairn.origin"] and "你在 Cairn 加的點和填的時刻都不會動" in zh["cairn.origin"]
