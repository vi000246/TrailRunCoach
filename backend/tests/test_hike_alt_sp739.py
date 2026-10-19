"""
SP-739 — the 登山 (type baiyue) stops per altitude class of the route's highest point:

1. ≥ 3000 m 百岳, 1500–2999 m 中級山, < 1500 m 郊山; a past hike day is classed by its trip's top
   and each class is learnt from its own days (inside 跟團 / 自己, SP-732); the plan names the
   class and the number of trips;
2. a class with too few trips / days (ALT_MIN_TRIPS / ALT_MIN_DAYS, 推估) → ≥ 3000 / < 3000; still
   too few → every hike day (as before), and the plan says so;
3. leave-one-trip-out on synthetic trips: the 百岳 days' bias shrinks, the 郊山 days' not worse.
All synthetic data.
"""
from __future__ import annotations

import random
from statistics import median
from types import SimpleNamespace

from pytest import approx

from backend.engine.racepower import nonmoving as NM
from backend.engine.racepower import planner as PL

_TRIP = [0]


def day(T_h, share, top, trip="group", trip_id=None):
    mv = T_h * 3600.0
    st = share * mv
    if trip_id is None:
        _TRIP[0] += 1
        trip_id = _TRIP[0]
    return {"moving_s": mv, "stopped_s": st, "elapsed_s": mv + st, "long_s": 0.7 * st, "short_s": 0.3 * st,
            "n_long": 2, "long_lengths": [0.35 * st, 0.35 * st], "kind": "hike", "trip": trip,
            "top_m": top, "trip_id": trip_id}


def trips(n, days, T_h, share, top, trip="group"):
    out = []
    for _ in range(n):
        _TRIP[0] += 1
        out += [day(T_h, share, top, trip, trip_id=_TRIP[0]) for _ in range(days)]
    return out


# ---- 1. three classes ----------------------------------------------------------------------

def test_alt_band_boundaries():
    assert [NM.alt_band(z) for z in (0, 1499.9, 1500, 2999, 3000, 3952)] == ["low", "low", "mid", "mid", "high", "high"]
    assert NM.alt_band(None) is None and NM.alt_band(float("nan")) is None and NM.alt_band("x") is None


def test_each_class_learns_from_its_own_trips_and_the_plan_names_it():
    rows = trips(10, 1, 2.0, 0.5, 900) + trips(5, 1, 2.0, 0.8, 2200) + trips(4, 3, 2.0, 1.0, 3500)
    prof = NM.profile(rows)
    k = prof["kinds"]
    assert k["hike_group@high"]["n_trips"] == 4 and k["hike_group@high"]["n"] == 12
    assert k["hike_group@low"]["n_trips"] == 10 and k["hike_group@mid"]["n_trips"] == 5
    hi = NM.predict(prof, 2 * 3600, kind="hike_group", top_m=3886)
    lo = NM.predict(prof, 2 * 3600, kind="hike_group", top_m=750)
    mid = NM.predict(prof, 2 * 3600, kind="hike_group", top_m=2100)
    flat = NM.predict(prof, 2 * 3600, kind="hike_group")
    assert hi["alt"]["used"] == "high" and hi["alt"]["scheme"] == "three"
    assert lo["alt"]["used"] == "low" and mid["alt"]["used"] == "mid"
    assert lo["share"] < mid["share"] < hi["share"]
    assert lo["share"] < flat["share"] < hi["share"]                 # the pooled fit sits between
    assert hi["kind_label"] == "登山（跟團） · 百岳 4 趟"
    assert "路線最高 3,886 m → 百岳（≥ 3000 m），用你之前百岳的 4 趟" in hi["why"] and "4 趟（12 天）" in hi["why"]
    assert lo["kind_label"].endswith("郊山 10 趟")
    # no route top (or no altitude) → the hike kind as a whole, as before SP-739
    assert flat["alt"] is None and flat["kind_label"] == "登山（跟團）"


def test_a_past_trip_is_classed_by_its_whole_top_and_the_marks_stay_apart(monkeypatch):
    from backend.engine.racepower import athlete as A
    w = SimpleNamespace(idx=7, day=5, sport="hiking", sport_type="hiking", tags=[], entry=None)
    monkeypatch.setattr(A, "nonmoving_kind", lambda x, race_idx: "hike")
    monkeypatch.setattr(A, "nonmoving_day_rows", lambda ds, x: [
        {**day(3.0, 0.5, 2400), "top_m": 2400}, {**day(4.0, 0.8, 3492), "top_m": 3492}])
    ds = SimpleNamespace(workouts=[w], flush_series=lambda: None)
    rows = A.nonmoving_history(ds, 20, set(), tags=[])
    # day 1 tops out at 2400 m, but the trip reaches 3492 m: both days are 百岳 days
    assert [r["top_m"] for r in rows] == [3492, 3492] and [r["day_top_m"] for r in rows] == [2400, 3492]
    assert {r["trip_id"] for r in rows} == {7} and {r["trip"] for r in rows} == {"group"}
    # 跟團 / 自己 apart: the solo classes only hold the solo days
    prof = NM.profile(trips(5, 1, 2.0, 0.5, 900, "solo") + trips(5, 2, 2.0, 1.0, 3300, "group"))
    assert prof["kinds"]["hike_solo@low"]["n_trips"] == 5 and prof["kinds"]["hike_solo@high"]["n_trips"] == 0
    assert prof["kinds"]["hike_group@high"]["n_trips"] == 5 and prof["kinds"]["hike_group@low"]["n_trips"] == 0


