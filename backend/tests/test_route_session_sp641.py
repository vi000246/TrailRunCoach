"""
SP-641: a session made from a whole route (課表 › 路線 → 「從這條路線產生課」;
engine/route_session.py, POST /steps/from-route) — one step per climb, the race calculator's
「匯出至課表」 way (watch_export.steps_for: lap-button legs named after where they end):
  * climbs by SP-639's rule (≥ 5 %, ≥ 30 m, a drop of max(10 m, 5 %) allowed): one step each;
  * legs under 200 m go into the neighbour whose grade is nearer; ≤ watch_export.LAP_MAX steps;
  * targets by SP-632: runnable (−3 … +15 %, ep_ok) power or Effort Pace, steep HR or RPE, descents none;
  * each step ends with the lap button, named 「爬 120 m · 約 6′」 (SP-640's label);
  * the session is the user's own (added, origin custom): nothing scheduled is replaced, and the watch
    gets the steps and targets only.
"""
import json
import re
from pathlib import Path

import pytest

from backend.api import route_library as RLA
from backend.api import routes as RA
from backend.engine import route_session as RS
from backend.engine import user_templates as UT
from backend.engine import workout_steps as WS
from backend.engine.racepower import watch_export as WE
from backend.tests.test_plan_store import API
from backend.tests.test_route_library_sp640 import LEnv, _mine_track, _mine_view, upload
from backend.tests.test_user_templates import _pin, gpx_bytes  # noqa: F401 (_pin: autouse)

MAOKONG = (Path(__file__).resolve().parents[1] / "demo" / "data" / "maokong_17k.gpx").read_bytes()
LABEL = re.compile(r"^爬 (\d+) m · 約 (\d+)′$")
CTX = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, terrain="trail")


def maokong():
    g = UT.parse_profile(MAOKONG, "maokong_17k.gpx")
    return g["course"], g["profile"]


def build(ctx=CTX, course=None, prof=None):
    if course is None:
        course, prof = maokong()
    return RS.build(course, prof, "貓空", {"kind": "gpx", "id": "1"}, ctx)


def _seg(i, a, b, g):
    return {"i": i, "start_km": a, "end_km": b, "dist_m": (b - a) * 1000.0, "grade": g, "climb_no": None}


def course_of(pieces):
    """A course from [(km, grade)] pieces: its profile, grade segments and SP-639's climbs."""
    km, z, segs, x, h = [0.0], [100.0], [], 0.0, 100.0
    for i, (d, g) in enumerate(pieces, 1):
        n = max(1, int(round(d / 0.01)))
        for _j in range(n):
            x += d / n
            h += g * d / n * 1000.0
            km.append(round(x, 4))
            z.append(round(h, 2))
        segs.append(_seg(i, round(x - d, 4), round(x, 4), g))
    climbs = UT.climbs_of([k * 1000.0 for k in km], z)
    return {"v": UT.COURSE_V, "rule": UT.climb_rule(), "segments": segs, "climbs": climbs}, {"km": km, "z": z}


# ---------------------------------------------------------------------------
# one step per climb
# ---------------------------------------------------------------------------

def test_one_step_per_climb_on_the_whole_route():
    course, prof = maokong()
    out = build()
    legs, items = out["legs"], out["steps"]["items"]
    assert len(legs) == len(items) <= WE.LAP_MAX
    # contiguous from the start to the finish
    assert legs[0]["start_km"] == pytest.approx(prof["km"][0], abs=1e-6)
    assert legs[-1]["end_km"] == pytest.approx(prof["km"][-1], abs=1e-6)
    assert all(a["end_km"] == pytest.approx(b["start_km"], abs=1e-9) for a, b in zip(legs, legs[1:]))
    # every climb of SP-639's rule is one step of its own
    nos = [lg["climb_no"] for lg in legs if lg["type"] == "climb"]
    assert sorted(nos) == [c["no"] for c in course["climbs"]] and len(course["climbs"]) >= 3
    for c in course["climbs"]:
        lg = next(x for x in legs if x["climb_no"] == c["no"])
        assert lg["start_km"] <= c["start_km"] + 0.25 and lg["end_km"] >= c["end_km"] - 1e-6
        assert lg["climb_gain_m"] == pytest.approx(c["gain_m"], abs=0.1)
        assert lg["gain_m"] == pytest.approx(c["gain_m"], rel=0.25)
    # no leg under 200 m (they went into a neighbour)
    assert min(lg["dist_m"] for lg in legs) >= RS.MIN_LEG_M
    assert out["km"] == pytest.approx(prof["km"][-1] - prof["km"][0], abs=0.01)


