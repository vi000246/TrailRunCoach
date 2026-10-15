"""
SP-671 (跑步周期化標準（SP-664）§5, the owner's B' 2026-10-15): an A race's 減量期 keeps ONE short
maintenance strength session in its first 3 days (≤ 20 min, no sets to failure, no new move, no heavy
eccentric) and none from 7 days before the race — by the taper's first day and the race, not a fixed
taper length (10 days since SP-668, 14 before it, 7 before a 2–3 day 百岳). B / C races unchanged.
Bompa & Buzzichelli's taper chapter (2 short sessions in week 1, none in week 2); Berryman 2020.
Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import balance_plan as BP
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.tests.test_b2b import _phases
from backend.tests.test_strength_stop import _plan


def _stop(race: str, days: int) -> dict:
    r = date.fromisoformat(race)
    return {"from": (r - dt.timedelta(days=days)).isoformat(), "to": race, "race": "越野賽", "days": days}


def _st(sid, day, minutes=35, done=False):
    return {"id": sid, "kind": "strength", "title": "肌力維持（高踏階＋下台階＋划船）", "minutes": minutes,
            "tss": 20.0, "detail": "", "source": "", "day": day, "done": done}


def _easy(day):
    return {"id": f"easy{day[-2:]}", "kind": "easy", "title": "輕鬆跑", "minutes": 40, "tss": 30.0, "day": day,
            "done": False}


def _maint(ss):
    return [s for s in ss if s["kind"] == "strength" and not s.get("done") and s.get("id") != BP.ID]


@pytest.mark.parametrize("days", [10, 14])
def test_keep_window_is_the_tapers_first_3_days_never_within_7_days(days):
    race = date(2026, 10, 17)                                   # Sat
    keep = O.taper_strength_days(_stop("2026-10-17", days))
    t0 = race - dt.timedelta(days=days)
    assert keep and all(0 <= (date.fromisoformat(d) - t0).days < 3 for d in keep)
    assert all((race - date.fromisoformat(d)).days > 7 for d in keep)
    # a 7-day taper (2–3 day 百岳): the first 3 days are all inside the last 7 → no session at all
    assert O.taper_strength_days(_stop("2026-10-17", 7)) == []
    # an 8-day one: only its first day
    assert O.taper_strength_days(_stop("2026-10-17", 8)) == ["2026-10-09"]


def test_ten_day_taper_keeps_one_short_session_in_its_first_3_days_none_in_the_last_7():
    # race Sat 10/17, 10-day taper from Wed 10/07: keep Wed–Fri, stop from Sat 10/10
    mon = date(2026, 10, 5)
    ss = [_st("strength1", "2026-10-06"),                       # before the taper: as it was
          _st("strength2", "2026-10-09"),                       # Fri: the taper's 3rd day → maintenance
          _easy("2026-10-08")]
    notes = []
    out = O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, notes)
    by = {s["id"]: s for s in out}
    assert by["strength1"]["minutes"] == 35
    m = by["strength2"]
    assert m["day"] == "2026-10-09" and m["minutes"] <= O.TAPER_STRENGTH_MAX_MIN
    assert m["tss"] == pytest.approx(20.0 * O.TAPER_STRENGTH_MAX_MIN / 35, abs=0.05)
    assert "減量期維持" in m["title"]
    assert "不做到力竭" in m["detail"] and "不加新動作" in m["detail"] and "離心" in m["detail"]
    assert "Bompa" in m["source"] and "Berryman" in m["source"]
    assert len(notes) == 1 and notes[0]["src"] == "strength"
    assert "Bompa" in notes[0]["text"] and "Berryman" in notes[0]["text"] and "賽前 7 天" in notes[0]["text"]
    # the race week: nothing
    out = O.drop_strength_before_a([_st("strength1", "2026-10-13"), _st("strength2", "2026-10-15")],
                                   [_stop("2026-10-17", 10)], date(2026, 10, 12), [])
    assert not _maint(out)


def test_only_one_session_kept_the_others_in_the_window_dropped():
    mon = date(2026, 10, 5)
    ss = [_st("strength1", "2026-10-07"), _st("strength2", "2026-10-08"), _st("strength3", "2026-10-10")]
    out = O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, [])
    assert [s["id"] for s in _maint(out)] == ["strength1"]


def test_a_strength_session_later_in_the_window_moves_to_an_easy_day_of_the_first_3():
    # the placement put the week's strength on Sat 10/10 (7 days out): it moves to Thu 10/08's easy run
    mon = date(2026, 10, 5)
    ss = [_easy("2026-10-08"), _st("strength1", "2026-10-10")]
    out = O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, [])
    m = _maint(out)
    assert len(m) == 1 and m[0]["day"] == "2026-10-08" and m[0]["minutes"] <= O.TAPER_STRENGTH_MAX_MIN
    # not onto a blocked (不排課) day, nor a past one (week_plan's today)
    assert not _maint(O.drop_strength_before_a([_easy("2026-10-08"), _st("strength1", "2026-10-10")],
                                               [_stop("2026-10-17", 10)], mon, [], blocked={"2026-10-08"}))
    assert not _maint(O.drop_strength_before_a([_easy("2026-10-08"), _st("strength1", "2026-10-10")],
                                               [_stop("2026-10-17", 10)], mon, [], today=date(2026, 10, 9)))


def test_done_strength_in_the_first_3_days_counts_as_the_session():
    mon = date(2026, 10, 5)
    ss = [_st("strength1", "2026-10-07", done=True), _st("strength2", "2026-10-09")]
    out = O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, [])
    assert [s["id"] for s in out] == ["strength1"]


def test_a_window_across_two_weeks_gives_one_session_in_the_week_with_most_of_its_weekdays():
    # race Sat 10/17, 14-day taper from Sat 10/03: Sat 10/03, Sun 10/04 (week of 9/28), Mon 10/05 — the
    # weekend holds the long runs, so the Monday's week has it
    stop = _stop("2026-10-17", 14)
    assert O.taper_strength_days(stop) == ["2026-10-05"]
    w1 = O.drop_strength_before_a([_st("strength1", "2026-10-03")], [stop], date(2026, 9, 28), [])
    w2 = O.drop_strength_before_a([_st("strength1", "2026-10-05")], [stop], date(2026, 10, 5), [])
    assert not _maint(w1) and len(_maint(w2)) == 1
    # Fri / Sat / Sun: the Friday's week, all three days
    assert O.taper_strength_days(_stop("2026-10-16", 14)) == ["2026-10-02", "2026-10-03", "2026-10-04"]
    # Sun / Mon / Tue: the second week
    assert O.taper_strength_days(_stop("2026-10-18", 14)) == ["2026-10-05", "2026-10-06"]


def _taper_strength(wp, weeks):
    x = wp["strength_stop"][0]
    t0, race = date.fromisoformat(x["from"]), date.fromisoformat(x["from"]) + dt.timedelta(days=x["days"])
    ss = [s for s in wp["sessions"]] + [s for w in weeks for s in w["sessions"]]
    return t0, race, [s for s in _maint(ss) if s.get("day") and t0.isoformat() <= s["day"] <= race.isoformat()]


def test_week_plan_and_projection_one_short_session_in_the_first_3_days_then_none():
    # A race Sat 11/07 (default taper), strength Tue + Sat
    prefs = PP.Prefs(strength=2, strength_days=(1, 5))
    plan, wp = _plan("2026-11-07", prefs=prefs)
    weeks = PJ.project_weeks(wp, _phases(plan, date(2026, 9, 30)), date(2026, 11, 8), prefs=prefs)
    t0, race, st = _taper_strength(wp, weeks)
    assert len(st) == 1
    s = st[0]
    d = date.fromisoformat(s["day"])
    assert (d - t0).days < 3 and (race - d).days > 7
    own = s["minutes"] - (BP.MINUTES if BP.title_tag() in s["title"] else 0)    # the balance block rides on it
    assert own <= O.TAPER_STRENGTH_MAX_MIN and "減量期維持" in s["title"]
    assert not [x for x in _maint([y for w in weeks for y in w["sessions"]])
                if x.get("day") and (race - dt.timedelta(days=7)).isoformat() <= x["day"] <= race.isoformat()]


def test_b_race_unchanged():
    prefs = PP.Prefs(strength=2, strength_days=(1, 5))
    plan, wp = _plan("2026-11-07", "B", prefs=prefs)
    weeks = PJ.project_weeks(wp, _phases(plan, date(2026, 9, 30)), date(2026, 11, 8), prefs=prefs)
    assert wp["strength_stop"] == []
    ss = [s for w in weeks for s in _maint(w["sessions"])]
    assert ss and not any("減量期維持" in s["title"] for s in ss)