def test_day_top_is_read_from_the_elevation_of_each_day(monkeypatch):
    import datetime as dt

    import numpy as np

    from backend.engine.racepower import athlete as A
    t = np.arange(0, 2 * 86400, 10.0)
    z = np.where(t < 86400, 1000 + t / 86400 * 1400, 3000 + (t - 86400) / 86400 * 400)
    arr = {"t": t, "kmh": np.full(len(t), 3.0), "z": z}
    monkeypatch.setattr(A, "activity_arrays", lambda ds, w: arr)
    w = SimpleNamespace(entry=SimpleNamespace(start=dt.datetime(2026, 9, 1, 0, 0)))
    rows = A._nonmoving_day_rows(None, w)
    assert len(rows) == 2
    assert rows[0]["top_m"] == approx(2400, abs=2) and rows[1]["top_m"] == approx(3400, abs=2)


# ---- 2. fallbacks --------------------------------------------------------------------------

def test_a_thin_class_falls_back_to_two_classes_then_to_all():
    # 中級山 only 1 trip: < 3000 m (郊山 + 中級山) has enough → the two-class split
    rows = trips(6, 1, 2.0, 0.5, 900) + trips(1, 1, 2.0, 0.8, 2200) + trips(4, 2, 2.0, 1.0, 3500)
    prof = NM.profile(rows)
    mid = NM.predict(prof, 2 * 3600, kind="hike_group", top_m=2200)
    assert mid["alt"]["scheme"] == "two" and mid["alt"]["used"] == "lt3000" and mid["alt"]["n_trips"] == 7
    assert "中級山只有 1 趟" in mid["why"] and "改用 3000 m 以下的 7 趟" in mid["why"]
    assert mid["kind_label"].endswith("3000 m 以下 7 趟")
    # 百岳 only 2 trips: ≥ 3000 is the same class in both splits → straight to every hike day
    rows = trips(6, 1, 2.0, 0.5, 900) + trips(2, 3, 2.0, 1.0, 3500)
    prof = NM.profile(rows)
    hi = NM.predict(prof, 2 * 3600, kind="hike_group", top_m=3500)
    flat = NM.predict(prof, 2 * 3600, kind="hike_group")
    assert hi["alt"]["scheme"] == "all" and hi["alt"]["used"] is None and hi["share"] == approx(flat["share"])
    assert "百岳只有 2 趟" in hi["why"] and "改用所有登山的 8 趟" in hi["why"]
    assert hi["kind_label"].endswith("所有登山 8 趟")
    # 郊山 and < 3000 both thin → every hike day
    rows = trips(1, 1, 2.0, 0.5, 900) + trips(1, 1, 2.0, 0.6, 2000) + trips(5, 2, 2.0, 1.0, 3500)
    lo = NM.predict(NM.profile(rows), 2 * 3600, kind="hike_group", top_m=900)
    assert lo["alt"]["scheme"] == "all" and "3000 m 以下也只有 2 趟" in lo["why"]


def test_the_threshold_counts_trips_and_days():
    assert (NM.ALT_MIN_TRIPS, NM.ALT_MIN_DAYS) == (3, 5)
    # 3 trips of 1 day each: enough trips, too few days
    rows = trips(3, 1, 2.0, 1.0, 3500) + trips(6, 1, 2.0, 0.5, 900)
    assert NM.predict(NM.profile(rows), 7200, kind="hike_group", top_m=3500)["alt"]["scheme"] == "all"
    rows = trips(3, 2, 2.0, 1.0, 3500) + trips(6, 1, 2.0, 0.5, 900)
    assert NM.predict(NM.profile(rows), 7200, kind="hike_group", top_m=3500)["alt"]["scheme"] == "three"


def test_a_sub_kind_without_marked_days_uses_the_hike_classes():
    rows = trips(6, 1, 2.0, 0.5, 900) + trips(4, 2, 2.0, 1.0, 3500)
    prof = NM.profile(rows)
    solo = NM.predict(prof, 2 * 3600, kind="hike_solo", top_m=3500)
    assert solo["alt"]["key"] == "hike@high" and solo["alt"]["scheme"] == "three"
    assert solo["share"] == approx(NM.share_at(prof["kinds"]["hike@high"], 7200))


