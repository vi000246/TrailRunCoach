"""The AeT drift test: its length by the 課表偏好 weekday cap and its day
(engine/aet_test.py variant_for / pick_day, plan.prefs.aet_test_days). The
athlete trail-runs on weekends: with a weekday cap below 80 min the test is
UA's minimum (10′ warm-up + 40′) on Mon–Fri; without a cap it is the standard
15′ + 60′ + 5′, weekday first. Synthetic plans only — never the user's plan /
DB, never COROS."""
import datetime as dt

import pytest

from backend.engine import aet_test as AT
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as P
from backend.engine.planning import Threshold
from backend.engine.status import Status
from backend.settings import repository as SR
from backend.sync import coros_workouts as CW
from backend.tests.test_quality_gate import TODAY, _aet_series, _daily, _ds, _plan

MON = dt.date(2026, 10, 5)
WEEK = [MON + dt.timedelta(days=i) for i in range(7)]
TGT = {"long": "", "z2": "", "threshold": "", "supra": ""}
TH = {"cp": 250.0, "lthr": 165.0, "aet": 145.0}


def _day(ss, sid="test_aet"):
    s = next(s for s in ss if s["id"] == sid)
    return dt.date.fromisoformat(s["day"]) if s["day"] else None


# ---------------------------------------------------------------------------
# the length: standard 80′ without a cap, UA's 50′ minimum under a weekday cap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cap, variant, minutes, steps, why", [
    (None, "standard", 80, [900, 3600, 300], "沒有平日時間上限 → 標準版 80 分"),
    (90, "standard", 80, [900, 3600, 300], "平日上限 90 分放得下 → 標準版 80 分"),
    (50, "short", 50, [600, 2400], "平日上限 50 分 → 用 UA 最短 40 分版本"),
    (45, "short", 50, [600, 2400], "還是要 50 分：UA 不建議短於 40 分"),
])
def test_the_length_follows_the_weekday_cap(cap, variant, minutes, steps, why):
    assert AT.variant_for(cap) == variant
    s = AT.session(TH, 140.0, AT.start_power(250.0), cap)
    assert s["minutes"] == minutes and s["title"] == AT.TITLES[variant] and AT.is_aet_session(s)
    assert AT.is_short(s) is (variant == "short")
    assert why in s["detail"]                                         # which protocol and why
    assert "冷氣房跑步機 2–3%＋電扇（首選），或清晨平路環線" in s["detail"] and "不要山路" in s["detail"]
    assert "中途不停" in s["detail"] and "記下溫度；熱的時候結果會偏高" in s["detail"]
    assert "< 25 °C" not in s["detail"]
    assert "主課第 10 分鐘心率已經比起始高 10 下還在升" in s["detail"] and "evokeendurance.com" in s["source"]
    st = CW.session_steps({**s, "day": "2026-10-07"}, CW.Thresholds.of(TH))
    assert [x.seconds for x in st] == steps                           # the short test: no cool-down step
    assert st[1].intensity == ("power", round(187.5 * 0.97), round(187.5 * 1.03)) or \
        st[1].intensity == ("power", round(188 * 0.97), round(188 * 1.03))


@pytest.mark.parametrize("warm, main, cool, title", [(15, 60, 5, AT.TITLES["standard"]),
                                                     (10, 40, 0, AT.TITLES["short"]),
                                                     (15, 60, 5, ""), (10, 40, 0, "")])
def test_both_lengths_are_analysed_on_the_strict_tier(warm, main, cool, title):
    assert AT.warm_for(title, (warm + main + cool) * 60) == warm * 60
    t, h, s, p, _ = _aet_series(12.9, warm=warm, main=main, cool=cool)
    r = AT.analyze(t, h, s, p, warm_s=AT.warm_for(title, len(t)))
    assert r["ok"] and r["band"] == "at" and r["main_s"] >= AT.UA_MIN_S - AT.UA_SLACK_S
    assert r["main_s"] == pytest.approx(main * 60, abs=90)


