"""
SP-639: the step chart on a template's route GPX —
  * hill repeats go up and back (a repeat whose work leg climbs: its recoveries come back down, not
    below the bottom; R1 / Q1-3 (b): a recovery too short to reach the bottom leaves the next rep
    climbing from where it stopped), the axis is the distance covered;
  * R1 / Q1-1 (a): no speed bonus on a descent of 15 % or steeper (chart and 坡段表);
  * the chart can be read on a time axis (each step's estimated seconds, the profile on time);
  * each step carries its climb / descent and estimated time (the tooltip);
  * the time ↔ distance of a step on the route by Minetti's running cost (the race calculator's
    GAP: algorithms/minetti.grade_factor, 10 % downhill floor) instead of km + climb ÷ 100;
  * the GPX upload keeps the course's grade segments and its climbs (≥ 5 %, ≥ 30 m, a drop of up
    to max(10 m, 5 % of the gain so far) does not end one), recomputed on read for a template
    stored before (or under other thresholds); the 坡段表 API.
"""
import math

import pytest

from backend.db.models import WorkoutTemplateUser
from backend.engine import user_templates as UT
from backend.engine import workout_steps as WS
from backend.engine.algorithms.minetti import grade_factor
from backend.tests.test_plan_store import API, Env, run
from backend.tests.test_user_templates import UAPI, _pin, gpx_bytes, st  # noqa: F401 (_pin: autouse fixture)

C = WS.Ctx.of({"cp": 250.0}, "power", speeds={"v_easy": 6.0, "ep_kmh": 6.0, "terrain": "trail"})
TG = {"type": "power", "mode": "pct", "lo": 0.77, "hi": 0.79}          # ≈ 0.78 CP = the easy anchor: 6 km/h


def hill(top_km=1.0, climb=100.0, flat_km=1.0, n=10):
    """Up `climb` m over the first `top_km` (n points), then flat for `flat_km`."""
    km = [top_km * i / n for i in range(n + 1)] + [top_km + flat_km]
    z = [climb * i / n for i in range(n + 1)] + [climb]
    return {"km": km, "z": z, "route_km": top_km + flat_km}


def v_on(g, v=6.0):
    return v / grade_factor(g)


# ---------------------------------------------------------------------------
# time ↔ distance on the route: Minetti
# ---------------------------------------------------------------------------

def test_steps_cover_the_route_at_the_minetti_speed():
    steps = WS.normalize({"items": [st("warm", 1200, TG), st("work", 600, TG),
                                    st("work", {"type": "distance", "value": 500}, TG), st("cool", 600, TG)]})
    el = UT.route_elevation(steps, C, hill())
    climb_s = 1.0 / v_on(0.1) * 3600.0                  # the 1 km at 10 % at the easy speed
    x1 = 1.0 + (1200 - climb_s) * 6.0 / 3600.0         # the rest of the 20′ on the flat
    x2 = x1 + 600 * 6.0 / 3600.0
    assert el["x"][1] == pytest.approx(x1, abs=0.002) and el["x"][2] == pytest.approx(x2, abs=0.002)
    assert el["x"][3] == pytest.approx(x2 + 0.5, abs=0.002)
    assert el["x"][4] == pytest.approx(x2 + 0.5 + 1.0, abs=0.002)   # past the end: 1 : 1 at 6 km/h
    # the seconds of each step: time steps as written, the distance step at its speed (flat here)
    assert el["s"] == pytest.approx([1200, 600, 0.5 / 6.0 * 3600, 600], abs=1)
    assert el["xt"][0] == 0 and el["xt"][-1] == pytest.approx(sum(el["s"]), abs=1)
    # climb / descent of each step: the whole 100 m in the warm-up
    assert el["up"][0] == pytest.approx(100, abs=1) and el["up"][1:] == [0, 0, 0] and el["down"] == [0, 0, 0, 0]
    assert el["r"][0][0] == 0.0 and el["r"][0][1] == pytest.approx(x1, abs=0.002) and el["complete"] is True
    # the profile path: only on the route (the axis goes on past its end), time on every point
    assert el["d"][0] == 0 and el["d"][-1] == pytest.approx(2.0, abs=0.01) and len(el["t"]) == len(el["d"])
    assert el["t"] == sorted(el["t"]) and el["note"]