def test_an_old_profile_without_classes_predicts_as_before():
    rows = trips(6, 1, 2.0, 0.5, 900) + trips(4, 2, 2.0, 1.0, 3500)
    prof = NM.profile(rows)
    old = {**prof, "kinds": {k: v for k, v in prof["kinds"].items() if "@" not in k}}
    a = NM.predict(old, 7200, kind="hike_group", top_m=3500)
    assert a["alt"] is None and a["share"] == approx(NM.predict(prof, 7200, kind="hike_group")["share"])
    # a non-hike kind never gets a class
    assert NM.predict(prof, 7200, kind="race", top_m=3500)["alt"] is None


# ---- the plan --------------------------------------------------------------------------------

def test_route_top_and_the_plan_rest_use_the_class():
    assert PL.route_top_m({"totals": {"z_max": 3605.0}, "segments": []}) == 3605.0
    assert PL.route_top_m({"totals": {}, "segments": [{"z_max": 1200.0}, {"z_max": 1350.0}]}) == 1350.0
    assert PL.route_top_m({"totals": {}, "segments": [{"z_max": None}]}) is None
    rows = trips(6, 1, 2.0, 0.5, 900) + trips(4, 2, 2.0, 1.0, 3500)
    prof = NM.profile(rows)
    rest = PL.hike_rest(prof, "hike_group", {1: 3 * 3600, 2: 5 * 3600}, top_m=3500)
    assert rest["alt"]["used"] == "high" and rest["kind_label"].endswith("百岳 4 趟")
    low = PL.hike_rest(prof, "hike_group", {1: 3 * 3600, 2: 5 * 3600}, top_m=900)
    assert rest["total_s"] > low["total_s"]
    # no route top (a manual route without the altitude): not split, and the page says so
    none = PL.hike_rest(prof, "hike_group", {1: 3 * 3600}, top_m=None)
    assert none["alt"] is None and none["kind_label"] == "登山（跟團） · 不分海拔"
    assert none["why"].startswith("沒有路線最高海拔")


def test_a_baiyue_plan_takes_its_class_from_the_course_top():
    from backend.tests.test_baiyue_capacity import _synthetic_cap
    from backend.tests.test_baiyue_snow_night_accl import _course, _hike
    c = _course()                                   # 2600 → 3400 m
    rows = trips(8, 1, 2.0, 0.4, 900) + trips(4, 2, 3.0, 1.0, 3500)
    prof = NM.profile(rows)
    p = _hike(c, _synthetic_cap(), nonmoving=prof, stop_kind="hike_group", day_splits_km=[8.0])
    rest = p["summary"]["rest"]
    assert rest["alt"]["used"] == "high" and rest["alt"]["top_m"] == approx(c["totals"]["z_max"])
    assert any("百岳" in w and "每天的停留預測" in w for w in p["warnings"])


# ---- 3. leave-one-trip-out -------------------------------------------------------------------

def _population(seed=3):
    """Short 郊山 days stopping ~0.55 of the moving time, 百岳 trips of 2–4 long days stopping
    ~0.9 (under the model's 1.0 cap), log-normal noise."""
    rnd = random.Random(seed)
    out = []
    for _ in range(30):
        out += trips(1, 1, rnd.uniform(0.8, 2.5), 0.55 * rnd.lognormvariate(0, 0.3), rnd.uniform(300, 1400))
    for _ in range(6):
        _TRIP[0] += 1
        tid, top = _TRIP[0], rnd.uniform(3000, 3900)
        out += [day(rnd.uniform(3.0, 7.0), 0.9 * rnd.lognormvariate(0, 0.2), top, trip_id=tid)
                for _ in range(rnd.choice([2, 3, 4]))]
    return out


def test_leave_one_trip_out_baiyue_bias_shrinks_and_jiaoshan_not_worse():
    rows = _population()
    by = {}
    for r in rows:
        by.setdefault(r["trip_id"], []).append(r)
    err = {"high": ([], []), "low": ([], [])}
    for tid, rs in by.items():
        prof = NM.profile([r for r in rows if r["trip_id"] != tid])
        for r in rs:
            before = NM.predict(prof, r["moving_s"], kind="hike_group")
            after = NM.predict(prof, r["moving_s"], kind="hike_group", top_m=r["top_m"])
            band = NM.alt_band(r["top_m"])
            for lst, p in zip(err[band], (before, after)):
                lst.append((r["moving_s"] + p["total_s"]) / r["elapsed_s"] - 1)
    hb, ha = median(err["high"][0]), median(err["high"][1])
    lb, la = median(err["low"][0]), median(err["low"][1])
    assert hb < -0.05 and abs(ha) < abs(hb) / 2              # 百岳 under-predicted pooled, halved by its class
    assert abs(la) <= abs(lb) + 0.01                          # 郊山 bias not worse
    mae = lambda xs: median(abs(x) for x in xs)  # noqa: E731
    assert mae(err["low"][1]) <= mae(err["low"][0]) + 0.01