def test_each_step_ends_with_the_lap_button_named_like_the_route_picker():
    out = build()
    for lg, st in zip(out["legs"], out["steps"]["items"]):
        assert st["dur"]["type"] == "open" and st["dur"]["est"] == round(lg["est_s"]) > 0
        assert len(st["note"]) <= WS.MAX_NOTE
        if lg["type"] == "climb":
            m = LABEL.match(st["note"])
            assert m, st["note"]
            # the climb's own gain, as the 坡段表 and the hill-repeat label (SP-639 / SP-640)
            assert int(m.group(1)) == round(lg["climb_gain_m"]) and int(m.group(2)) == max(1, round(lg["est_s"] / 60))
    assert out["minutes"] == round(sum(lg["est_s"] for lg in out["legs"]) / 60)
    # the route goes with the session (the chart), its source kept; no hill-repeat climb
    r = out["steps"]["route"]
    assert r["src"] == {"kind": "gpx", "id": "1"} and r["name"] == "貓空" and "climb" not in r and "rep_km" not in r
    assert WS.normalize(out["steps"])["route"]["src"] == r["src"]


def test_a_short_leg_goes_into_the_neighbour_with_the_nearer_grade():
    # descent 1 km −6 %, 120 m at −1 %, climb 1 km +8 %: the short flat is nearer the descent (−6) than the
    # climb (+8) → it goes down with the descent; then a flat km
    course, prof = course_of([(1.0, -0.06), (0.12, -0.01), (1.0, 0.08), (1.0, 0.0)])
    legs = RS.legs_of(course, prof)
    assert [lg["type"] for lg in legs] == ["down", "climb", "flat"]
    assert legs[0]["end_km"] == pytest.approx(1.12, abs=0.02)
    assert min(lg["dist_m"] for lg in legs) >= RS.MIN_LEG_M
    # the other way round: 120 m at +6 % between a descent and a climb goes into the climb
    course, prof = course_of([(1.0, -0.06), (0.12, 0.06), (1.0, 0.08)])
    legs = RS.legs_of(course, prof)
    assert [lg["type"] for lg in legs] == ["down", "climb"] and legs[1]["start_km"] == pytest.approx(1.0, abs=0.02)


def test_no_more_steps_than_the_watch_takes():
    pieces = []
    for _i in range(40):
        pieces += [(0.5, 0.10), (0.5, -0.10)]
    course, prof = course_of(pieces)
    assert len(course["climbs"]) > WE.LAP_MAX
    out = RS.build(course, prof, "鋸齒", {"kind": "gpx", "id": "2"}, CTX)
    assert len(out["steps"]["items"]) <= WE.LAP_MAX and out["merged"] > 0 and out["notes"]


# ---------------------------------------------------------------------------
# targets (SP-632's rule)
# ---------------------------------------------------------------------------

def _by_type(out):
    return {k: [(lg, st) for lg, st in zip(out["legs"], out["steps"]["items"]) if lg["cls"] == k]
            for k in ("run", "steep", "down")}


