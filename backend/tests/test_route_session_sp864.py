"""
SP-864 (SP-641 Q1-2 / Q1-3): a session made from a whole route —
  * the runnable legs' power is a narrow band centred on the easy intensity the 坡段表 uses
    (Ctx.easy_frac ± EASY_HALF_BAND), so a climb's name says the 坡段表's time (it was 65–80 % CP:
    爬坡 1 20′ in the table, 24′ on the step);
  * a flat / rolling leg shorter than 500 m becomes part of the neighbouring climb or descent (fewer
    lap presses: 貓空 17K made 16 steps).
SP-864 Q1 (answered 2026-10-21):
  * (a) the 坡段表's climbs are walked along the profile piece by piece (user_templates.route_walk), as
    the step chart and the route session's steps — a climb whose grade goes up and down takes longer than
    its average grade says, and the step's name is the table's time on a real route too; a hill repeat's
    「爬 … m · 約 …′」 (route_library.climb_secs) walks it the same way;
  * Q1-2: a steep climb's (HR cap) name says the easy run's time, as the table; its `est` stays at the
    target's speed (the chart's).
Synthetic profiles and the demo GPX (backend/demo/data), no real data.
"""
import copy

import pytest

from backend.engine import route_library as RL
from backend.engine import route_session as RS
from backend.engine import user_templates as UT
from backend.engine import workout_steps as WS
from backend.tests.test_route_session_sp641 import CTX, course_of, maokong
from backend.tests.test_user_templates import _pin, st  # noqa: F401 (_pin: autouse)

EASY = [("easy", "easy", CTX.easy_frac())]
# with an easy speed of its own: the HR cap's speed (its band's middle) is not the easy run's
CTX_V = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, terrain="trail", v_easy=10.0)
# a climb that steepens and eases: 10 %, 0 %, 10 % (8 % on average: one climb)
WAVY = [(1.0, 0.0), (0.6, 0.10), (0.3, 0.0), (0.6, 0.10), (1.0, 0.0)]


def test_the_tables_climb_is_walked_along_the_profile():
    course, prof = course_of(WAVY)
    assert len(course["climbs"]) == 1
    cl = course["climbs"][0]
    flat = UT.course_table(course, CTX, EASY)["climbs"][0]["est"]["easy"]          # its average grade
    walked = UT.course_table(course, CTX, EASY, prof)["climbs"][0]
    v = UT._flat_kmh(CTX.easy_frac(), CTX)
    want = UT.walk_secs(UT.route_walk(prof, CTX), cl["start_km"], cl["end_km"], v)
    assert walked["est"]["easy"]["t_s"] == round(want)
    assert walked["est"]["easy"]["t_s"] > flat["t_s"]                     # Minetti: 10 % + 0 % is slower than 8 %
    assert walked["est"]["easy"]["pace_s_per_km"] == pytest.approx(want / (cl["dist_m"] / 1000), abs=0.1)
    assert walked["label"] == UT._(UT.CLIMB_LABEL, m=round(cl["gain_m"]), t=UT._mins(want))
    # the segments keep their one grade
    assert UT.course_table(course, CTX, EASY, prof)["segments"] == UT.course_table(course, CTX, EASY)["segments"]


def test_a_wavy_climbs_step_says_the_tables_time():
    course, prof = course_of(WAVY)
    out = RS.build(course, prof, "x", {"kind": "gpx", "id": "1"}, CTX)
    table = UT.course_table(course, CTX, EASY, prof)
    lg, s = next((lg, s) for lg, s in zip(out["legs"], out["steps"]["items"]) if lg["type"] == "climb")
    assert (lg["start_km"], lg["end_km"]) == pytest.approx((table["climbs"][0]["start_km"], table["climbs"][0]["end_km"]), abs=1e-3)
    assert s["note"] == table["climbs"][0]["label"]


