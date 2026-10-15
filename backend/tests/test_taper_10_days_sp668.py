"""
SP-668 (owner 2026-10-15, SP-664 Q1): an A race's 減量期 is 10 days by default (was 14) — Bosquet 2007 /
Wang 2023: 8–14 days best, 10 the middle. The 課表偏好 (road marathon / ultra, up to 21) stays; the B races'
mini-taper, the recovery, SP-102's 7-day B 減量 are unchanged. A 10-day taper starts mid-week for most
race days, so the week holding its first day takes the taper's share on those days only (straddle_split);
the sessions follow the same day rules (full quality ≥ 10 days out, the same kind shorter, the short one
3–5 days out, the last long run ≥ 7 / 14 days out). Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import planning as P
from backend.engine import projection as PJ
from backend.engine.planning import Event
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30


def ev(**kw):
    a = dict(id="e", name="測試", date="2027-03-07", kind="race", priority="A")      # a Sunday
    a.update(kw)
    return Event(**a)


# ---- the length -------------------------------------------------------------------------------

def test_default_is_10_days_and_the_preference_range_reaches_21():
    from backend.settings import repository as R
    assert P.TAPER_DAYS == 10 and P.TAPER_DAYS_RANGE == (10, 21)
    assert PP.Prefs().taper_days == 10
    PP.check(PP.Prefs(taper_days=10))
    PP.check(PP.Prefs(taper_days=21))
    for bad in (9, 22):
        with pytest.raises(ValueError):
            PP.check(PP.Prefs(taper_days=bad))
    assert R.DEFAULTS["plan.prefs.taper_days"] == 10
    # the B races and the recovery are not this ticket's
    assert P.MINI_TAPER_DAYS == 5 and P.B_PREP_TAPER_DAYS == 7 and P.BAIYUE_TAPER_DAYS == 7


@pytest.mark.parametrize("kw, pref, days", [
    (dict(distance_km=50, climbing_m=2800, est_hours=7.5), None, 10),          # trail 50K
    (dict(distance_km=25, climbing_m=1000, est_hours=3.5), 21, 10),            # not an ultra: the pref doesn't apply
    (dict(kind="road", distance_km=21.1, est_hours=1.9), 21, 10),              # half
    (dict(kind="road", distance_km=42.2, est_hours=4.0), None, 10),            # marathon, no pref: the default
    (dict(kind="road", distance_km=42.2, est_hours=4.0), 14, 14),              # a pref the user set is kept
    (dict(kind="road", distance_km=42.2, est_hours=4.0), 21, 21),
    (dict(distance_km=100, climbing_m=5000, est_hours=18.0), 17, 17),          # ultra
    (dict(kind="baiyue", days=2), 21, 7), (dict(kind="baiyue", days=5), 21, 10),
    (dict(kind="baiyue", days=1), None, 10),                                   # 單攻: the default
])
def test_taper_days_by_event(kw, pref, days):
    assert P.taper_days(ev(**kw), pref) == days


def test_auto_phases_put_a_10_day_taper_after_an_8_week_specific_phase():
    race = ev(distance_km=50, climbing_m=2800, est_hours=7.5)
    ph = P.auto_phases([race], date(2026, 6, 1), date(2027, 12, 31), 0)
    tap = next(p for p in ph if p.kind == "taper")
    assert (tap.start, tap.end) == ("2027-02-25", "2027-03-06")                  # Thu → Sat, 10 days
    sp = next(p for p in ph if p.kind == "specific")
    assert sp.end == "2027-02-24" and P._d(sp.start) == date(2027, 2, 25) - dt.timedelta(weeks=P.SPECIFIC_WEEKS)
    assert P.taper_start(ph, race) == date(2027, 2, 25) == P.taper_start([], race)


def test_strength_stop_is_still_the_whole_taper():
    """SP-668 keeps SP-86 as it is (SP-664 Q5 is a ticket of its own): no strength from the taper's first
    day — now 10 days."""
    assert O.STRENGTH_STOP_DAYS == P.TAPER_DAYS
    st = O.strength_stops([ev(distance_km=50, climbing_m=2800, est_hours=7.5)], date(2027, 1, 1))
    assert st == [{"from": "2027-02-25", "to": "2027-03-07", "race": "測試", "days": 10}]


# ---- the week the taper starts in -------------------------------------------------------------

@pytest.mark.parametrize("race, monday, split", [
    (date(2026, 10, 11), date(2026, 9, 28), (3, 0.50)),       # Sun race: taper from Thu, 3 days before it
    (date(2026, 10, 10), date(2026, 9, 28), (2, 0.50)),       # Sat race: from Wed
    (date(2026, 10, 9), date(2026, 9, 28), (1, 0.50)),        # Fri race: from Tue
    (date(2026, 10, 8), date(2026, 9, 28), None),             # Thu race: from Mon — a whole taper week
    (date(2026, 10, 11), date(2026, 10, 5), None),            # the race week itself
    (date(2026, 10, 11), date(2026, 9, 21), None),            # before the taper
])
def test_straddle_split(race, monday, split):
    assert O.straddle_split(race - dt.timedelta(days=10), monday, race) == split


def test_straddle_split_by_the_taper_length():
    race = date(2026, 10, 11)                                 # Sun
    assert O.straddle_split(race - dt.timedelta(days=14), date(2026, 9, 21), race) == (6, 0.50)
    assert O.straddle_split(race - dt.timedelta(days=17), date(2026, 9, 21), race) == (3, 0.75)
    assert O.straddle_split(race - dt.timedelta(days=7), date(2026, 9, 28), race) == (6, 0.40)


def test_straddle_hours():
    # Sun race, 10 h before the taper: Mon–Wed at the pre-taper level, Thu–Sun at 50 %
    assert O.straddle_hours(10.0, 10.0, 3, 0.5) == pytest.approx((3 * 10 + 4 * 5) / 7)
    # never a build on the days before: a +10 % week is held at the pre-taper level
    assert O.straddle_hours(11.0, 10.0, 3, 0.5) == pytest.approx((3 * 10 + 4 * 5) / 7)
    # a recovery week stays the recovery week on those days
    assert O.straddle_hours(7.0, 10.0, 3, 0.5) == pytest.approx((3 * 7 + 4 * 5) / 7)


def test_projected_straddle_week_takes_the_taper_share_on_its_taper_days():
    """The projection's 專項期 week holding the taper's first day (a Sun race: from Thu) is not a build
    week: Mon–Wed at the pre-taper level, Thu–Sun at 50 %; the race week 40 % of the same base."""
    ds, plan, wp = _week("2026-10-25")                        # Sun; taper from Thu 10/15
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 25), events=plan.events)
    by = {w["start"]: w for w in weeks}
    hours_at = {h["start"]: float(h["hours"]) for h in wp.get("history") or []}
    hours_at[wp["week"]["start"]] = float(wp["target"]["hours"])
    for w in weeks:
        hours_at[w["start"]] = float(w["hours"])
    base = O.taper_base(date(2026, 10, 15), lambda m: hours_at.get(m.isoformat()))
    st = by["2026-10-12"]
    assert st["phase"] == "specific"
    # Mon–Wed at most the pre-taper level (a build week is held there), Thu–Sun 50 % of it
    assert 4 / 7 * 0.5 * base < st["hours"] <= O.straddle_hours(base, base, 3, 0.5) + 1e-6
    assert any("減量期" in str(y) and "10/15" in str(y) for y in st["why"])
    race_wk = by["2026-10-19"]
    assert race_wk["phase"] == "taper" and race_wk["hours"] == pytest.approx(0.40 * base, rel=0.01)


def _week(ev_day, today=TODAY, prefs=None, **kw):
    ds = _history(today)
    plan = _plan_with("2027-06-01", 1, today)
    a = dict(distance_km=30, climbing_m=2000, est_hours=5.0)
    a.update(kw)
    plan.events = [Event("e1", "越野賽", ev_day, kind="race", priority="A", **a)]
    ds.plan = plan
    st = Status(ds, plan, today, prefs=PP.Prefs()).compute()
    return ds, plan, O.week_plan(ds, st, today, prefs=prefs)


def _base_of(wp, t0: date) -> float:
    hours_at = {h["start"]: float(h["hours"]) for h in wp.get("history") or []}
    return O.taper_base(t0, lambda m: hours_at.get(m.isoformat()))


def test_week_plan_straddle_week_from_the_specific_days_and_from_the_taper_days_agree():
    """Sun 10/11: the taper starts Thu 10/1. Seen on Wed 9/30 (專項期) and on Thu 10/1 (減量期) the week
    has the same target: Mon–Wed at the pre-taper level, Thu–Sun at 50 % of it."""
    _, _, wed = _week("2026-10-11")
    _, _, thu = _week("2026-10-11", today=date(2026, 10, 1))
    assert wed["phase"] == "specific" and thu["phase"] == "taper"
    base = _base_of(wed, date(2026, 10, 1))
    want = O.straddle_hours(base, base, 3, 0.5)
    assert wed["target"]["hours"] == pytest.approx(want, rel=0.01)
    assert thu["target"]["hours"] == pytest.approx(want, rel=0.01)
    assert any("減量期" in str(y) and "10/1" in str(y) for y in wed["why"])


# ---- the sessions -----------------------------------------------------------------------------

RACE = "2026-10-11"          # Sun; taper Thu 10/01 – Sat 10/10


def tc(sore=False, trail=True):
    return {"race": "r", "id": "e", "start": RACE, "taper_start": "2026-10-01", "days": 10, "trail": trail,
            "sore": sore, "long_days": 14 if sore else 7}


def s(id_, kind, day, minutes=50, **kw):
    return {"id": id_, "kind": kind, "day": day, "minutes": minutes, "title": "x", "tss": 50.0, "done": False,
            "detail": "", "source": "", "terrain": None, **kw}


def test_last_full_quality_is_the_specific_one_before_the_taper():
    """≥ 10 days out the session is not a taper one: the 專項期's own (Tue 9/29, 12 days out) stays; on
    the taper's first day (10 out) it is the template's full one; 6–9 days out the same kind shorter;
    3–5 days out the half-volume short one."""
    ss = [s("quality", "quality", "2026-09-29", variant_key="z3_x", title="有氧間歇（巡航）3×6 分上坡")]
    O.taper_rules(ss, tc(), date(2026, 9, 28))
    assert ss[0]["title"] == "有氧間歇（巡航）3×6 分上坡" and ss[0]["variant_key"] == "z3_x"
    ss = [s("quality", "quality", "2026-10-01")]                                              # 10 out
    O.taper_rules(ss, tc(), date(2026, 9, 28))
    assert ss[0]["title"] == O.TAPER_FULL["trail"]["title"]
    ss = [s("quality", "quality", "2026-10-02")]                                              # 9 out
    O.taper_rules(ss, tc(), date(2026, 9, 28))
    assert ss[0]["title"] == O.TAPER_CUT["trail"]["title"]
    ss = [s("quality", "quality", "2026-10-07")]                                              # 4 out
    O.taper_rules(ss, tc(), date(2026, 10, 5))
    assert ss[0]["title"] == O.TAPER_SHORT["title"]


def test_last_long_run_in_a_10_day_taper():
    ss = [s("long", "long", "2026-10-03", 150)]                                               # 8 out, Sat
    O.taper_rules(ss, tc(), date(2026, 9, 28))
    assert ss[0]["kind"] == "long" and ss[0]["minutes"] == O.TAPER_LONG_MAX_MIN
    ss = [s("long", "long", "2026-10-05", 150)]                                               # 6 out
    O.taper_rules(ss, tc(), date(2026, 10, 5))
    assert ss[0]["kind"] == "easy"


def test_sore_race_keeps_its_14_days_without_a_long_run_before_a_10_day_taper():
    """A long downhill race: the last long run ≥ 14 days out (TAPER_LONG_SORE_DAYS) although the taper is
    10 — a long run 11–13 days out (before the taper) is an easy run too, with its own note."""
    assert O.TAPER_LONG_SORE_DAYS == 14
    ss = [s("long", "long", "2026-09-29", 150), s("easy1", "easy", "2026-09-28")]            # 12 out
    notes = []
    O.taper_rules(ss, tc(sore=True), date(2026, 9, 28), notes)
    assert ss[0]["kind"] == "easy" and ss[0]["minutes"] <= 60 and ss[1]["minutes"] == 50
    assert notes and "14" in notes[0]["text"]
    ss = [s("long", "long", "2026-09-27", 150)]                                               # 14 out
    O.taper_rules(ss, tc(sore=True), date(2026, 9, 21))
    assert ss[0]["kind"] == "long" and ss[0]["minutes"] == 150                                # before it: untouched
    # not sore: before the taper nothing is touched
    ss = [s("long", "long", "2026-09-29", 150)]
    O.taper_rules(ss, tc(), date(2026, 9, 28))
    assert ss[0]["kind"] == "long" and ss[0]["minutes"] == 150


def test_taper_context_reaches_the_sore_long_run_window():
    sat = Event("e1", "越野賽", "2026-10-10", kind="race", priority="A", distance_km=30, climbing_m=2000,
                est_hours=5.0)                       # Sat; taper from Wed 9/30; 14 days out = Sat 9/26
    assert O.taper_context([], [sat], date(2026, 9, 21))["sore"]                    # 9/26–27 are 13–14 out
    flat = Event("e2", "城市馬", "2026-10-10", kind="road", priority="A", distance_km=42.2, climbing_m=50,
                 est_hours=4.0)
    assert O.taper_context([], [flat], date(2026, 9, 21)) is None


def test_week_plan_and_projection_use_the_10_day_taper():
    # Sat 10/10: the taper starts today (Wed 9/30)
    ds, plan, wp = _week("2026-10-10")
    assert wp["phase"] == "taper" and wp["taper"]["taper_start"] == "2026-09-30" and wp["taper"]["days"] == 10
    note = next(n for n in wp["notes"] if n.get("src") == "taper")
    assert "前 10 天" in note["text"]
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18), events=plan.events)
    race_wk = next(w for w in weeks if w["start"] == "2026-10-05")
    assert race_wk["phase"] == "taper"
    for x in race_wk["sessions"]:
        out = (date(2026, 10, 10) - date.fromisoformat(x["day"])).days
        if x["kind"] == "quality":
            assert 3 <= out <= 5 and x["title"] == O.TAPER_SHORT["title"]
        assert x["kind"] != "long"
        assert x["kind"] != "strength" or x["id"] == "balance"            # SP-86: the balance block stays


def test_texts_say_10_days_not_14():
    import inspect
    src = inspect.getsource(O)
    assert "最後一次完整強度課賽前 10–14 天" not in src
    assert "最後一次完整強度課在賽前 10–14 天" not in src


def test_the_week_the_taper_starts_in_keeps_one_quality_session():
    """The 專項期 days of that week already hold the last full quality (Tue, 12 out): a second one on its
    taper days (a 技術地形 RPE 6–7 / interval on Thu) is an easy run — one hard session a week, as the
    taper weeks have; without one before the taper, the taper day's quality takes the template."""
    ss = [s("quality", "quality", "2026-09-29", title="有氧間歇（巡航）2×12 分上坡"),
          s("tech", "quality", "2026-10-01", 75, title="技術地形 75′（RPE 6–7）")]
    notes = []
    O.taper_rules(ss, tc(), date(2026, 9, 28), notes)
    assert ss[0]["title"] == "有氧間歇（巡航）2×12 分上坡"
    assert ss[1]["kind"] == "easy" and ss[1]["minutes"] <= 60
    assert "9/29 已有強度課" in notes[0]["text"]
    # done before the taper counts too
    ss = [s("quality", "quality", "2026-09-29", done=True), s("q2", "quality", "2026-10-02")]
    O.taper_rules(ss, tc(), date(2026, 9, 28))
    assert ss[1]["kind"] == "easy"
    # none before: the taper day's one takes its template (9 out: the same kind shorter)
    ss = [s("quality", "quality", "2026-10-02")]
    O.taper_rules(ss, tc(), date(2026, 9, 28))
    assert ss[0]["title"] == O.TAPER_CUT["trail"]["title"]
    # the race week is a week of its own: its short one stays
    ss = [s("quality", "quality", "2026-10-07")]
    O.taper_rules(ss, tc(), date(2026, 10, 5))
    assert ss[0]["title"] == O.TAPER_SHORT["title"]
