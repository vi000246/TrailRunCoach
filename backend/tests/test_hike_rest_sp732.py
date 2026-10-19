"""
SP-732 — the 百岳 calculator on the stop model (nonmoving.py, SP-721):

1. a 百岳 plan's stops come from the hike kind, split by the user's 跟團 / 自己 mark (sub-kinds
   hike_group / hike_solo shrunk to the hike kind while few days are marked), each day of a trip
   predicted for its own moving time;
2. the clocks read off the plan — segment ETAs, night, cold, 補給 — sit on that moving + stops
   timeline (each day's moving ÷ clock ratio);
3. the total time is one number with a p25–p75 range; Cairn's rest_hint carries each day's prediction;
4. leave-one-out on (synthetic) hike days: bias within ±5 %, median error not worse than the old
   moving ratio.
"""
from __future__ import annotations

import copy
import random
from statistics import median

import pytest
from pytest import approx

from backend.engine.racepower import cold as CD
from backend.engine.racepower import fuel as FU
from backend.engine.racepower import hike as HK
from backend.engine.racepower import night as NI
from backend.engine.racepower import nonmoving as NM
from backend.engine.racepower import planner as PL
from backend.tests.test_baiyue_snow_night_accl import _course, _hike
from backend.tests.test_baiyue_capacity import _synthetic_cap
from backend.tests.test_race_night import SUN

P = NM.NONMOVING


def row(T_h, share, kind="hike", trip=None):
    mv = T_h * 3600.0
    st = share * mv
    r = {"moving_s": mv, "stopped_s": st, "elapsed_s": mv + st, "long_s": 0.7 * st, "short_s": 0.3 * st,
         "n_long": 2, "long_lengths": [0.35 * st, 0.35 * st], "kind": kind}
    if trip:
        r["trip"] = trip
    return r


# ---- 1. the hike kind, split by 跟團 / 自己 -------------------------------------------------

def test_hike_sub_kinds_follow_their_marked_days_and_shrink_to_the_hike_kind():
    rows = [row(T, 1.0, trip="group") for T in (2.0, 3.0, 4.0, 5.0) for _ in range(10)]
    rows += [row(2.0, 0.3, "race") for _ in range(5)]
    prof = NM.profile(rows)
    k = prof["kinds"]
    assert {"hike_group", "hike_solo"} <= set(k)
    # 40 group days at share 1.0 → the group sub-kind is close to 1.0
    assert NM.share_at(k["hike_group"], 3 * 3600) == approx(1.0, abs=0.08)
    # no day marked 自己 → the solo sub-kind IS the hike kind (its c, b and range)
    assert k["hike_solo"]["n"] == 0
    assert k["hike_solo"]["c"] == approx(k["hike"]["c"]) and k["hike_solo"]["b"] == approx(k["hike"]["b"])
    assert k["hike_solo"]["range_from"] == "hike"
    nm = NM.predict(prof, 3 * 3600, kind="hike_solo")
    assert nm["share"] == approx(NM.share_at(k["hike"], 3 * 3600)) and "所有健行日" in nm["why"]
    # a few 自己 days at a lower share pull the solo sub-kind down, but only part of the way
    few = rows + [row(3.0, 0.2, trip="solo") for _ in range(3)]
    k2 = NM.profile(few)["kinds"]
    s_solo, s_hike = NM.share_at(k2["hike_solo"], 3 * 3600), NM.share_at(k2["hike"], 3 * 3600)
    assert 0.2 < s_solo < s_hike


def test_plan_kind_picks_the_trip_kind_and_an_old_profile_falls_back_to_hike():
    assert NM.plan_kind("baiyue", None, "group") == "hike_group"
    assert NM.plan_kind("baiyue", None, "solo") == "hike_solo"
    assert NM.plan_kind("baiyue") == "hike"
    prof = NM.profile([row(3.0, 0.8) for _ in range(6)])
    old = copy.deepcopy(prof)
    for sub in ("hike_group", "hike_solo"):
        old["kinds"].pop(sub)
    nm = NM.predict(old, 3 * 3600, kind="hike_group")
    assert nm["kind"] == "hike" and nm["total_s"] == approx(NM.predict(prof, 3 * 3600, kind="hike")["total_s"])


