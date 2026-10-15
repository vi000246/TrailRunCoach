"""
SP-523 (docs/research/coach-consensus-sp518/C-long-specific-b2b.md §3.8): the 專項期 long day.

1. A short road race (≤ 21.1 km, 主要訓練項目 路跑): the long run is a share of the race distance at
   long-run pace, so a 10K's 賽前第 4 週 gave ~8 km (45 min) — shorter than the base phase's long run.
   It now never drops below the base phase's long run (the same week, the same history).
2. 越野 FRAC: the peaks lowered to 75 / 80 % (推估, the rise / step-back shape kept).
3. 越野 race simulation: 賽前第 6、4 週 (never the last 3 weeks); 路跑 keeps 4、3.
4. Stored sessions: an edited / custom session (and an accepted simulation) is left alone.
Synthetic plans and histories; nothing reads the athlete's data.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as P
from backend.engine import specific_phase as SP
from backend.engine import suggestions as SG
from backend.engine.planning import Event, Plan, Threshold, Weight
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases
from backend.tests.test_quality_gate import TODAY        # Wed 9/30: the week of 9/28

RACE_W4 = "2026-10-24"            # Sat: the week of 9/28 is 賽前第 4 週
RACE_BASE = "2027-03-06"          # far away: the week of 9/28 is a base week


def _plan(km, hours, start, kind="road"):
    plan = Plan()
    plan.thresholds.append(Threshold("2026-08-01", lthr=165, cp=250.0))
    plan.events.append(Event(id="e1", name="短距離路跑", date=start, kind=kind, priority="A", days=1,
                             distance_km=km, est_hours=hours))
    plan.weights.append(Weight("2026-08-01", 62.0))
    return plan


def _wp(km, hours, start, sport="road"):
    ds = _history(TODAY)                                  # Sat 150-min long runs
    plan = _plan(km, hours, start)
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return plan, O.week_plan(ds, st, TODAY, sport=sport)


def _long(ss):
    return next(s for s in ss if s["id"] == "long")


# ---- 1. short road races ------------------------------------------------------------------------------

@pytest.mark.parametrize("km,hours", [(10.0, 50 / 60), (21.0975, 1.75)])
def test_short_road_race_long_run_not_below_the_base_phase(km, hours):
    """Reproduction: a 10K at 賽前第 4 週 was 45 min / 7.9 km (half marathon 95 min) while the same
    week as a base week has a 105-min long run."""
    _, base = _wp(km, hours, RACE_BASE)
    plan, wp = _wp(km, hours, RACE_W4)
    assert base["phase"] == "base" and wp["phase"] == "specific" and wp["specific"]["weeks_out"] == 4
    assert wp["target"]["hours"] == pytest.approx(base["target"]["hours"])     # same week volume
    b, s = _long(base["sessions"]), _long(wp["sessions"])
    assert s["minutes"] >= b["minutes"]
    assert s["distance_km"] > 0.8 * km                                         # more than the old 80 % share
    assert "賽事距離的" in s["detail"] and "基礎期" in s["detail"]
    # the projected 專項期 build weeks keep it too (a recovery week stays shorter on purpose)
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 25))
    spec = [w for w in weeks if w["phase"] == "specific" and w["mode"] != "recovery_week"]
    for w in spec:
        assert _long(w["sessions"])["minutes"] >= 60


def test_long_minutes_floor_only_for_short_road_races():
    p10 = {"day": {"hours": 50 / 60, "km": 10.0}}
    info = {"active": True, "frac": SP.ROAD_FRAC[4], "race": p10, "sport": "road"}
    old = 0.80 * 10.0 * 5.0 * SP.ROAD_EASY_SLOW                                # 46 min = 8 km
    assert SP.long_minutes(info, 150) == pytest.approx(old)                   # no week given: as before
    base = SP.base_long_minutes(400, 150)
    assert base == pytest.approx(min(0.30 * 400, 150 * 1.10))
    assert SP.long_minutes(info, 150, week_min=400) == pytest.approx(base)
    # ≤ the +10 % step all the same (SP-66): a short history caps it
    assert SP.long_minutes(info, 40, week_min=400) == pytest.approx(60 * SP.STEP)
    # the marathon: its share is already longer — unchanged
    mar = {**info, "race": {"day": {"hours": 3.5, "km": 42.195}}}
    assert SP.long_minutes(mar, 1e9, week_min=400) == SP.long_minutes(mar, 1e9)
    # a 30K (> 21.1 km): no floor even when its share is short
    k30 = {**info, "frac": SP.ROAD_FRAC[10], "race": {"day": {"hours": 2.0, "km": 30.0}}}
    assert SP.long_minutes(k30, 150, week_min=900) == SP.long_minutes(k30, 150)
    # a short 越野賽 (≤ 3 h) is left as it was (owner 2026-10-14)
    tr = {"active": True, "frac": SP.FRAC[4], "race": {"day": {"hours": 1.0, "km": 10.0}}, "sport": "trail"}
    assert SP.long_minutes(tr, 150, week_min=400) == SP.long_minutes(tr, 150) == pytest.approx(60 * 0.80)


# ---- 2. 越野 FRAC --------------------------------------------------------------------------------------

def test_trail_frac_peaks_lowered():
    assert SP.FRAC == {10: 0.50, 9: 0.55, 8: 0.60, 7: 0.65, 6: 0.75, 5: 0.65, 4: 0.80, 3: 0.65}
    assert max(SP.FRAC.values()) == SP.FRAC[4] == 0.80 and SP.FRAC[6] == 0.75
    assert SP.FRAC[5] < SP.FRAC[6] and SP.FRAC[3] < SP.FRAC[4]                  # the step-back shape kept
    race = {"day": {"hours": 5.0, "km": 30.0}}
    ctx = lambda w: {"active": True, "frac": SP.frac(w), "race": race, "sport": "trail"}
    assert SP.long_minutes(ctx(4), 1e9) == pytest.approx(0.80 * 300)            # was 0.90 × 300
    assert SP.long_minutes(ctx(6), 1e9) == pytest.approx(0.75 * 300)            # was 0.85 × 300
    assert "推估" in SP.SRC


@pytest.mark.parametrize("hours", [9.5, 12.0, 24.0])
def test_long_races_keep_the_6h_cap_every_week(hours):
    """Races whose every week's new share is past TRAIL_LONG_MAX_MIN (≥ 360 / 0.65 min ≈ 9.2 h) are
    unchanged: the long day is the 6-h cap, as before."""
    race = {"day": {"hours": hours, "km": 60.0}}
    for w in range(SP.WEEKS[0], 8):
        info = {"active": True, "frac": SP.frac(w), "race": race, "sport": "trail"}
        assert SP.long_minutes(info, 1e9) == SP.TRAIL_LONG_MAX_MIN


# ---- 3. the race simulation ---------------------------------------------------------------------------

def test_trail_sim_weeks_6_and_4_never_the_last_3():
    start = date(2026, 11, 7)                                    # Sat
    weeks = SP.sim_weeks(start)
    assert [SP.weeks_out(start, m) for m in weeks] == [6, 4]
    assert weeks == [date(2026, 9, 28), date(2026, 10, 12)]
    assert all((start - m).days > 21 for m in weeks)              # not in the last 3 weeks
    # 路跑: unchanged (4、3)
    road = SP.sim_weeks(start, "road")
    assert [SP.weeks_out(start, m) for m in road] == [4, 3] and road == [date(2026, 10, 12), date(2026, 10, 19)]


def _trail_race():
    return {"id": "e1", "name": "合歡山越野", "start": "2026-11-07", "days": 1, "kind": "race", "hours": 5.0,
            "km": 20.0, "goal": 31.0, "day": {"hours": 5.0, "km": 20.0, "climb_m": 1500.0, "descent_m": 1500.0}}


def test_trail_sim_suggestion_window_and_text():
    race = _trail_race()
    ctx = lambda m: SP.week_context(kind="specific", mode="specific", monday=m, race=race)
    assert ctx(date(2026, 9, 28))["sim_week"] and ctx(date(2026, 10, 12))["sim_week"]
    assert not ctx(date(2026, 10, 19))["sim_week"] and not ctx(date(2026, 10, 5))["sim_week"]
    assert SP.sim_suggestion(ctx(date(2026, 9, 14)), date(2026, 9, 14), 200) is None     # 賽前第 8 週: too early
    for m in (date(2026, 9, 21), date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12)):
        sg = SP.sim_suggestion(ctx(m), m, 200)
        assert sg and sg["weeks"] == ["2026-09-28", "2026-10-12"]
        assert "賽前第 6、4 週" in sg["reason"]
    assert SP.sim_suggestion(ctx(date(2026, 10, 19)), date(2026, 10, 19), 200) is None   # 賽前第 3 週: no more
    # the day options are in weeks 6 and 4 only (week 5 is the recovery week)
    sg = SP.sim_suggestion(ctx(date(2026, 9, 28)), date(2026, 9, 28), 200)
    days = {o["day"] for o in SP.sim_day_options(sg, date(2026, 9, 28))}
    assert days and all(d[:10] < "2026-10-05" or "2026-10-12" <= d <= "2026-10-18" for d in days)


def test_road_sim_suggestion_stays_4_to_3():
    race = {**_trail_race(), "kind": "road", "name": "台北馬", "hours": 3.5, "km": 42.195,
            "day": {"hours": 3.5, "km": 42.195}}
    ctx = lambda m: SP.week_context(kind="specific", mode="specific", monday=m, race=race, sport="road")
    assert ctx(date(2026, 10, 12))["sim_week"] and ctx(date(2026, 10, 19))["sim_week"]
    assert not ctx(date(2026, 9, 28))["sim_week"]
    sg = SP.sim_suggestion(ctx(date(2026, 10, 12)), date(2026, 10, 12), 170)
    assert sg["weeks"] == ["2026-10-12", "2026-10-19"] and "賽前第 4、3 週" in sg["reason"]
    assert SP.sim_suggestion(ctx(date(2026, 9, 28)), date(2026, 9, 28), 170) is None


def test_sim_reason_in_english():
    from backend.i18n import use_locale
    race = _trail_race()
    with use_locale("en"):
        sg = SP.sim_suggestion(SP.week_context(kind="specific", mode="specific", monday=date(2026, 9, 28),
                                               race=race), date(2026, 9, 28), 200)
    assert "6 and 4 weeks before the race" in sg["reason"]


def test_a_simulation_already_planned_up_to_the_race_hides_the_suggestion():
    """A simulation accepted under the old window (賽前第 3 週) still counts: no second suggestion."""
    race = _trail_race()
    m = date(2026, 9, 28)
    sg = SP.sim_suggestion(SP.week_context(kind="specific", mode="specific", monday=m, race=race), m, 200)
    opts = lambda s: SP.sim_day_options(s, m)
    assert SG.race_sim_rows({"cur": {"race_sim_suggestion": sg}}, opts, [])
    old = [{"title": "賽事模擬｜合歡山越野", "state": "active", "day": "2026-10-24"}]      # 賽前第 3 週 (old window)
    assert SG.race_sim_rows({"cur": {"race_sim_suggestion": sg}}, opts, old) == []
    after = [{"title": "賽事模擬｜合歡山越野", "state": "active", "day": "2026-11-08"}]    # after the race: not this one
    assert SG.race_sim_rows({"cur": {"race_sim_suggestion": sg}}, opts, after)


# ---- 4. stored sessions -------------------------------------------------------------------------------

def test_reconcile_keeps_edited_custom_and_sim_sessions():
    """The regenerated week (new long-day length) replaces only unedited auto sessions; an edited long
    day, a custom session and an accepted race simulation stay as they are (reconcile rule 3)."""
    from backend.engine import reconcile as R
    wk = "2026-10-12"

    def row(uid, gen_key, day, kind, title, minutes, origin="auto", edited=False, source=""):
        return {"uid": uid, "week_start": wk, "gen_key": gen_key, "day": day, "kind": kind, "title": title,
                "minutes": minutes, "target": "", "detail": "", "source": source, "tss": 30.0, "origin": origin,
                "edited": edited, "provisional": False, "state": "active", "done_by": None, "note": None}
    stored = [row("a1", "long", "2026-10-17", "long", "LSD", 45),
              row("e1", "easy1", "2026-10-14", "easy", "輕鬆跑（我改的）", 33, edited=True),
              row("c1", None, "2026-10-18", "long", "賽事模擬｜合歡山越野", 220, origin="custom", source=SP.SRC_SIM)]
    gen = [{"start": wk, "provisional": False, "mode": "specific", "sessions": [
        {"id": "long", "kind": "long", "day": "2026-10-17", "title": "LSD", "minutes": 105, "tss": 80.0,
         "target": "", "detail": "", "source": ""},
        {"id": "easy1", "kind": "easy", "day": "2026-10-14", "title": "輕鬆跑", "minutes": 50, "tss": 30.0,
         "target": "", "detail": "", "source": ""}]}]
    new, _changes = R.reconcile(stored, gen, [], "2026-10-13")
    by = {s["uid"]: s for s in new}
    assert by["a1"]["minutes"] == 105                                           # unedited auto: regenerated
    assert by["e1"]["minutes"] == 33 and by["e1"]["title"] == "輕鬆跑（我改的）" and by["e1"]["state"] == "active"
    assert by["c1"]["minutes"] == 220 and by["c1"]["state"] == "active" and by["c1"]["day"] == "2026-10-18"
