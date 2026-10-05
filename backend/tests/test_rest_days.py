"""
SP-82: where the rest days go (engine/rest_days.py; docs/research/rest-day-placement.md §4, the
owner's decisions §4.4), the 休息日偏好 in 課表偏好 (pref_days["rest"], first / second choice), auto
mode's ≤ 6 runs a week, strength off the rest days, and moving a rest day on the 課表 calendar
(POST /rest-days/move). Synthetic data.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import plan_prefs as PP
from backend.engine import projection as P
from backend.engine import rest_days as RD

MON = date(2026, 10, 5)
WEEK = [MON + dt.timedelta(days=i) for i in range(7)]
TUE, SAT, SUN = WEEK[1], WEEK[5], WEEK[6]
TGT = {"long": "", "threshold": "", "z2": "", "supra": ""}
RATES = {"road": 50.0, "trail": 60.0, "hike": 45.0, "strength": 30.0}


def wds(days):
    return "".join("一二三四五六日"[d.weekday()] for d in days)


@pytest.mark.parametrize("n, want", [(1, "四"), (2, "一四"), (3, "一三四"), (4, "一三四五")])
def test_the_research_patterns(n, want):
    """rest-day-placement.md §3 (long run Sat, interval Tue): 3 runs → － Q － E － L －,
    4 → E Q － E － L －, 5 → E Q E E － L －, 6 → E Q E E E L －."""
    avail = [d for d in WEEK if d not in (TUE, SAT)]
    assert wds(RD.pick_days(n, avail, WEEK, [TUE, SAT], SAT, 5, [TUE])) == want


def test_rules():
    avail = [d for d in WEEK if d not in (TUE, SUN)]
    # a Sunday long run: the day after it is this Monday (last week's long run) and next Monday
    assert "一" not in wds(RD.pick_days(2, avail, WEEK, [TUE, SUN], SUN, 6, [TUE]))
    # the 休息日偏好 wins over the rules: first choice Thu, second Mon
    got = RD.pick_days(3, [d for d in WEEK if d not in (TUE, SAT)], WEEK, [TUE, SAT], SAT, 5, [TUE], (3, 0))
    assert wds(got) == "三五日"
    got = RD.pick_days(4, [d for d in WEEK if d not in (TUE, SAT)], WEEK, [TUE, SAT], SAT, 5, [TUE], (3, 0))
    assert "四" not in wds(got) and "一" in wds(got)              # one more run: the second choice gives way
    # the same input always gives the same week; no more days than there are
    assert RD.pick_days(9, [WEEK[0]], WEEK, [], None, None) == [WEEK[0]]
    assert RD.pick_days(0, avail, WEEK, [], None, None) == []


def test_strength_days_prefer_easy_days():
    assert RD.strength_days([WEEK[3], WEEK[0]], [WEEK[2], WEEK[4]], avoid=[WEEK[4]]) == [WEEK[0], WEEK[3], WEEK[2]]
    # a free day only after the easy days; the 休息日偏好 day last of all (first choice after the second)
    assert RD.strength_days([], [WEEK[1], WEEK[2], WEEK[3]], rest_pref=(1, 2)) == [WEEK[3], WEEK[2], WEEK[1]]


def _week(prefs=None, hours=8.0, long_wd=5, **kw):
    return P.week_sessions(MON, "base", "base", hours, 50.0, TGT, long_wd, 120.0, False, True, 17.5, 150.0,
                           None, prefs=prefs, rates=RATES, notes=[], **kw)


def _runs(ss):
    return [s for s in ss if s["kind"] != "strength" and s["day"]]


def test_auto_mode_keeps_a_rest_day_after_the_long_run():
    ss = _week(hours=9.0)                                          # easy_count alone would make 7–8 runs
    runs = _runs(ss)
    assert len(runs) == RD.AUTO_MAX_RUNS and len({s["day"] for s in runs}) == len(runs)
    assert SUN.isoformat() not in {s["day"] for s in runs}          # the day after Saturday's long run
    assert abs(sum(s["minutes"] for s in runs) - 9.0 * 60) <= 15    # the minutes went to the other runs
    # strength on easy days, never the interval's day or the day before the long run
    q = {s["day"] for s in runs if s["kind"] == "quality"}
    easy = {s["day"] for s in runs if s["kind"] == "easy"}
    for s in (s for s in ss if s["kind"] == "strength"):
        assert s["day"] in easy and s["day"] not in q and s["day"] != WEEK[4].isoformat()
    # few runs: the rest days spread out instead of piling up after Wednesday
    few = _runs(_week(hours=4.0))
    assert len(few) == 4 and wds(sorted(date.fromisoformat(s["day"]) for s in few)) == "一二四六"


def test_prefs_rest_preference_and_the_7_runs_choice():
    p = PP.Prefs(pref_days=(("rest", (1, 3)),), cap_weekday=90)     # rather rest Tue, then Thu
    assert p.active and p.pref_of("rest") == (1, 3)
    days = {date.fromisoformat(s["day"]).weekday() for s in _runs(_week(p, hours=5.0))}
    assert len(days) == 5 and 1 not in days and 3 not in days
    days = [date.fromisoformat(s["day"]).weekday() for s in _runs(_week(p, hours=8.0))]
    assert len(days) == RD.AUTO_MAX_RUNS and 1 not in days         # auto ≤ 6: the first choice stays free
    # 每週跑步次數 7 is the athlete's call
    seven = _runs(_week(PP.Prefs(runs=7, cap_weekday=90), hours=8.0))
    assert len(seven) == 7


def test_settings_and_conflicts_accept_the_rest_row():
    from backend.settings.repository import validate
    validate("plan.prefs.pref_days", {"rest": [1, 3]})
    with pytest.raises(ValueError):
        validate("plan.prefs.pref_days", {"rest": [1, 3, 5]})
    p = PP.from_settings({"plan.prefs.pref_days": {"rest": [1, 3]}, "plan.prefs.days": [True] * 6 + [False]})
    assert p.pref_of("rest") == (1, 3) and p.to_dict()["pref_days"] == {"rest": [1, 3]}
    # a rest day on a non-可練日 is no conflict (it's a rest day anyway); a rest day on the long run's day is
    assert not PP.day_conflicts(PP.Prefs(pref_days=(("rest", (6,)),), days=(True,) * 6 + (False,)))
    with pytest.raises(ValueError):
        PP.check(PP.Prefs(long_day="sat", pref_days=(("rest", (5,)),)))


# ---- 移動休息日 on the calendar -------------------------------------------------------------

def test_api_move_rest_day(monkeypatch):
    from backend.sync import coros_workouts as CW
    from backend.tests.test_plan_store import API, Env
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    with Env(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        assert not any(s["day"] == "2026-10-03" for s in ss)                   # Sat: a rest day
        on_fri = {s["uid"] for s in ss if s["day"] == "2026-10-02" and s["state"] == "active"}
        r = e.c.post(f"{API}/rest-days/move", json={"from": "2026-10-03", "to": "2026-10-02"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert {s["uid"] for s in body["moved"]} == on_fri and all(s["day"] == "2026-10-03" and s["edited"]
                                                                   for s in body["moved"])
        assert body["warnings"] == []
        # the long run (Sun) to Fri: 1 day from Thu's interval → a warning
        r = e.c.post(f"{API}/rest-days/move", json={"from": "2026-10-02", "to": "2026-10-04"}).json()
        assert [s["kind"] for s in r["moved"]] == ["long"] and r["warnings"] and "只隔 1 天" in r["warnings"][0]
        bad = [{"from": "2026-10-04", "to": "2026-10-04"},                      # same day
               {"from": "2026-10-03", "to": "2026-10-01"},                      # 10/3 has sessions now
               {"from": "2026-09-28", "to": "2026-10-01"},                      # past
               {"from": "x", "to": "2026-10-01"}]
        for b in bad:
            assert e.c.post(f"{API}/rest-days/move", json=b).status_code == 400, b
