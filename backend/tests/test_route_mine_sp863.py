"""
SP-863 (SP-640's 「我的路段」 follow-ups):
  * a 「我的路段」 picked for a structure (POST /steps/route: the hill repeats' climb) or made into a
    session (POST /steps/from-route: every leg) is named with the time of your most recent effort on
    it, the model's estimate where there is none; the step's own estimate (the chart, the load) stays
    the model's;
  * the picker lists climbs and whole routes first; shared stretches and descents are `more` (under
    「更多」) unless starred.
"""
import copy
import re

import pytest

from backend.api import routes as RA
from backend.engine import route_library as RL
from backend.engine import route_session as RS
from backend.engine import routes as R
from backend.engine import user_templates as UT
from backend.engine import workout_steps as WS
from backend.tests.test_plan_store import API
from backend.tests.test_route_library_sp640 import LEnv, _mine_view
from backend.tests.test_user_templates import FREE_UPHILL, _pin  # noqa: F401 (_pin: autouse fixture)

C = WS.Ctx.of({"cp": 250.0}, "power", speeds={"v_easy": 6.0, "ep_kmh": 6.0, "terrain": "trail"})
LABEL = re.compile(r"^爬 (\d+) m · 約 (\d+)′$")


def _track(n=30, secs_per_pt=30.0, file="2026/a.fit"):
    """30 points 50 m apart north; 10–20 climb 100 m over 500 m; `t` (elapsed) and `cm` (moving) every 30 s."""
    lat = [25.0 + i * 0.05 / 111.2 for i in range(n)]
    e = [0.0 if i <= 10 else (10.0 * (i - 10) if i <= 20 else 100.0) for i in range(n)]
    t = [i * secs_per_pt for i in range(n)]
    return {"file": file, "lat": lat, "lon": [121.5] * n, "e": e, "d": [i * 0.05 for i in range(n)],
            "t": t, "cm": [x * 0.9 for x in t]}


def _item(**kw):
    it = copy.deepcopy(_mine_view()["items"]["c1"])
    it.update(kw)
    return it


# ---------------------------------------------------------------------------
# the picker's order
# ---------------------------------------------------------------------------

def test_climbs_and_routes_first_stretches_and_descents_under_more():
    base = _item()
    items = {
        "s1": {**base, "id": "s1", "kind": "stretch", "auto_name": "路段 A", "n_efforts": 9},
        "d1": {**base, "id": "d1", "kind": "descent", "auto_name": "下坡 B", "n_efforts": 8},
        "r1": {**base, "id": "r1", "kind": "route", "auto_name": "路線 C", "n_efforts": 2},
        "c1": {**base, "id": "c1", "kind": "climb", "auto_name": "爬坡 D", "n_efforts": 3},
        "s2": {**base, "id": "s2", "kind": "stretch", "auto_name": "路段 E", "n_efforts": 2},
    }
    rows = RL.mine_rows({"items": items}, {}, {"s2": True})
    assert [r["id"] for r in rows] == ["s2", "c1", "r1", "s1", "d1"]
    assert {r["id"]: r["more"] for r in rows} == {"s2": False, "c1": False, "r1": False, "s1": True, "d1": True}


# ---------------------------------------------------------------------------
# the last time you did it
# ---------------------------------------------------------------------------

def test_the_last_effort_s_time_on_the_whole_stretch_and_on_a_part():
    it = _item(efforts=[{"id": "old", "file": "2026/a.fit", "i0": 10, "i1": 20, "start": "2026-09-01T07:00:00", "elapsed_s": 500.0},
                        {"id": "back", "file": "2026/a.fit", "i0": 10, "i1": 20, "start": "2026-10-05T07:00:00",
                         "elapsed_s": 200.0, "dir": "reversed"},
                        {"id": "new", "file": "2026/a.fit", "i0": 10, "i1": 20, "start": "2026-10-01T07:00:00", "elapsed_s": 300.0}])
    lc = RL.last_clock(it, _track(), 0.5)
    assert lc["date"] == "2026-10-01" and lc["key"] == "elapsed_s"       # the latest in its own direction
    assert lc["secs"](0.0, 0.5) == 300.0                                  # the whole stretch: the list's time
    assert lc["secs"](0.1, 0.3) == pytest.approx(120.0)                   # 200 m of 500 m at 30 s / 50 m
    assert lc["secs"](0.3, 0.3) is None
    # a whole route ranks by moving time: its column (cm)
    rt = _item(time_key="moving_s", efforts=[{**it["efforts"][-1], "moving_s": 270.0}])
    assert RL.last_clock(rt, _track(), 0.5)["secs"](0.1, 0.3) == pytest.approx(108.0)
    # no timed effort / no track / no time column: None (the model's time)
    assert RL.last_clock(_item(efforts=[]), _track(), 0.5) is None
    assert RL.last_clock(it, None, 0.5) is None
    assert RL.last_clock(it, {k: v for k, v in _track().items() if k != "t"}, 0.5) is None


