"""
SP-721: the race calculator's stop time by activity kind and moving time (nonmoving.py).

1. per kind (race / solo / hike) from the athlete's own activities of that kind, shrunk to
   the kind's prior when there are few;
2. the stop share grows with the moving time and is capped (no extrapolation past T_CAP,
   never above SHARE_MAX, never falling with the duration);
3. the calculator's total: one number with the p25–p75 range (summary.time_total_range_s);
4. leave-one-out on a synthetic population whose stops grow with the duration: the outings
   ≥ 2 h moving within ±3 % on the total time and the median |error| no worse than the
   pre-SP-721 fixed race rate (the real athlete's numbers: the SP-721 report);
5. Cairn's rest_hint carries the new prediction and its kind.

Synthetic data only (no WKO5 folder, no ~/.wko5coach).
"""
from __future__ import annotations

import random
from statistics import median
from types import SimpleNamespace

import pytest

from backend.engine.racepower import nonmoving as NM

P = NM.NONMOVING


def row(moving_h: float, share: float, kind: str = "race", long_frac: float = 0.5, stop_len: float = 120.0) -> dict:
    """A run_row-shaped row: moving_h hours moving, share × moving stopped."""
    mv = moving_h * 3600.0
    st = share * mv
    lg = st * long_frac
    n = max(1, int(lg // stop_len)) if lg > 0 else 0
    return {"elapsed_s": mv + st, "moving_s": mv, "stopped_s": st, "long_s": lg, "n_long": n,
            "long_lengths": [lg / n] * n if n else [], "short_s": st - lg, "kind": kind}


# ---- 1. per kind, shrunk to the prior --------------------------------------------------

def test_each_kind_from_its_own_rows_and_an_empty_kind_is_its_prior():
    rows = [row(1.5, 0.10, "race") for _ in range(30)] + [row(1.5, 0.30, "solo") for _ in range(30)]
    prof = NM.profile(rows)
    k = prof["kinds"]
    assert k["race"]["n"] == 30 and k["solo"]["n"] == 30 and k["hike"]["n"] == 0
    s_race = NM.predict(prof, 1.5 * 3600, kind="race")["total_s"]
    s_solo = NM.predict(prof, 1.5 * 3600, kind="solo")["total_s"]
    assert s_solo > 2 * s_race                       # their own shares, not one pooled rate
    # no hikes: the hike prior (share at 1 h, flat prior slope from 1 h)
    hk = NM.predict(prof, 1.0 * 3600, kind="hike")
    assert hk["share"] == pytest.approx(P["prior"]["hike"]) and hk["n_kind"] == 0
    assert "先驗" in hk["why"]
    # an unknown kind falls back to race
    assert NM.predict(prof, 1.5 * 3600, kind="nope")["kind"] == "race"


def test_few_rows_shrink_toward_the_prior_many_rows_follow_the_data():
    few = NM.profile([row(1.0, 0.05, "solo") for _ in range(3)])["kinds"]["solo"]
    many = NM.profile([row(1.0, 0.05, "solo") for _ in range(200)])["kinds"]["solo"]
    prior = P["prior"]["solo"]
    assert 0.05 < few["c"] < prior and few["c"] == pytest.approx((3 * 0.05 + P["k_level"] * prior) / (3 + P["k_level"]))
    assert many["c"] == pytest.approx(0.05, abs=0.01)
    w = NM.predict(NM.profile([row(1.0, 0.05, "solo") for _ in range(3)]), 3600.0, kind="solo")
    assert "收縮" in w["why"]


# ---- 2. grows with the moving time, capped ---------------------------------------------

def _growing(kind="solo", n=60, seed=1):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        T = rnd.uniform(0.5, 4.0)
        out.append(row(T, 0.3 * min(max(T, 1.0), 2.5) ** 0.8, kind))
    return out


def test_share_grows_with_the_moving_time_and_stops_at_the_cap():
    kp = NM.profile(_growing())["kinds"]["solo"]
    assert 0.5 < kp["b"] <= P["b_max"]
    sh = [NM.share_at(kp, h * 3600) for h in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0, 10.0, 24.0)]
    assert all(b >= a for a, b in zip(sh, sh[1:]))          # never falls
    assert sh[0] == sh[1]                                     # flat below T_LO
    assert sh[3] > sh[1]                                      # grows between
    assert sh[4] == sh[5] == sh[6] == sh[7] == sh[8]          # flat past T_CAP (2.5 h): no extrapolation
    p10 = NM.predict(NM.profile(_growing()), 10 * 3600, kind="solo")
    assert p10["capped"] and "不再增加" in p10["why"]
    assert p10["total_s"] == pytest.approx(sh[7] * 10 * 3600)


def test_a_falling_share_is_flat_and_the_share_is_never_above_share_max():
    falling = [row(T, 0.6 / T, "race") for T in (1.0, 1.5, 2.0, 2.5, 3.0) for _ in range(6)]
    kp = NM.profile(falling)["kinds"]["race"]
    assert kp["b"] == 0.0                                     # clipped at 0: never falling with the time
    huge = [row(T, 3.0 * T, "hike") for T in (1.0, 2.0, 3.0) for _ in range(20)]
    hp = NM.profile(huge)["kinds"]["hike"]
    assert NM.share_at(hp, 20 * 3600) == P["share_max"]


# ---- 3. one total with its p25–p75 range ---------------------------------------------

def test_range_brackets_the_total_and_reaches_the_plan_summary():
    rnd = random.Random(3)
    rows = [row(T, 0.3 * min(max(T, 1), 2.5) ** 0.5 * rnd.lognormvariate(0, 0.6), "solo")
            for T in [rnd.uniform(0.5, 4) for _ in range(80)]]
    prof = NM.profile(rows)
    for kind in ("race", "solo", "hike"):
        for h in (0.7, 2.0, 6.0):
            nm = NM.predict(prof, h * 3600, kind=kind)
            assert nm["p25_s"] <= nm["total_s"] <= nm["p75_s"]
    nm = NM.predict(prof, 3 * 3600, kind="solo")
    assert nm["p75_s"] - nm["p25_s"] > 0.2 * nm["total_s"]     # the noise shows as a range
    assert prof["kinds"]["solo"]["range_from"] == "kind" and prof["kinds"]["race"]["range_from"] == "pooled"
    # the planner's summary: one number + the range (planner.plan_run reads it from trail_hr_estimate)
    import numpy as np
    from backend.engine.racepower import planner as PL
    from backend.engine.racepower import trailhr as TH
    pts = [{"x": x, "v": 2 + 4 * x, "T_h": 2.0, "eff_km": 10.0} for x in np.linspace(0.8, 1.2, 8)]
    m = TH.fit(pts, 0.05)
    m["xstar"] = TH.fit_xstar([])
    m["nonmoving"] = prof
    e_race = PL.trail_hr_estimate(m, 30.0, 1500.0, 1.0, stop_kind="race")
    e_solo = PL.trail_hr_estimate(m, 30.0, 1500.0, 1.0, stop_kind="solo")
    assert e_race["nonmoving"]["kind"] == "race" and e_solo["nonmoving"]["kind"] == "solo"
    assert e_solo["time_total_s"] == pytest.approx(e_solo["time_s"] + e_solo["nonmoving"]["total_s"])


def test_a_profile_stored_before_sp721_is_still_predicted():
    old = NM.legacy_profile([row(2.0, 0.2), row(3.0, 0.3)])
    assert "kinds" not in old
    nm = NM.predict(old, 4 * 3600.0, kind="solo")
    assert nm["method"] == "rate" and nm["long_s"] == pytest.approx(old["long_per_h_s"] * 4)
    assert nm["p25_s"] <= nm["total_s"] <= nm["p75_s"]


# ---- the kind of a plan / an activity ------------------------------------------------

def test_plan_kind_rules():
    assert NM.plan_kind("trail", "race") == "race"           # 越野賽
    assert NM.plan_kind("trail", "other") == "solo"          # 其他
    assert NM.plan_kind("trail", None) == "solo"             # no event
    # SP-732: 百岳 is split by the trip kind (跟團 → hike_group), as SP-721 left the interface for
    assert NM.plan_kind("baiyue", "baiyue", "group") == "hike_group"
    assert NM.plan_kind("baiyue", "baiyue") == "hike"


def test_calc_stop_kind_reads_the_event_kind():
    from backend.engine.racepower import calc as CALC
    evs = {"r1": SimpleNamespace(kind="race"), "o1": SimpleNamespace(kind="other")}

    class Ctx:
        def event(self, eid):
            if eid not in evs:
                raise KeyError(eid)
            return evs[eid]

    def body(**kw):
        return CALC.PlanIn(type="trail", distance_km=20.0, **kw)
    assert CALC.stop_kind(Ctx(), body(event_id="r1")) == "race"
    assert CALC.stop_kind(Ctx(), body(event_id="o1")) == "solo"
    assert CALC.stop_kind(Ctx(), body(event_id="gone")) == "solo"
    assert CALC.stop_kind(Ctx(), body()) == "solo"
    assert CALC.stop_kind(Ctx(), body(course=CALC.CourseRef(event_id="r1"))) == "race"
    assert CALC.stop_kind(object(), body(event_id="r1")) == "solo"     # a context without events


def test_nonmoving_kind_and_history(monkeypatch):
    from backend.engine.racepower import athlete as A

    def w(idx, day, sport="run", trail=True, hike=False):
        return SimpleNamespace(idx=idx, day=day, sport=sport, sport_type="trail running" if sport == "run" else "hiking",
                               tags=[], trail=trail, hike=hike)
    monkeypatch.setattr(A, "is_trail", lambda x: x.trail)
    monkeypatch.setattr(A, "is_hike", lambda x: x.hike)
    ws = [w(0, 10), w(1, 11), w(2, 12, sport="hiking", trail=False, hike=True), w(3, 13, trail=False),
          w(4, 30), w(5, 14)]
    assert [A.nonmoving_kind(x, {1}) for x in ws[:4]] == ["solo", "race", "hike", None]
    monkeypatch.setattr(A, "nonmoving_row", lambda ds, x: row(1.0 if x.idx != 5 else 0.2, 0.3))
    monkeypatch.setattr(A, "nonmoving_day_rows", lambda ds, x: [row(3.0, 0.5), row(4.0, 0.6)])
    ds = SimpleNamespace(workouts=ws, flush_series=lambda: None)
    rows = A.nonmoving_history(ds, 20, {1}, exclude={0})
    # day < 20 only, idx 0 excluded, the road run (3) has no kind, idx 5 is under 20 min moving,
    # the hike gives one row per day
    assert sorted(r["kind"] for r in rows) == ["hike", "hike", "race"]


# ---- 4. leave-one-out on a synthetic population ------------------------------------------

def _population(seed=7):
    """Stops that grow with the duration (as SP-716 measured: ~0.3 under 1 h, ~0.5 at 2–3 h)
    and differ by kind, with log-normal noise."""
    rnd = random.Random(seed)
    base = {"race": 0.25, "solo": 0.30, "hike": 0.55}
    slope = {"race": 0.2, "solo": 0.8, "hike": 0.1}
    n = {"race": 15, "solo": 100, "hike": 45}
    out = []
    for k in base:
        for _ in range(n[k]):
            T = rnd.choice([rnd.uniform(0.4, 2.0), rnd.uniform(2.0, 5.0)]) if k != "race" else rnd.uniform(1.6, 3.5)
            share = base[k] * min(max(T, 1.0), 3.0) ** slope[k] * rnd.lognormvariate(0, 0.5)
            out.append(row(T, share, k))
    return out


def _err(r, s):
    return (r["moving_s"] + s) / r["elapsed_s"] - 1


def test_leave_one_out_long_outings_within_3_percent_and_no_worse_overall():
    rows = _population()
    new, old = [], []
    for i, r in enumerate(rows):
        train = rows[:i] + rows[i + 1:]
        new.append((_err(r, NM.predict(NM.profile(train), r["moving_s"], kind=r["kind"])["total_s"]), r))
        races = [x for x in train if x["kind"] == "race" and x["moving_s"] >= 5400]
        old.append((_err(r, NM.predict(NM.legacy_profile(races), r["moving_s"])["total_s"]), r))

    def bias(e, sel):
        return median([x for x, r in e if sel(r)])

    def mae(e):
        return median([abs(x) for x, _r in e])
    long_ = lambda r: r["moving_s"] >= 2 * 3600  # noqa: E731
    assert abs(bias(new, long_)) <= 0.03
    assert abs(bias(old, long_)) > 0.03                      # the fixed race rate misses them
    assert mae(new) <= mae(old)


# ---- 5. Cairn's rest_hint -------------------------------------------------------------

def test_cairn_rest_hint_uses_the_new_prediction_and_its_kind():
    from backend.engine.racepower import cairn_curve as CC
    prof = NM.profile(_population())
    nm = NM.predict(prof, 5 * 3600.0, kind="solo")
    rh = CC.rest_hint({"summary": {"nonmoving": nm}})
    assert rh["basis"] == "nonmoving:rate" and rh["kind"] == "solo"
    assert rh["days"] == [{"day": 1, "rest_s": round(nm["total_s"]), "range_s": [round(nm["p25_s"]), round(nm["p75_s"])]}]