def test_a_distance_step_on_a_climb_takes_its_minetti_time():
    steps = WS.normalize({"items": [st("work", {"type": "distance", "value": 500}, TG)]})
    el = UT.route_elevation(steps, C, hill())
    assert el["x"] == [0.0, 0.5]
    assert el["s"][0] == pytest.approx(0.5 / v_on(0.1) * 3600.0, rel=0.01)
    assert el["up"][0] == pytest.approx(50, abs=0.5) and el["complete"] is False
    # a lap-button step without an estimate: the chart's OPEN_CHART_S at that speed
    lap = WS.normalize({"items": [st("work", {"type": "open"}, TG)]})
    el = UT.route_elevation(lap, C, hill())
    assert el["s"] == [WS.OPEN_CHART_S]
    assert el["x"][1] == pytest.approx(v_on(0.1) * WS.OPEN_CHART_S / 3600.0, abs=0.002)


def test_a_cut_course_starts_at_zero():
    prof = hill()
    prof = {"km": [k + 5.0 for k in prof["km"]], "z": prof["z"]}
    el = UT.route_elevation(WS.normalize({"items": [st("work", 600, TG)]}), C, prof)
    assert el["x"][0] == 0.0 and el["d"][0] == 0.0 and el["r"][0][0] == 0.0


# ---------------------------------------------------------------------------
# hill repeats: up and back
# ---------------------------------------------------------------------------

def test_hill_repeats_go_up_and_come_back_down():
    rest = st("rest", 600, None, "慢慢走下來")
    steps = WS.normalize({"items": [{"kind": "repeat", "times": 3, "last_rest": True, "items": [
        st("work", 300, TG, "上坡"), rest]}]})
    el = UT.route_elevation(steps, C, hill())
    up_km = v_on(0.1) * 300 / 3600.0                    # each 5′ climb
    down_s = up_km / v_on(-0.1, WS.WALK_KMH) * 3600.0   # walked back down (no target: WALK_KMH)
    at_base = (600 - down_s) * WS.WALK_KMH / 3600.0     # the rest of the recovery at the bottom
    for i in range(3):
        w, r = 2 * i, 2 * i + 1
        assert el["r"][w] == pytest.approx([0.0, up_km], abs=0.002)        # every rep from the bottom
        assert el["r"][r] == pytest.approx([up_km, 0.0], abs=0.002)        # and back down
        assert el["up"][w] == pytest.approx(up_km * 100, abs=0.5) and el["down"][w] == 0
        assert el["down"][r] == pytest.approx(up_km * 100, abs=0.5) and el["up"][r] == 0
    # the axis is the distance covered: monotonic, 3 × (up + down + the rest at the bottom)
    assert el["x"] == sorted(el["x"])
    assert el["x"][-1] == pytest.approx(3 * (2 * up_km + at_base), abs=0.01)
    # the drawn profile climbs three times, back to the bottom each time
    z = [v for v in el["z"] if v is not None]
    peaks = [i for i in range(1, len(z) - 1) if z[i] > z[i - 1] and z[i] >= z[i + 1]]
    assert len(peaks) == 3 and all(z[p] == pytest.approx(up_km * 100, abs=1) for p in peaks)
    assert z[-1] == pytest.approx(0, abs=0.5)
    assert el["km"] == pytest.approx(up_km, abs=0.01) and el["complete"] is False
    # the elevation scale is the drawn part's (not the whole route's 0–100 m)
    assert el["z_max"] == pytest.approx(up_km * 100, abs=1) and el["z_min"] == pytest.approx(0, abs=0.5)


def test_a_short_recovery_leaves_the_next_rep_climbing_from_where_it_stopped():
    # 7′ up, 1′ jog back: not all the way down — SP-639 Q1-3 (b): the next rep climbs again from where
    # the recovery stopped (no jump back to the bottom, no vertical line on the chart)
    steps = WS.normalize({"items": [{"kind": "repeat", "times": 2, "last_rest": True, "items": [
        st("work", 420, TG), st("rest", 60, TG)]}]})
    el = UT.route_elevation(steps, C, hill())
    up_km = v_on(0.1) * 420 / 3600.0
    back = v_on(-0.1) * 60 / 3600.0
    assert el["r"][1] == pytest.approx([up_km, up_km - back], abs=0.002)
    assert el["r"][2][0] == pytest.approx(up_km - back, abs=0.002)
    assert el["r"][2][1] > el["r"][1][0]                  # the second climb goes on past the first one's top
    # the drawn path is continuous: every step starts where the one before ended
    for i in range(1, len(el["r"])):
        assert el["r"][i][0] == pytest.approx(el["r"][i - 1][1], abs=0.002)