def _hill():
    km = [0.0, 1.0] + [1.0 + i / 10 for i in range(1, 11)] + [3.0]
    z = [0.0, 0.0] + [10.0 * i for i in range(1, 11)] + [100.0]
    return km, z


def test_the_climb_is_named_with_the_last_time_the_estimate_stays_the_model():
    km, z = _hill()
    climb = {"no": 1, "gain_m": 100.0, "dist_m": 1000.0, "grade": 0.1, "start_km": 1.0, "end_km": 2.0}
    steps = WS.normalize({"items": copy.deepcopy(FREE_UPHILL["items"])})
    prof = {"km": km, "z": z, "route_km": 3.0, "gain_m": 100.0, "name": "壽山坡"}
    actual = {"secs": lambda a, b: 1260.0 if (a, b) == (1.0, 2.0) else None, "date": "2026-10-01", "key": "elapsed_s"}
    out, n = RL.apply(steps, prof, {"kind": "seg", "id": "c1"}, climb, C, actual)
    model, _n = RL.apply(steps, prof, {"kind": "seg", "id": "c1"}, climb, C)
    leg = next(x for x in out["items"][1]["items"] if x["kind"] == "work")
    leg0 = next(x for x in model["items"][1]["items"] if x["kind"] == "work")
    assert n == 1 and LABEL.match(leg["note"]).group(2) == "21"
    assert leg["dur"] == leg0["dur"] and LABEL.match(leg0["note"]).group(2) != "21"


def test_a_route_session_names_each_leg_with_the_last_time():
    km, z = _hill()
    prof = {"km": km, "z": z}
    course = {"segments": [{"start_km": 0.0, "end_km": 1.0, "grade": 0.0}, {"start_km": 1.0, "end_km": 2.0, "grade": 0.1},
                           {"start_km": 2.0, "end_km": 3.0, "grade": 0.0}],
              "climbs": [{"no": 1, "start_km": 1.0, "end_km": 2.0, "gain_m": 100.0}]}
    actual = {"secs": lambda a, b: (b - a) * 900.0, "date": "2026-10-01", "key": "elapsed_s"}
    new = RS.build(course, prof, "壽山坡", {"kind": "seg", "id": "c1"}, C, actual)
    old = RS.build(course, prof, "壽山坡", {"kind": "seg", "id": "c1"}, C)
    climb = next(x for x in new["legs"] if x["type"] == "climb")
    assert climb["name"].startswith("爬 100 m · 約 15′") and climb["est_src"] == "last" and climb["last_s"] == 900
    assert [x["est_s"] for x in new["legs"]] == [x["est_s"] for x in old["legs"]]        # the chart / load: the model
    assert new["minutes"] == old["minutes"]
    assert any("2026-10-01" in n for n in new["notes"]) and not any("2026-10-01" in n for n in old["notes"])
    assert all(x["est_src"] == "model" for x in old["legs"])


def test_picking_my_segment_uses_the_last_effort(monkeypatch, tmp_path):
    view = _mine_view()
    store = R.RouteStore(tmp_path / "routes")
    store.save_track("2026/a.fit", _track())
    monkeypatch.setattr(RA, "_index", lambda: view)
    monkeypatch.setattr(RA, "STORE", store)
    monkeypatch.setattr(RA, "_names", lambda v=None: {"c1": "壽山坡"})
    with LEnv(monkeypatch) as e:
        b = e.c.post(f"{API}/steps/route", json={"kind": "hike", "steps": {"items": copy.deepcopy(FREE_UPHILL["items"])},
                                                 "route": {"src": "seg", "id": "c1"}}).json()
        assert b["last"] == {"date": "2026-10-01", "key": "elapsed_s"}
        leg = next(x for x in b["steps"]["items"][1]["items"] if x["kind"] == "work")
        m = LABEL.match(leg["note"])
        # the climb the rule finds is the whole stretch: the effort's own time on the list (600 s)
        assert m and m.group(2) == "10"
        s = e.c.post(f"{API}/steps/from-route", json={"route": {"src": "seg", "id": "c1"}}).json()
        assert s["last"] == {"date": "2026-10-01", "key": "elapsed_s"} and any(x["est_src"] == "last" for x in s["legs"])
        # a GPX route has no effort: the model's time, no `last`
        rows = e.c.get(f"{API}/routes/mine").json()["rows"]
        assert [(x["id"], x["more"]) for x in rows] == [("c1", False), ("f1", True)]
