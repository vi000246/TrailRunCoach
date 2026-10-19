"""
SP-578 (跟團 G2): leg by leg, how hard following the group's timetable is — research SP-575 §3, Q1-5.

  * e = the metabolic rate the group's speed needs ÷ your AeT rate: your AeT capacity time on the leg
    vs the group's moving time (r), the capacity model run the other way (Pandolf forwards, the
    modifiers A, f_time, H, e^δ taken off); downhill a speed ratio (e = r);
  * tiers (推估): < 0.80 wait · 0.80–0.95 easy · 0.95–1.05 right · 1.05–ceiling hard · above the
    ceiling or your time > the group's × 1.1 behind;
  * per day: the group's time above AeT, the legs behind / waiting;
  * a day with no stop in the timetable: × the group moving ratio; the plan's numbers don't change.

Checked against independent arithmetic (Pandolf's closed form), not against the code under test.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pytest import approx

from backend.engine.racepower import capacity as CAP
from backend.engine.racepower import course as CO
from backend.engine.racepower import group as GR
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.tests.test_baiyue_capacity import _synthetic_cap, _v1, pandolf_v_closed_form
from backend.tests.test_racepower_v2 import synthetic_track

ROOT = Path(__file__).resolve().parents[2]
W, L = 68.0, 4.0                       # _synthetic_cap's weight; the default pack of a single-day hike (SP-738: 4 kg)


def course():
    """6 km at 15 % up, then 6 km at 15 % down (1100 m at the top: little altitude)."""
    return CO.build_course(synthetic_track({"len": 12000, "z": lambda x: 200 + (x if x < 6000 else 12000 - x) * 0.15}))


def plan(cap=None, **opts):
    return PL.plan_hike(v1=_v1(), course=course(), hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                        opts={"mode": "auto", **opts}, validated={}, capacity=cap or _synthetic_cap())


def you_between(p, a, b):
    """Your AeT time (s) from km a to b, read off the plan's segments (auto mode, AeT band: λ 1)."""
    t = 0.0
    for s in p["segments"]:
        lo, hi = max(a, s["start_km"]), min(b, s["end_km"])
        if hi > lo:
            t += s["t"] * (hi - lo) / (s["end_km"] - s["start_km"])
    return t


def hhmm(m):
    m = int(round(m))
    return f"{m // 60:02d}:{m % 60:02d}"


def sched(times_min, kms=(0.0, 6.0, 12.0), rests=(0, 0, 0), day=1):
    return {"points": [{"name": f"p{i}", "day": day, "eta": hhmm(t), "rest_min": r, "src": "user", "kind": "pass", "km": k}
                       for i, (t, k, r) in enumerate(zip(times_min, kms, rests))]}


def moving(*mins, start=360):
    """A timetable whose legs move these minutes: no stop written → the clock is moving ÷ 0.8 (the
    default group moving ratio)."""
    t = [start]
    for m in mins:
        t.append(t[-1] + m / 0.8)
    return sched(t)


def e_by_hand(r, cap, grade=0.15):
    """Pandolf at r × v0 (v0: the closed-form speed at Ė_AeT) ÷ Ė_AeT."""
    v0 = pandolf_v_closed_form(cap.e_aet, W, L, 100 * grade)
    v = r * v0
    m = 1.5 * W + 2.0 * (W + L) * (L / W) ** 2 + (W + L) * (1.5 * v * v + 0.35 * v * 100 * grade)
    return m / cap.e_aet


def test_the_tier_cut_points():
    assert [GR.tier(e, 1.0, 1.2) for e in (0.79, 0.80, 0.949, 0.95, 1.049, 1.05, 1.19)] == \
        ["wait", "easy", "easy", "right", "right", "hard", "hard"]
    assert GR.tier(1.21, 1.0, 1.2) == "behind"                      # above the ceiling
    assert GR.tier(0.9, 1.11, 1.2) == "behind"                       # your time > the group's × 1.1
    assert (GR.CUT_WAIT, GR.CUT_EASY, GR.CUT_RIGHT, GR.BEHIND_R) == (0.80, 0.95, 1.05, 1.10)


