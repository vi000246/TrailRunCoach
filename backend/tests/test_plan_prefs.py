"""
課表偏好 (engine/plan_prefs.py): defaults change nothing; hard / soft caps,
run counts, allowed days, long day, quality / strength counts, terrain and
interval target; validation; the API; reconcile keeps edited sessions.
No dataset: the projection's template is the same one week_plan() shapes.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import plan_prefs as PP
from backend.engine import plan_store as PS
from backend.engine import projection as P
from backend.engine import reconcile as R
from backend.settings import repository as SR
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import PHASES, Env, cur_plan, inputs, API

MON = date(2026, 10, 5)
TGT = {"z2": "功率 120–150 W · 心率 130–150 bpm", "long": "心率 < 150 bpm", "threshold": "功率 170–180 W · 心率 155–165 bpm",
       "supra": "功率 180–190 W · 心率 160–170 bpm"}
RATES = {"road": 50.0, "trail": 40.0, "hike": 45.0, "strength": 30.0}


def week(prefs=None, kind="base", mode="base", hours=5.0, allow_quality=True, long_wd=6, notes=None,
         base_quality=None):
    return P.week_sessions(MON, kind, mode, hours, 50.0, TGT, long_wd, 60.0, False, allow_quality, 17.5, 150.0,
                           base_quality, prefs=prefs, rates=RATES, notes=notes)


def main(ss):
    return [s for s in ss if s["kind"] != "strength"]


def mins(ss):
    return sum(s["minutes"] for s in main(ss))


def wd(s):
    return date.fromisoformat(s["day"]).weekday()


# ---------------------------------------------------------------------------
# defaults are the original planner
# ---------------------------------------------------------------------------

def test_default_prefs_are_inactive_and_change_nothing():
    assert not PP.Prefs().active
    assert week(PP.Prefs()) == week(None)
    a = P.project_weeks(cur_plan(), PHASES, date(2027, 3, 1))
    b = P.project_weeks(cur_plan(), PHASES, date(2027, 3, 1), prefs=PP.Prefs())
    assert a == b


def test_settings_round_trip():
    p = PP.Prefs(days=(True, False, True, False, True, False, True), long_day="sun", cap_weekday=50,
                 cap_mode="hard", runs=4, quality=1, strength=2, strength_days=(0, 3), weekly_hours=6.0,
                 terrain_easy="trail", terrain_long="trail", terrain_quality="hill", interval_target="hr")
    s = p.settings()
    for k, v in s.items():
        SR.validate(k, v)                                   # every stored value is valid
    # the old 間歇目標 = 心率 reads back as 目標依據 = 心率 (engine/target_policy.py migration)
    from dataclasses import replace
    assert PP.from_settings(s) == replace(p, target_basis="hr")
    assert PP.Prefs().settings()["plan.prefs.days"] is None  # all days = the default (null)


# ---------------------------------------------------------------------------
# caps
# ---------------------------------------------------------------------------

def test_50_min_cap_keeps_every_session_within_it_and_the_volume():
    notes = []
    ss = week(PP.Prefs(cap_weekday=50), notes=notes)
    assert all(s["minutes"] <= 50 for s in main(ss)), [(s["id"], s["minutes"]) for s in ss]
    assert abs(mins(ss) - 300) <= 10                         # auto count grows instead of dropping volume
    assert len({s["day"] for s in main(ss)}) == len(main(ss))   # one main session per day
    q = next(s for s in ss if s["kind"] == "quality")
    assert q["minutes"] == 50 and "暖身 10 分、緩和 5 分" in q["detail"]
    steps = CW.session_steps(q, CW.Thresholds.of({"cp": 300, "lthr": 170, "aet": 150}))
    assert steps[1].sets == 3                              # still parses as 3×10


def test_hard_cap_drops_the_excess_with_a_note():
    notes = []
    ss = week(PP.Prefs(cap_weekday=50, runs=4, cap_mode="hard"), notes=notes)
    assert len(main(ss)) == 4
    assert all(s["minutes"] <= 50 for s in main(ss))
    assert mins(ss) == 200
    assert any("受限於你的偏好，本週少 1.7 小時" in n["text"] for n in notes), notes


def test_soft_cap_puts_the_excess_on_the_long_day():
    notes = []
    ss = week(PP.Prefs(cap_weekday=50, runs=4, cap_mode="soft"), notes=notes)
    long_s = next(s for s in ss if s["id"] == "long")
    assert all(s["minutes"] <= 50 for s in main(ss) if s["id"] != "long")   # weekdays within the cap
    assert long_s["minutes"] == 150 and abs(mins(ss) - 300) <= 5
    assert wd(long_s) == 6
    assert any("放在長跑日" in n["text"] for n in notes)


def test_hard_cap_uses_the_long_day_cap_first():
    notes = []
    ss = week(PP.Prefs(cap_weekday=50, cap_long=90, runs=4, cap_mode="hard"), notes=notes)
    long_s = next(s for s in ss if s["id"] == "long")
    assert long_s["minutes"] == 90
    assert mins(ss) == 240
    assert any("少 1.0 小時" in n["text"] for n in notes)


def test_soft_cap_without_a_long_session_uses_an_easy_run_on_the_long_day():
    notes = []
    ss = week(PP.Prefs(cap_weekday=30, runs=3), kind="base", mode="recovery_week", hours=3.0, notes=notes)
    assert not [s for s in ss if s["id"] == "long"]          # recovery week: no long session
    big = max(main(ss), key=lambda s: s["minutes"])
    assert big["minutes"] > 30 and wd(big) == 6
    assert sum(1 for s in main(ss) if s["minutes"] > 30) == 1


def test_cp_test_is_exempt_from_the_cap_with_a_note():
    ctx = PP.Ctx(kind="base", mode="base", allow_quality=True, rates=RATES, aet=150)
    test = {"id": "test", "kind": "test", "title": "CP 測試 3 分 + 12 分", "minutes": 60, "target": "", "detail": "",
            "source": "", "tss": 75.0, "day": None, "done": False, "done_by": None}
    out = PP.shape([test], 200, PP.Prefs(cap_weekday=50, cap_mode="hard"), ctx)
    assert next(s for s in out if s["kind"] == "test")["minutes"] == 60
    assert any(n["text"] == PP.NOTE_TEST for n in ctx.notes)


def test_trim_quality_drops_reps_after_warmup_and_cooldown():
    s = {"title": "閾值 3×10 分", "minutes": 60, "detail": "休 2–3 分鐘；暖身 15 分、緩和 10 分", "tss": 70.0}
    assert PP.trim_quality(s, 40)
    assert s["title"] == "閾值 2×10 分" and s["minutes"] <= 40 and s["tss"] < 70
    s = {"title": "閾值 2×10 分", "minutes": 40, "detail": "暖身 10 分、緩和 5 分", "tss": 40.0}
    assert not PP.trim_quality(s, 20)                       # never below 2 reps
    assert s["title"] == "閾值 2×10 分"


# ---------------------------------------------------------------------------
# counts and days
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("runs", [3, 4, 5, 6, 7])
def test_run_count_is_respected(runs):
    ss = week(PP.Prefs(runs=runs))
    assert len(main(ss)) == runs
    assert abs(mins(ss) - 300) <= 10


def test_unchecked_days_are_rest_days():
    days = (True, False, True, False, True, False, True)    # Mon Wed Fri Sun
    notes = []
    ss = week(PP.Prefs(days=days, strength=2), notes=notes)
    assert {wd(s) for s in ss if s["day"]} <= {0, 2, 4, 6}
    assert len(main(ss)) <= 4
    assert wd(next(s for s in ss if s["id"] == "long")) == 6


def test_long_day_saturday_and_quality_spacing():
    ss = week(PP.Prefs(long_day="sat", quality=2))
    long_d = date.fromisoformat(next(s for s in ss if s["id"] == "long")["day"])
    assert long_d.weekday() == 5
    q = [date.fromisoformat(s["day"]) for s in ss if s["kind"] == "quality"]
    assert len(q) == 2 and abs((q[0] - q[1]).days) >= 2
    assert all(abs((d - long_d).days) >= 2 for d in q)


def test_long_day_falls_back_to_the_nearest_allowed_day():
    days = (True, True, True, True, True, True, False)      # no Sunday
    ss = week(PP.Prefs(days=days, long_day="sun"))
    assert wd(next(s for s in ss if s["id"] == "long")) == 5


def test_quality_count_zero_and_the_drift_gate():
    assert not [s for s in week(PP.Prefs(quality=0)) if s["kind"] == "quality"]
    # 2 asked, but the base-phase gate (drift streak) says no: none at all
    assert not [s for s in week(PP.Prefs(quality=2), allow_quality=False) if s["kind"] == "quality"]
    assert len([s for s in week(PP.Prefs(quality=2)) if s["kind"] == "quality"]) == 2


def test_strength_count_and_chosen_days():
    ss = week(PP.Prefs(strength=3, strength_days=(0, 2, 4)))
    st = [s for s in ss if s["kind"] == "strength"]
    assert len(st) == 3 and sorted(wd(s) for s in st) == [0, 2, 4]
    assert not [s for s in week(PP.Prefs(strength=0)) if s["kind"] == "strength"]
    # with easy runs: on easy-run days, never the day before the long session
    ss = week(PP.Prefs(strength=2))
    easy_days = {s["day"] for s in ss if s["kind"] == "easy"}
    long_d = date.fromisoformat(next(s for s in ss if s["id"] == "long")["day"])
    for s in ss:
        if s["kind"] == "strength":
            assert s["day"] in easy_days and date.fromisoformat(s["day"]) != long_d - dt.timedelta(days=1)


def test_weekly_hours_cap_in_the_projection():
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 11, 1), prefs=PP.Prefs(weekly_hours=4.0))
    assert all(w["hours"] <= 4.0 + 1e-9 for w in weeks)
    assert any("每週時數上限 4 h" in " ".join(w["why"]) for w in weeks)


# ---------------------------------------------------------------------------
# terrain and interval target
# ---------------------------------------------------------------------------

def test_trail_easy_is_hr_only_and_uses_the_trail_rate():
    ss = week(PP.Prefs(terrain_easy="trail"))
    e = [s for s in ss if s["kind"] == "easy"]
    assert e and all(s["terrain"] == "trail" and "越野" in s["title"] for s in e)
    assert all(s["target"] == "心率 ≤ AeT 150 bpm" and "功率" not in s["target"] for s in e)
    assert all(s["tss"] == pytest.approx(s["minutes"] / 60 * RATES["trail"]) for s in e)


def test_old_hike_long_terrain_is_a_trail_long_run():
    # 長跑地形「登山」 is gone: a stored "hike" builds the 越野跑 long run, never 登山健行
    trail = week(PP.Prefs(terrain_long="trail"))
    for p in (PP.Prefs(terrain_long="hike"), PP.from_settings({"plan.prefs.terrain_long": "hike"})):
        h = next(s for s in week(p) if s["id"] == "long")
        assert h["kind"] == "long" and h["terrain"] == "trail" and "登山" not in h["title"]
        assert h == next(s for s in trail if s["id"] == "long")
    assert PP.from_settings({"plan.prefs.terrain_long": "hike"}).terrain_long == "trail"


def test_quality_terrain_and_hr_target():
    q = next(s for s in week(PP.Prefs(terrain_quality="hill", interval_target="hr")) if s["kind"] == "quality")
    assert q["title"].endswith("（坡道）") and q["terrain"] == "trail"
    assert q["target"] == "心率 155–165 bpm"
    steps = CW.session_steps(q, CW.Thresholds.of({"cp": 300, "lthr": 170, "aet": 150}))
    assert steps[1].steps[0].intensity == ("hr", 155, 165)  # the work step targets heart rate
    flat = next(s for s in week(PP.Prefs(terrain_quality="flat"), kind="specific", mode="specific")
                if s["kind"] == "quality")
    assert "爬坡" not in flat["title"] and flat["title"].endswith("（平路）")
    assert "Supra" in flat["source"]                         # still the supra-threshold power band
    # carried into the next projected week: not shaped twice
    again = next(s for s in week(PP.Prefs(terrain_quality="hill"), base_quality=q) if s["kind"] == "quality")
    assert again["title"].count("（坡道）") == 1


# ---------------------------------------------------------------------------
# validation + API
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,value", [
    ("plan.prefs.cap_weekday", 5), ("plan.prefs.cap_weekday", "50"), ("plan.prefs.runs_per_week", 8),
    ("plan.prefs.quality_per_week", 3), ("plan.prefs.days", [True] * 6), ("plan.prefs.days", [False] * 7),
    ("plan.prefs.cap_mode", "maybe"), ("plan.prefs.terrain_long", "swim"), ("plan.prefs.strength_days", [7]),
    ("plan.prefs.weekly_hours", 0), ("plan.prefs.interval_target", "pace")])
def test_bad_values_are_rejected(key, value):
    with pytest.raises(ValueError):
        SR.validate(key, value)


def test_dropped_quality_gate_mode_falls_back_to_auto(monkeypatch):
    # 三訊號 ("xu_signals") is no longer a 間歇門檻: a stored value reads as auto…
    assert PP.from_settings({"plan.prefs.quality_gate": "xu_signals"}).quality_gate == "auto"
    assert PP.from_settings({"plan.prefs.quality_gate": "ua_gap"}).quality_gate == "ua_gap"
    from backend.engine.wko5expr import datasource as DSRC
    monkeypatch.setattr(DSRC, "read_setting", lambda k, default=None, user_id=1:
                        "xu_signals" if k == "plan.prefs.quality_gate" else default)
    assert PP.load().quality_gate == "auto"
    # …new writes of it are rejected (settings enum, API body)
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.quality_gate", "xu_signals")
    with pytest.raises(ValueError):
        PP.check(PP.from_body({"quality_gate": "xu_signals"}))
    # and evaluate() maps any unknown mode to auto too
    from backend.engine import quality_gate as QG
    assert "xu_signals" not in QG.MODES and "xu_signals" not in QG.OPTION_INFO


def test_cross_field_checks():
    with pytest.raises(ValueError):
        PP.check(PP.Prefs(days=(True, True, True, False, False, False, False), runs=4))
    with pytest.raises(ValueError):
        PP.check(PP.Prefs(runs=3, quality=3 - 0))
    with pytest.raises(ValueError):
        PP.check(PP.Prefs(cap_weekday=60, cap_long=45))
    PP.check(PP.Prefs(runs=4, quality=2, cap_weekday=50, cap_long=90))


def test_api_prefs_round_trip_and_regeneration(monkeypatch):
    from backend.api import plan_sessions
    with Env(monkeypatch) as e:
        assert e.c.get(f"{API}/prefs").json()["active"] is False
        assert e.c.put(f"{API}/prefs", json={"runs": 9}).status_code == 400
        assert e.c.put(f"{API}/prefs", json={"nope": 1}).status_code == 400
        r = e.c.put(f"{API}/prefs", json={"cap_weekday": 50, "cap_mode": "hard", "runs": 4})
        assert r.status_code == 200 and r.json()["prefs"]["cap_weekday"] == 50
        got = e.c.get(f"{API}/prefs").json()
        assert got["active"] and got["prefs"]["runs"] == 4 and got["prefs"]["cap_mode"] == "hard"
        # back to the defaults
        assert e.c.put(f"{API}/prefs", json={}).json()["active"] is False


def test_reconcile_never_overwrites_edited_sessions_when_prefs_change():
    base = inputs()
    stored, _ = R.reconcile([], PS.gen_weeks(base), base["activities"], base["today"], base["horizon_end"])
    q = next(s for s in stored if s["gen_key"] == "quality" and s["state"] == "active")
    q.update(edited=True, minutes=75, title="我的間歇 4×8 分")
    # the preferences shorten every generated session to 30 min
    cur = cur_plan()
    for s in cur["sessions"]:
        if not s.get("done"):
            s["minutes"] = min(s["minutes"], 30)
    new, changes = R.reconcile(stored, PS.gen_weeks({**base, "cur": cur}), base["activities"], base["today"],
                               base["horizon_end"])
    q2 = next(s for s in new if s["uid"] == q["uid"])
    assert q2["minutes"] == 75 and q2["title"] == "我的間歇 4×8 分" and q2["state"] == "active"
    assert all(c["uid"] != q["uid"] for c in changes)
    long_s = next(s for s in new if s["gen_key"] == "long" and s["state"] == "active")
    assert long_s["minutes"] == 30                          # the unedited one follows the preferences


@pytest.mark.golden
def test_week_plan_with_prefs_on_the_athletes_data():
    """week_plan() itself (not only the projection): defaults identical, a
    50-min cap keeps the long / quality / strength sessions and caps them."""
    from backend.settings.paths import athlete_dir
    if not any(athlete_dir().glob("*.wko5athlete")):
        pytest.skip("no WKO5 athlete folder")
    from backend.api.overview import _dataset, _status
    from backend.engine import overview as O
    ds = _dataset()
    today = O.day_to_date(ds.today)
    st = _status(ds, today)
    base = O.week_plan(ds, st, today)
    assert O.week_plan(ds, st, today, prefs=PP.Prefs()) == base
    capped = O.week_plan(ds, st, today, prefs=PP.Prefs(cap_weekday=50, cap_mode="hard"))
    # a hot A/B race in the plan adds heat sessions; a hard cap turns them into run + bath (heat_plan.py)
    kinds = lambda wp: sorted({s["id"].rstrip("0123456789") for s in wp["sessions"]
                               if s["kind"] != "heat_passive"} - {"easy", "steep"})
    # (steep = an easy run turned into the 陡坡健走 session, engine/steep_hill.py:
    # whether one fits depends on where the cap puts the easy runs)
    assert kinds(capped) == kinds(base)                   # nothing but easy runs is lost
    for s in capped["sessions"]:
        if s["kind"] != "test" and not s["done"]:
            assert s["minutes"] <= 50, s