def test_a_climb_to_the_top_after_a_short_recovery_climbs_only_what_is_left():
    # the climb picked on 路線 (lap button at the top): each rep ends at the top; after a 2′ walk down
    # the next one starts part-way and climbs only the rest of it
    prof = {**hill(), "climb": {"no": 1, "start_km": 0.0, "end_km": 1.0}, "rep_km": 0.0}
    steps = WS.normalize({"items": [{"kind": "repeat", "times": 2, "last_rest": True, "items": [
        {"kind": "work", "dur": {"type": "open", "est": 900}, "target": TG, "note": "爬 100 m"},
        st("rest", 120, None, "走下來")]}]})
    el = UT.route_elevation(steps, C, prof)
    back = v_on(-0.1, WS.WALK_KMH) * 120 / 3600.0
    assert el["r"][0] == pytest.approx([0.0, 1.0], abs=0.002)
    assert el["r"][1] == pytest.approx([1.0, 1.0 - back], abs=0.002)
    assert el["r"][2] == pytest.approx([1.0 - back, 1.0], abs=0.002)        # to the top again, no further
    assert el["up"][2] == pytest.approx(back * 100, abs=0.5) and el["up"][2] < el["up"][0]


def test_a_lap_button_recovery_walks_all_the_way_down():
    # 「走下坡」 until the lap button: pressed at the bottom — however long that takes, the next rep from there
    steps = WS.normalize({"items": [{"kind": "repeat", "times": 2, "last_rest": True, "items": [
        st("work", 420, TG), st("rest", {"type": "open"}, None, "走下坡")]}]})
    el = UT.route_elevation(steps, C, hill())
    up_km = v_on(0.1) * 420 / 3600.0
    assert el["r"][1] == pytest.approx([up_km, 0.0], abs=0.002) and el["r"][2][0] == pytest.approx(0.0, abs=0.002)
    assert el["s"][1] == pytest.approx(up_km / v_on(-0.1, WS.WALK_KMH) * 3600.0, rel=0.02)


def test_a_recovery_never_goes_below_where_the_repeats_started():
    # a long recovery still stops at the bottom of the repeats and spends the rest of its time there
    steps = WS.normalize({"items": [st("warm", 300, TG), {"kind": "repeat", "times": 2, "last_rest": True, "items": [
        st("work", 120, TG), st("rest", 900, TG)]}]})
    el = UT.route_elevation(steps, C, hill())
    base = el["r"][1][0]
    assert base > 0 and el["r"][2][1] == pytest.approx(base, abs=0.002) and el["r"][3][0] == pytest.approx(base, abs=0.002)


# ---------------------------------------------------------------------------
# SP-639 Q1-1 (a): no speed bonus on a steep descent (≤ −15 %)
# ---------------------------------------------------------------------------

def test_a_steep_descent_is_no_faster_than_the_flat():
    assert UT.STEEP_DOWN_GRADE == -0.15
    for g in (-0.15, -0.2, -0.247, -0.35, -0.5):
        assert UT._gf(g) >= 1.0, g                         # the flat pace or slower
        assert UT._gf(g) == pytest.approx(max(1.0, grade_factor(g))), g
    # a gentler descent keeps Minetti's bonus (at most 10 %), a climb is unchanged
    assert UT._gf(-0.08) == pytest.approx(grade_factor(-0.08)) and UT._gf(-0.08) < 1.0
    assert UT._gf(0.12) == pytest.approx(grade_factor(0.12))


def test_the_chart_walks_a_steep_descent_at_the_flat_speed():
    # down 200 m over 1 km (−20 %) at the easy speed: no faster than 6 km/h on the flat
    prof = {"km": [0.0, 1.0, 2.0], "z": [300.0, 100.0, 100.0], "route_km": 2.0}
    el = UT.route_elevation(WS.normalize({"items": [st("work", {"type": "distance", "value": 1000}, TG)]}), C, prof)
    assert el["s"][0] == pytest.approx(1.0 / 6.0 * 3600.0 * max(1.0, grade_factor(-0.2)), rel=0.01)
    assert el["s"][0] >= 600 - 1


def test_the_table_gives_a_steep_descent_no_speed_bonus():
    course = {"segments": [
        {"i": 1, "start_km": 0, "end_km": 1, "dist_m": 1000, "gain_m": 0, "loss_m": 247, "grade": -0.247, "max_grade": -0.3},
        {"i": 2, "start_km": 1, "end_km": 2, "dist_m": 1000, "gain_m": 0, "loss_m": 80, "grade": -0.08, "max_grade": -0.1}],
        "climbs": []}
    b = UT.course_table(course, C, [("easy", "輕鬆", 0.78)])
    flat = b["levels"][0]["flat_pace_s_per_km"]
    steep, gentle = (s["est"]["easy"]["pace_s_per_km"] for s in b["segments"])
    assert steep >= flat - 0.5                              # was 10 % faster before (4:23 on −24.7 %)
    assert gentle < flat                                    # a runnable descent is still a bit faster