def test_a_climb_at_your_own_aet_time_is_e_1_and_the_effort_follows_pandolf():
    cap = _synthetic_cap()
    p0 = plan()
    up = you_between(p0, 0, 6)
    for r in (0.7, 1.0, 1.08):
        # the group's moving time = your AeT time ÷ r, so the group goes r × your speed
        p = plan(group_schedule=moving(up / 60 / r, 200))
        lg = p["group"]["legs"][0]
        # the timetable is to the minute: r as written
        r_w = up / lg["moving_s"]
        assert lg["r"] == approx(r_w, rel=1e-9)
        assert lg["e"] == approx(e_by_hand(r_w, cap), rel=2e-3)
        assert lg["up_share"] == approx(1.0)
    assert e_by_hand(1.0, cap) == approx(1.0, rel=1e-6)
    # 1.08 × your speed on the climb, not yet × 1.1: 「吃力」 below your ceiling (your faster quarter on
    # steep windows, exp(p75 − mean)), 「跟不上」 above it
    wide = _synthetic_cap()
    wide.basis["resid_p75_minus_mean"] = 0.15                         # ceiling × 1.16
    hard = plan(cap=wide, group_schedule=moving(up / 60 / 1.08, 200))["group"]["legs"][0]
    assert hard["e"] >= 1.05 and hard["r"] < 1.10 and hard["tier"] == "hard"
    assert hard["e_cap"] == approx(e_by_hand(math.exp(0.15), cap), rel=2e-3)
    narrow = _synthetic_cap()
    narrow.basis["resid_p75_minus_mean"] = 0.02                       # ceiling × 1.02: 1.08 is past it
    over = plan(cap=narrow, group_schedule=moving(up / 60 / 1.08, 200))["group"]["legs"][0]
    assert over["r"] < 1.10 and over["e"] > over["e_cap"] and over["tier"] == "behind"
    assert over["e_cap"] == approx(GR.CUT_RIGHT + 0.01)              # never under the 吃力 cut


def test_downhill_is_a_speed_ratio_and_behind_and_wait_are_marked():
    p0 = plan()
    up, down = you_between(p0, 0, 6), you_between(p0, 6, 12)
    # climb: the group 1.4 × slower than you (wait); descent: the group 1.2 × faster (behind)
    p = plan(group_schedule=moving(up * 1.4 / 60, down / 1.2 / 60))
    a, b = p["group"]["legs"]
    assert a["tier"] == "wait" and a["e"] < 0.80
    assert b["up_share"] == approx(0.0) and b["e"] == approx(b["r"], rel=1e-12) and b["r"] == approx(1.2, rel=0.02)
    assert b["tier"] == "behind" and b["behind_s"] == approx(b["you_s"] - b["moving_s"])
    d = p["group"]["days"][0]
    assert d["behind_n"] == 1 and d["wait_n"] == 1 and d["worst"] == "behind"
    assert not b["above_aet"] and d["above_aet_s"] == 0              # a fast descent costs the legs, not AeT
    assert p["group"]["tiers"] == {"wait": 1, "easy": 0, "right": 0, "hard": 0, "behind": 1}


def test_the_day_adds_up_the_time_above_aet():
    p0 = plan()
    up = you_between(p0, 0, 6)
    p = plan(group_schedule=moving(up / 60 / 1.08, 300))
    d = p["group"]["days"][0]
    leg = p["group"]["legs"][0]
    assert leg["above_aet"] and d["above_aet_s"] == approx(leg["moving_s"])
    assert d["long_hard"] is (leg["moving_s"] > 3 * 3600)
    assert d["group_s"] == approx(sum(x["moving_s"] for x in p["group"]["legs"]))


