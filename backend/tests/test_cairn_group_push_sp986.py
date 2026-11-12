"""SP-986 was: pushing to a Cairn trip marked 「這是跟團的時刻表」 (Cairn SP-582) — Cairn answered the PUT
409 "group schedule: …", TRC said it plainly (CAIRN_GROUP), and Cairn's trip list carried `group`.

Cairn retired the mark (Cairn SP-582 R1); TRC no longer reads it (Cairn SP-1454). The fake Cairn below can
still answer like an old Cairn did — these pin that nothing in TRC treats it specially any more: no
CAIRN_GROUP, no `source.group` written, the list's `group` not read. TRC's own 「這是團的時間表」 is in
test_cairn_group_sp986.

SP-538's fake Cairn with the old 409 added (httpx MockTransport): nothing reaches the real Cairn."""
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


def test_an_old_cairns_group_409_is_no_longer_special(c, app, cairn, stores):
    """An old Cairn's 409 "group schedule: …": the generic hint, nothing written, the trip not made the group's."""
    from backend.engine import event_gpx as EG
    _imported(c, cairn)
    assert c.get(status_url("race-1")).json()["target"] == "origin"
    cairn.tick()
    r = _push(c)
    d = r.json()["detail"]
    assert d["code"] != "CAIRN_GROUP" and d["code"] == "CAIRN_DOWN"
    assert "取消勾選" not in d["message"] and "跟團" not in d["message"]
    assert cairn.trips[TID]["body"].get("pace_config") is None              # nothing written there
    with _as("g-a"):
        assert "group" not in EG.get("race-1")["source"]
    assert c.get(status_url("race-1")).json()["target"] == "origin"         # still the trip it came from


def test_the_lists_group_is_not_read_and_resync_brings_no_mark(c, app, cairn, stores):
    from backend.engine import event_gpx as EG
    from backend.accounts import cairn_push as CP
    _imported(c, cairn)
    cairn.tick()                                                            # an old Cairn's list says `group`
    got = c.get(TRIPS_URL).json()
    assert got["races"]["race-1"]["stale"] is True                          # the version moved: still said
    assert "group" not in next(t for t in got["trips"] if t["id"] == TID)
    assert _import(c).status_code == 200                                    # 「重新同步」
    with _as("g-a"):
        src = EG.get("race-1")["source"]
        assert "group" not in src and src["version"] == "4"
        assert CP.origin("race-1")["group"] is False
    assert c.get(status_url("race-1")).json()["target"] == "origin"


def test_cairns_other_conflict_is_not_called_the_groups(c, app, cairn, stores):
    _imported(c, cairn)
    cairn.busy = True
    r = _push(c)
    assert r.json()["detail"]["code"] != "CAIRN_GROUP"