def test_repeats_on_flat_ground_keep_going_forward():
    prof = {"km": [0.0, 5.0], "z": [100.0, 100.0], "route_km": 5.0}
    steps = WS.normalize({"items": [{"kind": "repeat", "times": 2, "last_rest": True, "items": [
        st("work", 300, TG), st("rest", 120, TG)]}]})
    el = UT.route_elevation(steps, C, prof)
    starts = [a for a, _b in el["r"]]
    ends = [b for _a, b in el["r"]]
    assert starts == sorted(starts) and all(b > a for a, b in el["r"]) and ends[-1] == pytest.approx(el["x"][-1], abs=0.002)


def test_steps_after_the_repeats_go_on_from_the_bottom():
    steps = WS.normalize({"items": [st("warm", 300, TG), {"kind": "repeat", "times": 2, "last_rest": True, "items": [
        st("work", 300, TG), st("rest", 600, None)]}, st("cool", 300, TG)]})
    el = UT.route_elevation(steps, C, hill())
    base = el["r"][1][0]
    assert el["r"][3][0] == pytest.approx(base, abs=0.002) and el["r"][4][1] == pytest.approx(base, abs=0.002)
    assert el["r"][5][0] == pytest.approx(base, abs=0.002) and el["r"][5][1] > base


# ---------------------------------------------------------------------------
# climbs: ≥ 5 %, ≥ 30 m, a drop ≤ max(10 m, 5 % of the gain) doesn't end one
# ---------------------------------------------------------------------------

def _course(seq, step=10.0):
    xs, zs = [0.0], [0.0]
    for length, dz in seq:
        n = int(round(length / step))
        for k in range(1, n + 1):
            xs.append(xs[-1] + step)
            zs.append(zs[-1] + dz / n)
    return xs, zs


def test_climb_thresholds():
    assert (UT.CLIMB_MIN_GRADE, UT.CLIMB_MIN_GAIN_M, UT.CLIMB_DROP_M, UT.CLIMB_DROP_FRAC) == (0.05, 30.0, 10.0, 0.05)
    xs, zs = _course([
        (600, 36), (400, -60),                       # A 36 m at 6 %: a climb
        (300, 25), (300, -40),                       # 25 m: too little
        (1500, 60), (600, -80),                      # 4 %: too gentle
        (500, 50), (100, -9), (500, 50), (1000, -120),   # D: a 9 m dip after 50 m doesn't end it
        (500, 50), (100, -12), (500, 50), (1000, -150),  # E: 12 m > 10 m → two climbs
        (3000, 300), (100, -12), (1000, 100)])       # F: 12 m < 5 % of 300 m → one climb
    cs = UT.climbs_of(xs, zs)
    assert [round(c["gain_m"]) for c in cs] == [36, 91, 50, 50, 388]
    assert all(c["grade"] >= 0.05 for c in cs)
    assert cs[0]["start_km"] == pytest.approx(0.0) and cs[0]["end_km"] == pytest.approx(0.6)
    assert [c["no"] for c in cs] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# the upload keeps the segments and climbs; the 坡段表
# ---------------------------------------------------------------------------

def _tpl(e):
    return e.c.post(UAPI, json={"name": "登山王", "cats": ["trail"], "steps": {"items": [
        st("warm", 600, {"type": "auto", "intent": "easy"}),
        {"kind": "repeat", "times": 4, "last_rest": True, "items": [
            st("work", 420, {"type": "power", "mode": "pct", "lo": 0.9, "hi": 0.95}, "上坡"),
            st("rest", 420, None, "走下來")]},
        st("cool", 300, {"type": "auto", "intent": "easy"})]}}).json()


