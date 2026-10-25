"""
SP-862: picking a route for a hill repeat (課表 編輯器 ▲ 路線, POST /steps/route, route_library.apply) made each
rep's climb end with the lap button at the top (SP-640) but kept the recovery after it a fixed time — often
too short to get back to the foot before the next rep. Now that recovery (kind rest, timed or a distance)
also ends with the lap button, back at the foot: 「回到坡底按圈 · 約 N′」, its estimate the way down at its
intensity (descent_secs, the step chart's speed: a recovery with no target walks). One already to the lap
button (「走下坡」) stays as written; a rep that is not a climb (strides) is unchanged. The COROS push has the
same steps (both lap-button, still one group). Synthetic route (gpx_bytes), no real data.
"""
import copy
import re

import pytest

from backend.engine import route_library as RL
from backend.engine import user_templates as UT
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API
from backend.tests.test_route_library_sp640 import LEnv, upload
from backend.tests.test_user_templates import _pin, st  # noqa: F401 (_pin: autouse)

BACK = re.compile(r"^回到坡底按圈 · 約 (\d+)′$")

TIMED_HILL = {"items": [
    st("warm", 900, {"type": "auto", "intent": "easy"}, "平路暖身"),
    {"kind": "repeat", "times": 5, "last_rest": True, "note": "上坡 ×5", "items": [
        st("work", 240, None, "上坡"),
        st("rest", 120, {"type": "none"}, "慢跑下坡")]},
    st("cool", 600, {"type": "auto", "intent": "easy"}, "緩和")]}


def _rep(items):
    rep = next(x for x in items if x["kind"] == "repeat")
    return rep["items"][0], rep["items"][1]


def _apply(e, steps, kind="hike"):
    r = upload(e)
    res = e.c.post(f"{API}/steps/route", json={"kind": kind, "steps": steps, "route": {"src": "gpx", "id": r["id"]}})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_recovery_after_the_climb_ends_back_at_the_foot(monkeypatch):
    with LEnv(monkeypatch) as e:
        b = _apply(e, {"items": copy.deepcopy(TIMED_HILL["items"])})
        s = b["steps"]
        assert b["applied"] == 1
        up, back = _rep(s["items"])
        assert up["dur"]["type"] == "open"                                   # SP-640: the top
        assert back["dur"]["type"] == "open" and back["dur"]["est"] > 0     # SP-862: the foot
        m = BACK.match(back["note"])
        assert m and int(m.group(1)) == max(1, round(back["dur"]["est"] / 60))
        assert back["target"] == {"type": "none"}                            # the target as it was
        # the estimate: the climb's way down at a walk (a recovery with no target), Minetti going down
        cl = s["route"]["climb"]
        v = WS.WALK_KMH / UT._gf(-cl["grade"])
        assert back["dur"]["est"] == pytest.approx(cl["dist_m"] / 1000 / v * 3600, abs=1)
        # the chart: every recovery comes back to the foot (rep_km), not part-way up
        chk = e.c.post(f"{API}/steps/check", json={"kind": "hike", "steps": s}).json()
        el, order = chk["elev"], chk["order"]
        ri = [i for i, o in enumerate(order) if o["kind"] == "rest"]
        assert len(ri) == 5 and all(el["r"][i][1] == pytest.approx(s["route"]["rep_km"] - s["route"]["km"][0], abs=0.02)
                                    for i in ri)


def test_pushed_to_coros_both_legs_are_lap_button_in_one_group(monkeypatch):
    with LEnv(monkeypatch) as e:
        s = _apply(e, {"items": copy.deepcopy(TIMED_HILL["items"])})["steps"]
        sv = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "hike", "title": "反覆爬坡", "steps": s})
        assert sv.status_code == 200, sv.text
        assert e.c.post(f"{API}/push-coros?scope=day&day=2026-10-03").status_code == 200
        prog = next(p for p in e.fake.programs.values() if "反覆爬坡" in p["name"])
        group = next(x for x in prog["exercises"] if x.get("isGroup"))
        kids = [x for x in prog["exercises"] if x.get("groupId") == str(group["id"])]
        assert group["sets"] == 5 and len(kids) == 2
        assert all(k["targetType"] == CW.TARGET_OPEN for k in kids)
        assert BACK.match(kids[1]["name"])


def test_a_recovery_already_to_the_lap_and_strides_stay_as_written():
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, terrain="trail")
    prof = {"km": [0.0, 1.0, 2.0, 3.0], "z": [100.0, 100.0, 200.0, 200.0]}
    climb = {"no": 1, "gain_m": 100.0, "dist_m": 1000.0, "grade": 0.1, "start_km": 1.0, "end_km": 2.0}
    src = {"kind": "gpx", "id": "1"}
    # 「走下坡」 to the lap button already: as written (no estimate added)
    walk = {"items": [{"kind": "repeat", "times": 3, "items": [st("work", {"type": "open"}, None, "上坡"),
                                                              st("rest", {"type": "open"}, None, "走下坡")]}]}
    out, n = RL.apply(walk, prof, src, climb, c)
    assert n == 1 and _rep(out["items"])[1] == st("rest", {"type": "open"}, None, "走下坡")
    # strides: not a climb rep, the recovery stays timed
    strides = {"items": [{"kind": "repeat", "times": 6, "items": [st("other", 20, None, "快步跑"), st("rest", 40, None, "慢跑")]}]}
    out, n = RL.apply(strides, prof, src, climb, c)
    assert n == 0 and _rep(out["items"])[1]["dur"] == {"type": "time", "value": 40}
    # no climb picked (0): nothing changes
    out, n = RL.apply(copy.deepcopy(TIMED_HILL), prof, src, None, c)
    assert n == 0 and _rep(out["items"])[1]["dur"] == {"type": "time", "value": 120}
    # a recovery with a target: its own speed (faster than a walk → a shorter estimate)
    jog = {"items": [{"kind": "repeat", "times": 3, "items": [st("work", 240, None, "上坡"),
                                                             st("rest", 120, {"type": "auto", "intent": "easy"}, "慢跑下坡")]}]}
    out, _n = RL.apply(jog, prof, src, climb, c)
    walk_est = RL.descent_secs(st("rest", 120, None), climb, c)
    assert _rep(out["items"])[1]["dur"]["est"] < walk_est


def test_english_label():
    from backend import i18n
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, terrain="trail")
    prof = {"km": [0.0, 1.0, 2.0, 3.0], "z": [100.0, 100.0, 200.0, 200.0]}
    climb = {"no": 1, "gain_m": 100.0, "dist_m": 1000.0, "grade": 0.1, "start_km": 1.0, "end_km": 2.0}
    with i18n.use_locale("en"):
        out, _n = RL.apply(copy.deepcopy(TIMED_HILL), prof, {"kind": "gpx", "id": "1"}, climb, c)
    assert _rep(out["items"])[1]["note"].startswith("Back at the foot, press lap")