def test_a_steep_climb_says_the_easy_runs_time():
    course, prof = course_of([(1.0, 0.0), (1.0, 0.20), (1.0, 0.0)])
    out = RS.build(course, prof, "x", {"kind": "gpx", "id": "1"}, CTX_V)
    table = UT.course_table(course, CTX_V, [("easy", "easy", CTX_V.easy_frac())], prof)
    lg, s = next((lg, s) for lg, s in zip(out["legs"], out["steps"]["items"]) if lg["type"] == "climb")
    assert lg["cls"] == "steep" and s["target"]["type"] == "hr"
    assert s["note"] == table["climbs"][0]["label"]                       # the easy run's time, as the table
    # its estimate stays at the HR cap's speed (the chart walks the step at it): slower than easy here
    w = UT.route_walk(prof, CTX_V)
    v_hr = UT._flat_kmh(WS.resolve(s, CTX_V).frac, CTX_V)
    assert v_hr < UT._flat_kmh(CTX_V.easy_frac(), CTX_V)
    assert s["dur"]["est"] == pytest.approx(UT.walk_secs(w, lg["start_km"], lg["end_km"], v_hr), abs=1)
    assert UT._mins(s["dur"]["est"]) != UT._mins(table["climbs"][0]["est"]["easy"]["t_s"])


def test_maokong_climb_steps_say_the_tables_time():
    course, prof = maokong()
    out = RS.build(course, prof, "貓空", {"kind": "gpx", "id": "1"}, CTX)
    table = {c["no"]: c for c in UT.course_table(course, CTX, EASY, prof)["climbs"]}
    same = 0
    for lg, s in zip(out["legs"], out["steps"]["items"]):
        if lg["type"] != "climb":
            continue
        cl = table[lg["climb_no"]]
        # a climb that took in a short stretch (a flat between two climbs) says that stretch too
        if abs(lg["start_km"] - cl["start_km"]) < 0.05 and abs(lg["end_km"] - cl["end_km"]) < 0.05:
            assert s["note"] == cl["label"], (s["note"], cl["label"])
            same += 1
    assert same >= 3


def test_a_hill_repeats_climb_is_walked_along_the_profile():
    course, prof = course_of(WAVY)
    cl = course["climbs"][0]
    steps = {"items": [{"kind": "repeat", "times": 3, "items": [
        st("work", 240, None, "上坡"), st("rest", {"type": "open"}, None, "走下坡")]}]}
    out, n = RL.apply(copy.deepcopy(steps), prof, {"kind": "gpx", "id": "1"}, cl, CTX)
    assert n == 1
    up = out["items"][0]["items"][0]
    r = WS.resolve(up, CTX)
    v = UT._flat_kmh(r.frac if r.frac is not None else WS.NONE_IF["work"], CTX)
    want = UT.walk_secs(UT.route_walk(prof, CTX), cl["start_km"], cl["end_km"], v)
    assert up["dur"]["est"] == round(want)
    assert up["note"] == UT._(UT.CLIMB_LABEL, m=round(cl["gain_m"]), t=UT._mins(want))
    # longer than at its average grade
    assert want > cl["dist_m"] / 1000 / (v / UT._gf(cl["grade"])) * 3600


def test_every_climb_table_gets_the_routes_profile(monkeypatch):
    """The routes page (an uploaded route), the editor's route pick (POST /steps/route) and a template's
    坡段表 (gpx/segments) all time the climbs on the profile."""
    from backend.tests.test_plan_store import API
    from backend.tests.test_route_library_sp640 import LAPI, LEnv, upload
    from backend.tests.test_user_templates import UAPI, gpx_bytes
    seen = []
    real = UT.course_table

    def spy(course, c, levels, prof=None):
        seen.append(bool(prof and len(prof.get("km") or []) >= 2))
        return real(course, c, levels, prof)

    monkeypatch.setattr(UT, "course_table", spy)
    with LEnv(monkeypatch) as e:
        r = upload(e)
        assert e.c.get(f"{LAPI}/{r['id']}").status_code == 200
        res = e.c.post(f"{API}/steps/route", json={"kind": "hike", "route": {"src": "gpx", "id": r["id"]},
                                                  "steps": {"items": [st("work", 600, None, "上坡")]}})
        assert res.status_code == 200, res.text
        t = e.c.post(UAPI, json={"name": "坡", "cats": ["trail"], "steps": {"items": [st("work", 600, None, "上坡")]}}).json()
        assert e.c.post(f"{UAPI}/{t['id']}/gpx", files={"file": ("slope.gpx", gpx_bytes(), "application/gpx+xml")}).status_code == 200
        assert e.c.get(f"{UAPI}/{t['id']}/gpx/segments").status_code == 200
    assert seen == [True, True, True]