def test_targets_runnable_power_or_effort_pace_steep_heart_rate_descents_none():
    course, prof = course_of([(1.5, 0.06), (0.8, -0.08), (1.0, 0.20), (1.0, -0.10), (1.0, 0.01)])
    out = RS.build(course, prof, "x", {"kind": "gpx", "id": "1"}, CTX)
    g = _by_type(out)
    assert g["run"] and g["steep"] and g["down"]
    for lg, st in g["run"]:
        t = st["target"]
        assert t["type"] == "power" and t["mode"] == "pct" and t["lo"] < t["hi"]
        assert 120 <= t["ep"][0] < t["ep"][1] <= 1200                    # Effort Pace for COROS
    for lg, st in g["steep"]:
        assert st["target"] == {"type": "hr", "mode": "zone", "zone": "aet"} and lg["grade"] > WE.EP_GRADE[1]
    for lg, st in g["down"]:
        assert st["target"] == {"type": "none"} and "控制、安全" in st["note"]
    # the steps as the editor stores them (ep kept on the runnable ones)
    norm = WS.normalize(out["steps"])
    assert [x["target"] for x in norm["items"]] == [x["target"] for x in out["steps"]["items"]]


def test_the_runnable_test_is_ep_ok():
    # +14 % is runnable (EP up to +15 %), +16 % is steep; a runnable leg high up (≥ 2800 m) has no Effort Pace
    course, prof = course_of([(1.0, 0.14), (1.0, -0.05), (1.0, 0.16)])
    out = RS.build(course, prof, "x", {"kind": "gpx", "id": "1"}, CTX)
    assert [lg["cls"] for lg in out["legs"]] == ["run", "down", "steep"]
    hi = {**prof, "z": [v + 3000.0 for v in prof["z"]]}
    out = RS.build(course, hi, "x", {"kind": "gpx", "id": "1"}, CTX)
    st = out["steps"]["items"][0]
    assert st["target"]["type"] == "power" and "ep" not in st["target"]


def test_without_power_or_heart_rate():
    course, prof = course_of([(1.5, 0.06), (1.0, -0.10), (1.0, 0.20), (1.0, -0.10)])
    no_cp = RS.build(course, prof, "x", {"kind": "gpx", "id": "1"}, WS.Ctx(lthr=170.0, aet=150.0, terrain="trail"))
    run_t = _by_type(no_cp)["run"][0][1]["target"]
    assert run_t["type"] == "hr" and run_t["zone"] == "aet" and run_t["ep"]        # HR on the watch, EP on COROS
    none = RS.build(course, prof, "x", {"kind": "gpx", "id": "1"}, WS.Ctx(terrain="trail"))
    steep = _by_type(none)["steep"][0]
    assert steep[1]["target"]["type"] == "rpe" and steep[1]["target"]["up"] == round(steep[0]["gain_m"])
    assert _by_type(none)["run"][0][1]["target"]["type"] == "rpe"


# ---------------------------------------------------------------------------
# the API: the routes page's button → the 課表 editor
# ---------------------------------------------------------------------------

def _gen(e, src, rid, **kw):
    return e.c.post(f"{API}/steps/from-route", json={"kind": "hike", "terrain": "trail", "route": {"src": src, "id": rid}, **kw})