def _profile():
    """Hike days whose share grows with the moving time (b > 0) — so two days of a trip get
    different ratios."""
    rnd = random.Random(5)
    rows = [row(T, 0.4 * min(max(T, 1.0), 2.5) ** 0.9 * rnd.lognormvariate(0, 0.2), trip="group")
            for T in [rnd.uniform(0.8, 6.0) for _ in range(60)]]
    return NM.profile(rows)


def test_each_day_of_a_trip_gets_its_own_stops_and_the_plan_clock_is_moving_plus_stops():
    prof = _profile()
    rest = PL.hike_rest(prof, "hike_group", {1: 1.0 * 3600, 2: 5.0 * 3600})
    d1, d2 = rest["days"]
    for d in (d1, d2):
        nm = NM.predict(prof, d["moving_s"], kind="hike_group")
        assert d["rest_s"] == approx(nm["total_s"]) and d["p25_s"] <= d["rest_s"] <= d["p75_s"]
        assert d["ratio"] == approx(d["moving_s"] / (d["moving_s"] + d["rest_s"]))
    assert d1["ratio"] > d2["ratio"]                # the longer day stops a bigger share (SP-721 curve)
    assert rest["total_s"] == approx(d1["rest_s"] + d2["rest_s"])
    assert PL.hike_rest(None, "hike", {1: 3600.0}) is None


def _two_day_plan(prof, **opts):
    c = _course()
    return _hike(c, _synthetic_cap(), nonmoving=prof, stop_kind="hike_group", day_splits_km=[8.0], **opts)


def test_baiyue_plan_uses_the_stop_model_per_day():
    prof = _profile()
    p = _two_day_plan(prof)
    sm = p["summary"]
    rest = sm["rest"]
    assert rest and rest["kind"] == "hike_group" and len(rest["days"]) == len(p["days"]) == 2
    T = sm["time_s"]
    assert sm["clock_s"] == approx(T + rest["total_s"])
    assert sm["clock_range_s"][0] <= sm["clock_s"] <= sm["clock_range_s"][1]
    for d, rd in zip(p["days"], rest["days"]):
        assert rd["moving_s"] == approx(d["moving_h"] * 3600)
        assert rd["rest_s"] == approx(NM.predict(prof, rd["moving_s"], kind="hike_group")["total_s"])
        assert d["clock_h"] * 3600 == approx(rd["moving_s"] + rd["rest_s"])
    assert sm["day_ratios"] == [rd["ratio"] for rd in rest["days"]]
    assert any("每天的停留預測" in w for w in p["warnings"])
    # no stop profile: the old moving ratio, no rest
    q = _two_day_plan(None)
    assert q["summary"]["rest"] is None and q["summary"]["clock_s"] == approx(q["summary"]["time_s"] / q["summary"]["moving_ratio"])


def test_v1_baiyue_plan_without_capacity_also_uses_the_stop_model():
    from backend.engine.racepower import grade_model as GM
    from backend.tests.test_baiyue_capacity import _v1
    prof = _profile()
    p = PL.plan_hike(v1=_v1(), course=_course(), hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                     opts={"mode": "auto", "start_time": "06:00", "nonmoving": prof, "stop_kind": "hike_group"},
                     validated={}, capacity=None)
    sm = p["summary"]
    assert sm["rest"] and sm["clock_s"] == approx(sm["time_s"] + sm["rest"]["total_s"])


# ---- 2. every clock on the same timeline -------------------------------------------------

def _clock_s(eta: str, start: str) -> float:
    h, m = (int(x) for x in eta.split(" ")[0].split(":"))
    h0, m0 = (int(x) for x in start.split(":"))
    return ((h * 60 + m) - (h0 * 60 + m0)) * 60.0


