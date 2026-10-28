"""SP-986: pushing to a Cairn trip marked 「這是跟團的時刻表」 (Cairn SP-582).

Cairn answers `PUT /api/internal/trc/trips/<id>` for such a trip with 409 "group schedule: this route
is marked 「這是跟團的時刻表」 in Cairn — read only, not written back" (its ownership 404 first, before
reading the body or counting the push). TRC never sends one on purpose (cairn_push.target); when it
does — the person ticked it in Cairn after the import or after our push — it is said plainly
(CAIRN_GROUP), not "Cairn can't be reached". Ticking it moves the trip's version: the race page says
Cairn changed and 「重新同步」 brings the mark over (SP-538).

SP-538's fake Cairn with Cairn's 409 added (httpx MockTransport): nothing reaches the real Cairn."""
from __future__ import annotations

import httpx
import pytest

from backend.tests.test_cairn_push_sp655 import _as, _event, _push, _ready, status_url, stores  # noqa: F401
from backend.tests.test_cairn_trips_sp538 import TID, TRIPS_URL, TripsCairn, _import, _race
from backend.tests.test_cairn_curve_sp653 import _baiyue_result
from backend.tests.test_link_sp329 import (  # noqa: F401
    CAIRN_URL, SECRET, _limits, _link, app, browser, c, csrf, env, sign_up, started,
)

GROUP_409 = "group schedule: this route is marked 「這是跟團的時刻表」 in Cairn — read only, not written back"


class GroupCairn(TripsCairn):
    """TripsCairn plus Cairn's read-only rule for a group's timetable: a PUT onto a trip marked the
    group's (`own[...]["group"]`, or one of `marked` — a trip we created and the person ticked) is 409
    after the ownership check, nothing written. `busy`: Cairn's other 409 (the version moving)."""

    def __init__(self):
        super().__init__()
        self.marked: set = set()
        self.busy = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "PUT" and path.startswith("/api/internal/trc/trips/"):
            tid = path[len("/api/internal/trc/trips/"):]
            sub = request.url.params.get("sub")
            mine = tid in self.trips and self.trips[tid]["sub"] == sub
            if mine and (tid in self.marked or self.own.get(tid, {}).get("group")):
                self.calls.append(request)
                return self.err(409, "CONFLICT", GROUP_409)
            if mine and self.busy:
                self.calls.append(request)
                return self.err(409, "CONFLICT", "trip version is changing, retry")
        return super().handle(request)

    def tick(self, tid: str = TID):
        """The person ticks 「這是跟團的時刻表」 in Cairn: the trip's version moves."""
        self.own[tid]["group"] = True
        self.own[tid]["version"] += 1


@pytest.fixture
def cairn(monkeypatch):
    from backend.accounts import links as LK
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    monkeypatch.setenv("TRC_CAIRN_URL", CAIRN_URL)
    fake = GroupCairn()
    monkeypatch.setattr(LK, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


def _imported(c, cairn):
    """A linked, paid person's race imported from their own trip, with a saved calculation."""
    from backend.engine import race_calc_store as RC
    _ready(c)
    cairn.add()
    _race(days=2)
    assert _import(c, trip_id=TID).status_code == 200
    with _as("g-a"):
        RC.save("race-1", {"form": {"start": "05:00"}}, _baiyue_result())


def _writes(cairn):
    return [(x.method, x.url.path) for x in cairn.calls if x.method in ("PUT", "POST")]


def test_a_trip_ticked_in_cairn_after_the_import_is_said_plainly_and_not_written(c, app, cairn, stores):
    from backend.engine import event_gpx as EG
    _imported(c, cairn)
    assert c.get(status_url("race-1")).json()["target"] == "origin"
    cairn.tick()                                                            # not synced yet
    r = _push(c)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "CAIRN_GROUP" and d["code"] != "CAIRN_DOWN"
    assert "這是跟團的時刻表" in d["message"] and "另外建一條" in d["message"]
    assert cairn.trips[TID]["body"].get("pace_config") is None              # nothing written there
    # now known as the group's: the next push makes a trip of our own, as for one known at the import
    with _as("g-a"):
        assert EG.get("race-1")["source"]["group"] is True
    st = c.get(status_url("race-1")).json()
    assert st["origin"]["group"] is True and st["target"] == "new"
    r2 = _push(c)
    assert r2.status_code == 200 and r2.json()["action"] == "created" and r2.json()["origin"] is False
    assert _writes(cairn) == [("PUT", f"/api/internal/trc/trips/{TID}"), ("POST", "/api/internal/trc/trips")]


def test_ticking_moves_the_version_and_resync_brings_the_mark(c, app, cairn, stores):
    from backend.engine import event_gpx as EG
    _imported(c, cairn)
    cairn.tick()
    got = c.get(TRIPS_URL).json()
    assert got["races"]["race-1"]["stale"] is True                          # 「Cairn 上這條改過了」
    assert next(t for t in got["trips"] if t["id"] == TID)["group"] is True
    assert _import(c).status_code == 200                                    # 「重新同步」
    with _as("g-a"):
        src = EG.get("race-1")["source"]
    assert src["group"] is True and src["version"] == "4"
    assert c.get(TRIPS_URL).json()["races"]["race-1"]["stale"] is False
    st = c.get(status_url("race-1")).json()
    assert st["target"] == "new"                                            # never pushed back
    assert _push(c).json()["action"] == "created"
    assert ("PUT", f"/api/internal/trc/trips/{TID}") not in _writes(cairn)


def test_a_trip_we_made_and_the_person_ticked_stays_ours(c, app, cairn, stores):
    _ready(c)
    _event("g-a")                                                           # an uploaded GPX
    r = _push(c)
    assert r.status_code == 200 and r.json()["action"] == "created"
    tid = r.json()["trip_id"]
    cairn.marked.add(tid)                                                   # ticked in Cairn later
    r2 = _push(c)
    assert r2.status_code == 409
    d = r2.json()["detail"]
    assert d["code"] == "CAIRN_GROUP" and "取消勾選" in d["message"]
    st = c.get(status_url("race-1")).json()
    assert st["pushed"]["trip_id"] == tid and st["target"] == "kept"        # kept: no second trip by itself
    cairn.marked.discard(tid)                                               # unticked there
    r3 = _push(c)
    assert r3.status_code == 200 and r3.json()["action"] == "updated" and r3.json()["trip_id"] == tid


def test_cairns_other_conflict_is_not_called_the_groups(c, app, cairn, stores):
    _imported(c, cairn)
    cairn.busy = True
    r = _push(c)
    assert r.json()["detail"]["code"] != "CAIRN_GROUP"