def test_from_an_uploaded_route_saved_as_the_users_own_session(monkeypatch):
    with LEnv(monkeypatch) as e:
        r = upload(e, MAOKONG, "maokong_17k.gpx")
        res = _gen(e, "gpx", r["id"], day="2026-10-04")
        assert res.status_code == 200, res.text
        b = res.json()
        s = b["steps"]
        assert b["name"] == r["name"] and b["km"] == pytest.approx(r["km"], abs=0.05) and b["gain_m"] > 0
        assert b["minutes"] > 60 and len(b["legs"]) == len(s["items"]) and b["notes"]
        assert s["route"]["src"] == {"kind": "gpx", "id": str(r["id"])}
        assert b["title"] and r["name"] in b["title"]
        # the chart: each step on its own stretch of the route (the editor's distance axis)
        chk = e.c.post(f"{API}/steps/check", json={"kind": "hike", "terrain": "trail", "steps": s}).json()
        el = chk["elev"]
        for lg, rr in zip(b["legs"], el["r"]):
            assert rr[0] == pytest.approx(lg["start_km"] - b["legs"][0]["start_km"], abs=0.05)
            assert rr[1] == pytest.approx(lg["end_km"] - b["legs"][0]["start_km"], abs=0.05)
        assert el["complete"]
        # saved on a day whose scheduled session the user changed, and on a day with an automatic one:
        # added beside them as the user's own (origin custom); the automatic plan replaces neither
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        edited = next(x for x in ss if x["day"] == "2026-10-04" and x["kind"] == "long")
        auto = next(x for x in ss if x["day"] == "2026-10-11" and x["kind"] == "long")
        assert e.c.patch(f"{API}/sessions/{edited['uid']}", json={"minutes": 95}).status_code == 200
        mine = []
        for day in ("2026-10-04", "2026-10-11"):
            sv = e.c.post(f"{API}/sessions", json={"day": day, "kind": "hike", "title": b["title"],
                                                   "minutes": b["minutes"], "terrain": "trail", "steps": s})
            assert sv.status_code == 200, sv.text
            assert sv.json()["origin"] == "custom" and sv.json()["steps"]["route"]["src"] == s["route"]["src"]
            mine.append(sv.json()["uid"])
        e.c.post(f"{API}/reconcile")                                     # the automatic plan again
        after = {x["uid"]: x for x in e.c.get(f"{API}/sessions").json()["sessions"]}
        assert after[edited["uid"]]["day"] == "2026-10-04" and after[edited["uid"]]["minutes"] == 95
        a = after[auto["uid"]]                                           # moved to a free day, not dropped
        assert a["state"] == "active" and a["title"] == auto["title"] and a["minutes"] == auto["minutes"]
        for uid in mine:
            kept = after[uid]
            assert kept["state"] == "active" and kept["origin"] == "custom"
            assert [x["note"] for x in kept["steps"]["items"]] == [x["note"] for x in s["items"]]
        # pushed: the steps and targets only, never the route
        assert e.c.post(f"{API}/push-coros?scope=day&day=2026-10-04").status_code == 200
        prog = next(p for p in e.fake.programs.values() if b["title"][:8] in p["name"])
        names = [x["name"] for x in prog["exercises"] if not x.get("isGroup")]
        assert names[:len(s["items"])] == [x["note"] for x in s["items"]][:len(names)]
        dump = json.dumps(prog, ensure_ascii=False)
        assert "maokong" not in dump and '"route"' not in dump and '"lat"' not in dump


def test_the_watch_preview_has_the_steps_and_targets_only(monkeypatch):
    with LEnv(monkeypatch) as e:
        r = upload(e, MAOKONG, "maokong_17k.gpx")
        s = _gen(e, "gpx", r["id"]).json()["steps"]
        chk = e.c.post(f"{API}/steps/check", json={"kind": "hike", "terrain": "trail", "steps": s}).json()
        w = chk["watch"]
        dump = json.dumps(w, ensure_ascii=False)
        assert "maokong" not in dump and '"km"' not in dump
        assert len(w["lines"]) >= len(s["items"])


def test_from_my_segments(monkeypatch, tmp_path):
    view = _mine_view()
    monkeypatch.setattr(RA, "_index", lambda: view)
    monkeypatch.setattr(RA, "STORE", _mine_track(tmp_path))
    monkeypatch.setattr(RA, "_names", lambda v=None: {"c1": "壽山坡"})
    with LEnv(monkeypatch) as e:
        b = _gen(e, "seg", "c1").json()
        s = b["steps"]
        assert s["route"]["src"] == {"kind": "seg", "id": "c1"} and b["name"] == "壽山坡"
        assert any(LABEL.match(x["note"]) for x in s["items"])


def test_errors(monkeypatch):
    with LEnv(monkeypatch) as e:
        assert _gen(e, "gpx", 999).status_code == 404
        assert _gen(e, "nope", 1).status_code == 400
        assert e.c.post(f"{API}/steps/from-route", json={"kind": "hike"}).status_code == 400