def test_upload_keeps_segments_and_climbs_and_the_table(monkeypatch):
    with Env(monkeypatch) as e:
        t = _tpl(e)
        r = e.c.post(f"{UAPI}/{t['id']}/gpx", files={"file": ("slope.gpx", gpx_bytes(), "application/gpx+xml")})
        assert r.status_code == 200, r.text
        row = run(e.db.get(WorkoutTemplateUser, t["id"]))
        import json
        stored = json.loads(row.gpx_course_json)
        assert stored["rule"] == UT.climb_rule() and len(stored["segments"]) >= 2 and len(stored["climbs"]) == 1
        assert "lat" not in json.dumps(stored)                    # no coordinates in the row
        tb = e.c.get(f"{UAPI}/{t['id']}/gpx/segments")
        assert tb.status_code == 200, tb.text
        b = tb.json()
        segs, climbs = b["segments"], b["climbs"]
        up = next(s for s in segs if s["grade"] > 0.1)
        down = next(s for s in segs if s["grade"] < -0.1)
        assert up["gain_m"] > 100 and down["loss_m"] > 100 and up["climb_no"] == 1
        assert sum(s["dist_m"] for s in segs) == pytest.approx(3000, rel=0.05)
        assert {lv["key"] for lv in b["levels"]} == {"easy", "main"}
        for lv in b["levels"]:
            k = lv["key"]
            assert lv["flat_pace_s_per_km"] > 0 and lv["label"]
            # the pace on the climb is slower than the flat-equivalent (GAP) pace, by Minetti
            e_up = up["est"][k]
            assert e_up["pace_s_per_km"] == pytest.approx(lv["flat_pace_s_per_km"] * grade_factor(up["grade"]), rel=0.01)
            assert e_up["t_s"] == pytest.approx(up["dist_m"] / 1000 * e_up["pace_s_per_km"], rel=0.01)
            assert e_up["gap_s_per_km"] == pytest.approx(lv["flat_pace_s_per_km"], rel=0.001)
        # the main set (90–95 % CP) is faster than the easy run
        assert up["est"]["main"]["pace_s_per_km"] < up["est"]["easy"]["pace_s_per_km"]
        # power: a target on runnable ground only (the race calculator's rule); a > 8 % climb: reference
        assert up["kind"] == "steep_climb" and up["est"]["main"]["power"] is None and up["est"]["main"]["power_ref"]
        c = climbs[0]
        assert c["gain_m"] > 100 and c["est"]["main"]["t_s"] > 0 and c["label"]
        assert b["rule"]["min_grade"] == 0.05 and b["note"]
        # no GPX: 404
        e.c.delete(f"{UAPI}/{t['id']}/gpx")
        assert e.c.get(f"{UAPI}/{t['id']}/gpx/segments").status_code == 404


def test_a_template_stored_before_gets_its_table_on_read(monkeypatch):
    with Env(monkeypatch) as e:
        t = _tpl(e)
        e.c.post(f"{UAPI}/{t['id']}/gpx", files={"file": ("slope.gpx", gpx_bytes(), "application/gpx+xml")})
        row = run(e.db.get(WorkoutTemplateUser, t["id"]))
        row.gpx_course_json = None
        run(e.db.commit())
        b = e.c.get(f"{UAPI}/{t['id']}/gpx/segments").json()
        assert len(b["climbs"]) == 1
        run(e.db.refresh(row))
        assert row.gpx_course_json                               # stored for the next read
        # other thresholds than the stored ones: recomputed too
        monkeypatch.setattr(UT, "CLIMB_MIN_GAIN_M", 1000.0)
        assert e.c.get(f"{UAPI}/{t['id']}/gpx/segments").json()["climbs"] == []


def test_the_chart_of_a_hill_template_on_the_route(monkeypatch):
    with Env(monkeypatch) as e:
        t = _tpl(e)
        e.c.post(f"{UAPI}/{t['id']}/gpx", files={"file": ("slope.gpx", gpx_bytes(), "application/gpx+xml")})
        chk = e.c.post(f"{API}/steps/check", json={"kind": "hike", "steps": {"items": t["steps"]["items"], "tpl": t["id"]}}).json()
        el = chk["elev"]
        n = len(chk["order"])
        assert len(el["x"]) == len(el["xt"]) == n + 1 and len(el["s"]) == len(el["up"]) == len(el["down"]) == n
        works = [i for i, o in enumerate(chk["order"]) if o["kind"] == "work"]
        rests = [i for i, o in enumerate(chk["order"]) if o["kind"] == "rest"]
        assert len(works) == 4 and all(el["up"][i] > 20 for i in works) and all(el["down"][i] > 20 for i in rests)
        # Q1-3 (b): each rep climbs again from where the recovery before it stopped
        for w_, r_ in zip(works[1:], rests[:-1]):
            assert el["r"][w_][0] == pytest.approx(el["r"][r_][1], abs=0.002)
        assert all(el["r"][r_][1] >= el["r"][works[0]][0] - 0.002 for r_ in rests)    # never below the bottom
        assert math.isclose(el["xt"][-1], sum(el["s"]), abs_tol=1)