def test_the_pref_is_not_shaping_and_validates():
    assert AT.test_days(PP.Prefs(aet_test_days="any")) is None
    assert AT.test_days(PP.Prefs()) == AT.test_days(None) == (0, 1, 2, 3, 4)
    assert SR.DEFAULTS["plan.prefs.aet_test_days"] == "weekday" == PP.Prefs().aet_test_days
    SR.validate("plan.prefs.aet_test_days", "any")
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.aet_test_days", "weekend")
    assert not PP.Prefs(aet_test_days="any").active
    p = PP.from_body({"aet_test_days": "any"})
    assert p.aet_test_days == "any" and PP.from_settings(p.settings()) == p


def test_the_short_test_fits_a_50_minute_cap_untrimmed():
    notes = []
    ctx = PP.Ctx(kind="base", mode="base", allow_quality=True, rates={"road": 50.0, "strength": 30.0}, notes=notes)
    prefs = PP.Prefs(cap_weekday=50, cap_long=150, long_day="sat")
    test = AT.session(TH, 140.0, 190.0, prefs.cap_weekday)
    ss = PP.shape([{**test, "day": None, "done": False, "done_by": None},
                   {"id": "long", "kind": "long", "title": "長時間輕鬆", "minutes": 120, "target": "", "detail": "",
                    "source": "", "tss": 100.0, "day": None, "done": False, "done_by": None}], 300, prefs, ctx)
    t = next(s for s in ss if s["id"] == "test_aet")
    assert t["minutes"] == 50 and t["detail"] == test["detail"]
    assert not any("AeT" in n["text"] for n in notes)


# ---------------------------------------------------------------------------
# the day
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("long_wd, want", [(5, 1), (6, 1), (None, 1), (2, 0), (1, 3)])
def test_pick_day_weekday_two_days_from_the_long_run(long_wd, want):
    long_day = WEEK[long_wd] if long_wd is not None else None
    avail = [d for d in WEEK if d != long_day]
    r = AT.pick_day(avail, long_day, [], AT.test_days(None))
    assert r["day"].weekday() == want and r["note"] is None
    if long_day is not None:
        assert abs((r["day"] - long_day).days) >= 2


def test_pick_day_weekend_only_for_the_standard_test():
    # no weekday left: the short test waits, the standard one may take Sunday (not the long Saturday)
    r = AT.pick_day([WEEK[6]], WEEK[5], [], AT.test_days(None))
    assert r["day"] is None and "平日" in r["note"]
    assert AT.pick_day([WEEK[6]], WEEK[5], [], AT.test_days(None), weekend_ok=True)["day"] == WEEK[6]
    # a weekday is still preferred for the standard test
    assert AT.pick_day([WEEK[3], WEEK[6]], WEEK[5], [], AT.test_days(None), weekend_ok=True)["day"] == WEEK[3]
    # not the day after the long run unless nothing else; ≥ 2 days from another hard day
    assert AT.pick_day([WEEK[2], WEEK[3], WEEK[6]], WEEK[1], [], AT.test_days(None))["day"] == WEEK[3]
    assert AT.pick_day([WEEK[2]], WEEK[1], [], AT.test_days(None))["day"] == WEEK[2]
    assert AT.pick_day(WEEK[:5], WEEK[5], [WEEK[1]], AT.test_days(None))["day"] == WEEK[3]


def _week(prefs=None, long_wd=5):
    test = AT.session(TH, 140.0, 190.0, getattr(prefs, "cap_weekday", None))
    return P.week_sessions(MON, "base", "base", 5.0, 50.0, TGT, long_wd, 90.0, True, True, 17.5, 145.0,
                           test, prefs=prefs, notes=[], aet_test_days=getattr(prefs, "aet_test_days", None))