def test_stops_inside_the_times_take_the_group_moving_ratio_off():
    p0 = plan()
    up = you_between(p0, 0, 6)
    clock = up / 60 / 0.8                                             # 80 % of it moving: the default ratio
    p = plan(group_schedule=sched([360, 360 + clock, 360 + clock + 240]))
    lg = p["group"]["legs"][0]
    assert lg["rest"] == "ratio" and lg["moving_s"] == approx(round(clock) * 60 * 0.8)
    assert lg["r"] == approx(1.0, abs=0.01)
    # past group days give the ratio instead (moving_rows_group, as the clock time's)
    rows = [{"moving_s": 7000, "elapsed_s": 10000}] * 3
    lg2 = plan(group_schedule=sched([360, 360 + clock, 360 + clock + 240]), moving_rows_group=rows)["group"]["legs"][0]
    assert lg2["moving_s"] == approx(round(clock) * 60 * 0.7)
    # a stop written in Cairn: that, not the ratio
    lg3 = plan(group_schedule=sched([360, 360 + up / 60 + 30, 360 + up / 60 + 30 + 240], rests=(0, 30, 0),
                                    kms=(0.0, 6.0, 12.0)))["group"]["legs"]
    assert lg3[0]["rest"] == "cairn" and lg3[1]["moving_s"] == approx(240 * 60 - 30 * 60)


def test_the_plans_own_numbers_do_not_change():
    p0 = plan()
    up = you_between(p0, 0, 6)
    p = plan(group_schedule=moving(up / 60, 200))
    assert p["group"] and p0["group"] is None
    for k in ("time_s", "clock_s", "capacity_time_s", "band", "speed_factor"):
        assert p["summary"][k] == p0["summary"][k]
    assert [s["t"] for s in p["segments"]] == [s["t"] for s in p0["segments"]]
    assert p["days"] == p0["days"] and p["effort"] == p0["effort"]
    # the target-time mode and the capacity band don't move the comparison (your AeT time, λ 1)
    pt = plan(group_schedule=moving(up / 60, 200), mode="time", target_time_s=p0["summary"]["time_s"] * 0.8)
    assert [x["e"] for x in pt["group"]["legs"]] == approx([x["e"] for x in p["group"]["legs"]], rel=1e-12)
    # the capacity band: the same AeT times, but the hours into the day (f_time) run on the faster
    # band's clock — a hair apart
    pc = plan(group_schedule=moving(up / 60, 200), hr_band="cap")
    assert [x["e"] for x in pc["group"]["legs"]] == approx([x["e"] for x in p["group"]["legs"]], rel=1e-3)


def test_without_a_capacity_model_only_the_speed_ratio():
    c = course()
    out = PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                       opts={"mode": "auto", "group_schedule": sched([360, 600, 800])}, validated={})
    g = out["group"]
    assert out["summary"]["total_method"] == "v1" and g["speed_only"] is True
    assert all(x["e"] == approx(x["r"]) for x in g["legs"])


def test_the_api_takes_the_timetable():
    from backend.engine.racepower import calc as CALC
    assert "group_schedule" in CALC.PlanIn.model_fields
    html = (ROOT / "backend/static/racepower.html").read_text("utf-8")
    assert "body.group_schedule = { points: COURSE.group.points }" in html


def test_on_the_flat_the_group_at_your_own_pace_is_1():
    """Where the flat walk caps your speed, e is ÷ that walk's rate: the group at your pace is 「剛好」,
    not 「below AeT, waiting」."""
    flat = CO.build_course(synthetic_track({"len": 8000, "z": lambda x: 300 + x * 0.01}))     # 1 %
    p0 = PL.plan_hike(v1=_v1(), course=flat, hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                      opts={"mode": "auto"}, validated={}, capacity=_synthetic_cap())
    t = sum(s["t"] for s in p0["segments"]) / 60
    p = PL.plan_hike(v1=_v1(), course=flat, hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                     opts={"mode": "auto", "group_schedule": sched([360, 360 + t / 0.8], kms=(0.0, 8.0))},
                     validated={}, capacity=_synthetic_cap())
    lg = p["group"]["legs"][0]
    assert lg["r"] == approx(1.0, abs=0.01) and lg["e"] == approx(1.0, abs=0.01) and lg["tier"] == "right"