def test_the_runnable_power_is_a_narrow_band_on_the_easy_intensity():
    course, prof = course_of([(1.0, 0.0), (1.5, 0.06), (1.0, 0.0)])
    out = RS.build(course, prof, "x", {"kind": "gpx", "id": "1"}, CTX)
    f = CTX.easy_frac()
    runs = [st for lg, st in zip(out["legs"], out["steps"]["items"]) if lg["cls"] == "run"]
    assert runs
    for st in runs:
        t = st["target"]
        assert t["type"] == "power" and t["mode"] == "pct"
        assert t["hi"] - t["lo"] == pytest.approx(2 * RS.EASY_HALF_BAND, abs=0.002)
        assert (t["lo"] + t["hi"]) / 2 == pytest.approx(f, abs=0.001)
        assert t["ep"][0] < t["ep"][1]                                     # Effort Pace still there


def test_a_climb_step_says_the_tables_time():
    course, prof = course_of([(1.0, 0.0), (1.5, 0.06), (1.0, 0.0)])
    out = RS.build(course, prof, "x", {"kind": "gpx", "id": "1"}, CTX)
    table = UT.course_table(course, CTX, [("easy", "easy", CTX.easy_frac())])
    assert len(table["climbs"]) == 1
    lg, st = next((lg, st) for lg, st in zip(out["legs"], out["steps"]["items"]) if lg["type"] == "climb")
    assert lg["cls"] == "run"
    assert st["note"] == table["climbs"][0]["label"]                      # 「爬 … m · 約 …′」, the same minutes


def test_a_short_flat_goes_into_the_neighbouring_climb_or_descent():
    # climb 1 km +8 %, 400 m flat, descent 1 km −8 %: the flat is part of a neighbour, not a step of its own
    course, prof = course_of([(1.0, 0.08), (0.4, 0.0), (1.0, -0.08)])
    legs = RS.legs_of(course, prof)
    assert [lg["type"] for lg in legs] == ["climb", "down"]
    assert sum(lg["dist_m"] for lg in legs) == pytest.approx(2400, abs=20)
    # 600 m of flat stays a step
    course, prof = course_of([(1.0, 0.08), (0.6, 0.0), (1.0, -0.08)])
    assert [lg["type"] for lg in RS.legs_of(course, prof)] == ["climb", "flat", "down"]
    # a short flat finish after a descent: the descent runs to the end
    course, prof = course_of([(1.0, 0.08), (1.0, -0.08), (0.3, 0.0)])
    legs = RS.legs_of(course, prof)
    assert legs[-1]["type"] == "down" and legs[-1]["end_km"] == pytest.approx(2.3, abs=0.01)
    # a route that is only a short flat stays one step
    course, prof = course_of([(0.3, 0.0)])
    assert [lg["type"] for lg in RS.legs_of(course, prof)] == ["flat"]


def test_maokong_needs_fewer_steps(monkeypatch):
    course, prof = maokong()
    now = RS.legs_of(course, prof)
    assert all(lg["dist_m"] >= RS.FLAT_MIN_M for lg in now if lg["type"] == "flat")
    assert sorted(lg["climb_no"] for lg in now if lg["type"] == "climb") == [c["no"] for c in course["climbs"]]
    monkeypatch.setattr(RS, "FLAT_MIN_M", RS.MIN_LEG_M)                  # SP-641's rule
    before = RS.legs_of(course, prof)
    assert len(now) < len(before)