@pytest.mark.parametrize("prefs, minutes", [(None, 80), (PP.Prefs(cap_weekday=50, cap_long=180, long_day="sat"), 50),
                                            (PP.Prefs(cap_weekday=50, long_day="sun", runs=4), 50),
                                            (PP.Prefs(runs=4, long_day="sun"), 80)])
def test_projection_puts_the_test_on_a_weekday_two_days_from_the_long_run(prefs, minutes):
    ss = _week(prefs, long_wd=5 if prefs is None else PP.LONG_WD[prefs.long_day])
    d, long_day = _day(ss), _day(ss, "long")
    assert d is not None and d.weekday() < 5 and abs((d - long_day).days) >= 2
    t = next(s for s in ss if s["id"] == "test_aet")
    assert t["minutes"] == minutes
    assert not any(s["kind"] == "quality" for s in ss)                 # it replaces the week's interval
    before = (d - dt.timedelta(days=1)).isoformat()                    # after a rest / easy day
    assert not any(s["day"] == before and s["kind"] in ("long", "quality", "test") for s in ss)


def test_projection_any_falls_back_to_the_interval_order():
    assert _day(_week(PP.Prefs(aet_test_days="any"))).weekday() == 1  # Tue, as an interval


def _build_week_ds(extra=()):
    plan = _plan(lthr=165, day="2026-09-01")
    plan.thresholds.append(Threshold("2026-09-20", cp=250.0))
    # four days off two weeks ago: no 3-build streak, so this is a build week (not 3:1)
    ws = [w for w in _daily() if not 10 <= (TODAY - w.start.date()).days <= 13
          and w.start.date() not in {x.start.date() for x in extra}]
    return _ds(ws + list(extra), plan), plan


@pytest.mark.parametrize("prefs, minutes", [(PP.Prefs(), 80), (PP.Prefs(cap_weekday=50, long_day="sat"), 50)])
def test_week_plan_puts_this_weeks_test_on_a_weekday(monkeypatch, prefs, minutes):
    ds, plan = _build_week_ds()
    st = Status(ds, plan, TODAY, prefs=prefs).compute()
    monkeypatch.setattr(AT, "due", lambda *a, **k: True)
    wp = O.week_plan(ds, st, TODAY, prefs=prefs)
    assert wp["mode"] != "recovery_week"
    (t,) = [s for s in wp["sessions"] if s["id"] == "test_aet"]
    assert t["minutes"] == minutes and t["day"] is not None and not t["done"]   # 52′ easy runs aren't the test
    d = dt.date.fromisoformat(t["day"])
    assert d.weekday() < 5 and d >= TODAY                              # Wed 9/30 – Fri 10/2
    long_day = next(dt.date.fromisoformat(s["day"]) for s in wp["sessions"] if s["kind"] == "long" and s["day"])
    assert long_day.weekday() >= 5 and abs((d - long_day).days) >= 2


def test_a_titled_50_minute_test_is_marked_done_and_analysed(monkeypatch):
    from backend.engine import workout_review as R
    from backend.tests.test_quality_gate import _aet_workout
    tday = TODAY - dt.timedelta(days=1)                               # Tue 9/29
    ds, plan = _build_week_ds([_aet_workout(tday, title="WKO5 AeT 飄移測試 40 分", main=40)])
    prefs = PP.Prefs(cap_weekday=50)
    st = Status(ds, plan, TODAY, prefs=prefs).compute()
    monkeypatch.setattr(AT, "due", lambda *a, **k: True)
    wp = O.week_plan(ds, st, TODAY, prefs=prefs)
    (t,) = [s for s in wp["sessions"] if s["id"] == "test_aet"]
    assert t["done"] and t["day"] == tday.isoformat()
    w = next(x for x in ds.workouts if x.entry.start.date() == tday)
    assert R.classify(ds, w)["type"] == "test_aet"
    r = AT.analyze_workout(ds, w)
    assert r["ok"] and r["warm_s"] == 600 and r["main_s"] == pytest.approx(2400, abs=30) and r["band"] == "at"