def test_segment_etas_cold_points_and_fuel_read_the_day_ratio():
    prof = _profile()
    p = _two_day_plan(prof)
    segs = p["segments"]
    st = p["summary"]["day_starts"]
    for s in segs:
        n = int(s["day"])
        day_t0 = sum(x["t"] for x in segs if int(x["day"]) < n)
        want = (s["cum_s"] - day_t0) / PL.day_ratio(p, n)
        assert _clock_s(s["eta"], st[n - 1] or "15:00") == approx(want, abs=61)
    pts = CD.points(p, "2099-01-10", "15:00", [], 2)
    for s, pt in zip(segs, pts):
        assert (pt["end"] - pt["begin"]).total_seconds() == approx(s["t"] / PL.day_ratio(p, int(s["day"])))
    # day ratio: the plan's per-day value, else the one moving ratio
    assert PL.day_ratio(p, 2) == p["summary"]["day_ratios"][1]
    assert PL.day_ratio({"summary": {"moving_ratio": 0.7}}, 1) == 0.7
    # 補給: the schedule's ETAs within a day use that day's ratio
    f = FU.plan_fuel({**p, "used": {"weight": {"value": 65}}}, weight=65, stops=[], start_time="15:00")
    ev = [e for e in f["schedule"] if e["kind"] == "fuel" and e["day"] == 2]
    assert ev
    t0 = sum(x["t"] for x in segs if int(x["day"]) < 2)
    for e in ev:
        assert _clock_s(e["eta"], st[1] or "15:00") == approx((e["t_s"] - t0) / PL.day_ratio(p, 2), abs=61)


def test_night_slowdown_keeps_each_days_ratio_and_grows_its_stops():
    prof = _profile()
    p = _two_day_plan(prof)
    base = copy.deepcopy(p)
    n = NI.apply(p, date="2099-01-10", start_time="15:00", sun=SUN, slow_pct=10.0)
    sm, rest = n["summary"], n["summary"]["rest"]
    assert n["night"]["added_s"] > 0
    for d, rd, rd0 in zip(n["days"], rest["days"], base["summary"]["rest"]["days"]):
        assert rd["ratio"] == rd0["ratio"]
        assert rd["moving_s"] == approx(d["moving_h"] * 3600)
        assert d["clock_h"] * 3600 == approx(rd["moving_s"] / rd["ratio"])
    assert sm["clock_s"] == approx(sm["time_s"] + rest["total_s"])
    assert sm["clock_s"] > base["summary"]["clock_s"]


# ---- 3. Cairn's rest_hint ------------------------------------------------------------------

def test_cairn_rest_hint_of_a_baiyue_trip_is_each_days_prediction():
    from backend.engine.racepower import cairn_curve as CC
    p = _two_day_plan(_profile())
    rh = CC.rest_hint(p)
    rest = p["summary"]["rest"]
    assert rh["basis"] == "nonmoving:hike" and rh["kind"] == "hike_group"
    assert rh["days"] == [{"day": d["day"], "rest_s": round(d["rest_s"]), "range_s": [round(d["p25_s"]), round(d["p75_s"])]}
                          for d in rest["days"]]


# ---- 4. leave-one-out on hike days ---------------------------------------------------------

def _hike_days(seed=11):
    """Hike days (group mostly; a few own-pace days stop less), the stop share log-normal around a
    level that grows a little with the moving time."""
    rnd = random.Random(seed)
    out = []
    for i in range(60):
        trip = "solo" if i % 5 == 0 else "group"
        T = rnd.uniform(1.0, 7.0)
        base = 0.35 if trip == "solo" else 0.8
        out.append({**row(T, base * min(max(T, 1.0), 2.5) ** 0.2 * rnd.lognormvariate(0, 0.5), trip=trip), "id": i})
    return out


def test_leave_one_out_hike_days_bias_within_5_percent_and_no_worse_than_the_moving_ratio():
    days = _hike_days()

    def err(r, s):
        return (r["moving_s"] + s) / r["elapsed_s"] - 1
    new, old = [], []
    for r in days:
        train = [x for x in days if x["id"] != r["id"]]
        kind = NM.plan_kind("baiyue", None, r["trip"])
        new.append(err(r, PL.hike_rest(NM.profile(train), kind, {1: r["moving_s"]})["total_s"]))
        ratio, _src = HK.moving_ratio(train)
        old.append(err(r, r["moving_s"] / ratio - r["moving_s"]))
    assert abs(median(new)) <= 0.05
    assert median(abs(x) for x in new) <= median(abs(x) for x in old)
