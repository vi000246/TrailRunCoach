"""
SP-545: a multi-day 百岳's check — 「你一次能爬多少」 is the biggest single day of the last 6 months, further back
(at most 24) only while the training was normal (R1: no 停練 ≥ 14 days, no 4 weeks under half the usual volume,
no 中／重 injury); every weekend counts once it reaches the summit day, +10 % a week only below it; a steep enough slope stands in
for the pack; the card says how hard the trip will feel on the race calculator's effort words (輕鬆／穩定／
吃力／極限／超出) instead of 來得及／來不及; too few simulations is a yellow suggestion, red stays for the
turnaround and 時間不夠; the climb-rate line names both rates and which one it compares. Synthetic events
and activities; nothing reads the athlete's data.
"""
import datetime as dt
import json
import re
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import baiyue_multiday as BM
from backend.engine import race_feasibility as F
from backend.engine.racepower import difficulty as DF
from backend.i18n import use_locale
from backend.tests.test_baiyue_multiday import TODAY, hist, line, row, trip
from backend.tests.test_feasibility_card_sp486 import NODE, STATIC, _card, _render, needs_node

CJK = re.compile(r"[一-鿿]")


def best(climb, hours=10.0, when=date(2025, 11, 1), months=24):
    """long_best of an ability window of `months` that reached its 24 months (nothing interrupted it)."""
    return {"climb_m": float(climb), "climb_date": when, "hours": float(hours), "hours_date": when,
            "window": {"since": BM.months_back(TODAY, months), "months": months, "extended": months > BM.BASE_MONTHS,
                       "stop": None}}


def run(e, rows, b=None, today=TODAY, summit=None):
    ln = line(e)
    r = F.assess(e, ln, today, hist()[-4:], summit)
    r["readiness"] = F.readiness(e, ln, today, hist(), [])
    BM.apply(r, e, ln, today, None, rows, b)
    r["hints"] = F.card_hints(r)
    return r


def check(r, cid):
    return next(c for c in r["checks"] if c["id"] == cid)


def steep(days_ago, climb, grade, pack=None):
    return {**row(days_ago, climb, pack), "grade": grade}


# ---- 1. the long-term biggest day, every weekend ------------------------------------------------------------

def test_long_term_best_reaching_the_summit_day_counts_every_weekend_not_plus_10_percent():
    """Trip 12/19 (window 10/24–12/9), the last 8 weeks' best only 400 m but 1600 m within 24 months: every
    weekend outside the recovery weeks is a possible simulation — 10/24, 10/31, 11/14, 11/21, 11/28 (11/7
    and 12/5 are recovery weeks, 12/12 is inside the last 10 days)."""
    assert BM.LONG_MONTHS == F.STEP_MONTHS == 24                      # the same window as 跨級
    e = trip()
    pj = BM.projected(1600.0, 1500.0, TODAY, e.start)
    assert pj["every"] and pj["n"] == 5 and pj["first"] == date(2026, 10, 24)
    assert BM.projected(400.0, 1500.0, TODAY, e.start)["n"] == 0      # the old way from the 8-week best: none
    r = run(e, [row(3, 400.0)], best(1600.0))
    c = check(r, "summit_sim")
    assert c["every"] and c["projected"] == 5 and c["best_m"] == 1600 and c["level"] == "ok"
    assert "近 24 個月一天最多爬過 1600 m（2025-11-01）" in c["text"] and "每個週末" in c["text"] and "推估" in c["text"]


def test_below_the_summit_day_it_grows_plus_10_percent_from_the_long_term_best():
    e = trip(start="2026-11-14")                                      # window 9/19–11/4
    r = run(e, [row(3, 400.0)], best(1250.0))
    c = check(r, "summit_sim")
    assert not c["every"] and c["projected"] == BM.projected(1250.0, 1500.0, TODAY, e.start)["n"] == 2
    assert "每週最多 +10%" in c["text"] and "1250 m" in c["text"]


def test_this_weeks_weekend_counts_while_it_is_ahead_and_nothing_was_done_this_week():
    start = date(2026, 12, 19)
    wed, sun = date(2026, 10, 21), date(2026, 10, 25)                 # Saturday 10/24 is inside the window
    n_wed = BM.projected(1600.0, 1500.0, wed, start)["n"]
    assert n_wed == BM.projected(1600.0, 1500.0, sun, start)["n"] + 1
    assert BM.projected(1600.0, 1500.0, wed, start, (date(2026, 10, 20),))["n"] == n_wed - 1


def test_a_multi_day_activity_counts_day_by_day(monkeypatch):
    """A 3-day trip stored as one activity (3500 m) is three days (1400 / 800 / 1300 m), from the achievements
    cache's per-day split: the long-term best is 1400 m, not 3500 m."""
    from backend.engine import achievements as ACH
    from backend.engine import overview as O
    from backend.engine.wko5expr.dataset import date_to_day
    trip_w = SimpleNamespace(entry=SimpleNamespace(file="trip.fit"), day=date_to_day(date(2026, 8, 1)),
                             metrics={"climbing": 3500.0, "distance": 40.0, "movingduration": 70000.0})
    run_w = SimpleNamespace(entry=SimpleNamespace(file="run.fit"), day=date_to_day(date(2026, 9, 1)),
                            metrics={"climbing": 900.0, "distance": 15.0, "movingduration": 9000.0})
    days = [{"date": "2026-08-01", "distance_km": 12.0, "climbing_m": 1400, "moving_s": 25000, "top_m": 3000},
            {"date": "2026-08-02", "distance_km": 10.0, "climbing_m": 800, "moving_s": 20000, "top_m": 3400},
            {"date": "2026-08-03", "distance_km": 18.0, "climbing_m": 1300, "moving_s": 25000, "top_m": 3000}]
    monkeypatch.setattr(ACH, "_load_cache", lambda: {"trip.fit": {"summary": {"days": days}}})
    monkeypatch.setattr(O, "workouts_between", lambda ds, a, b: [trip_w, run_w])
    monkeypatch.setattr(O, "category", lambda w: "hike")
    rows = BM.long_rows(object(), TODAY)
    assert [(r["date"], r["climb_m"], r["day_no"]) for r in rows] == [
        (date(2026, 8, 1), 1400.0, 1), (date(2026, 8, 2), 800.0, 2), (date(2026, 8, 3), 1300.0, 3),
        (date(2026, 9, 1), 900.0, None)]
    b = BM.long_best(rows)
    assert b["climb_m"] == 1400.0 and b["climb_date"] == date(2026, 8, 1) and b["hours"] == pytest.approx(25000 / 3600)


# ---- 1b. R1: 6 months, further back only while the training was normal ------------------------------------

def foot(start, end, every=3, hours=1.0, climb=300.0, off=(), low=(), low_hours=0.3):
    """A foot day every `every` days from `start` to `end` (dates), none inside the `off` ranges, `low_hours`
    inside the `low` ranges."""
    out, d = [], start
    while d <= end:
        if not any(a <= d <= b for a, b in off):
            h = low_hours if any(a <= d <= b for a, b in low) else hours
            out.append({"date": d, "climb_m": climb, "km": 8.0, "hours": h, "day_no": None})
        d += dt.timedelta(days=every)
    return out


def big(when, climb=2100.0, hours=7.0):
    return {"date": when, "climb_m": climb, "km": 20.0, "hours": hours, "day_no": None}


SINCE = TODAY - dt.timedelta(days=24 * 365 // 12 + 7 * BM.NORMAL_WEEKS)    # what ability_best reads


def test_six_months_by_default_when_a_recent_break_interrupted_them():
    """A 20-day 停練 3 months ago: the window stays at 6 months, the 2100 m day 10 months ago is out."""
    gap = (TODAY - dt.timedelta(days=100), TODAY - dt.timedelta(days=80))
    rows = foot(SINCE, TODAY, off=[gap]) + [big(TODAY - dt.timedelta(days=300))]
    win = BM.ability_window(rows, TODAY)
    assert win["since"] == BM.months_back(TODAY, 6) and win["months"] == 6 and not win["extended"]
    assert win["stop"]["kind"] == "break" and win["stop"]["days"] >= BM.BREAK_DAYS
    assert BM.long_best(win["rows"])["climb_m"] == 300.0
    assert BM.window_text(win).startswith("「一天最多」看近 6 個月：") and "停練" in BM.window_text(win)
    assert "不往前延伸" in BM.window_text(win)


def test_normal_training_reaches_back_to_after_the_latest_older_interruption():
    """Normal for the last 6 months; a 30-day 停練 15 months ago: back to the day after it (~14 months)."""
    gap_end = TODAY - dt.timedelta(days=430)
    rows = foot(SINCE, TODAY, off=[(gap_end - dt.timedelta(days=30), gap_end)])
    rows += [big(TODAY - dt.timedelta(days=300)), big(TODAY - dt.timedelta(days=500), climb=2600.0)]
    win = BM.ability_window(rows, TODAY)
    assert win["extended"] and win["stop"]["kind"] == "break"
    assert BM.months_back(TODAY, 24) < win["since"] <= gap_end + dt.timedelta(days=3)
    assert win["months"] == 14
    assert BM.long_best(win["rows"])["climb_m"] == 2100.0               # the 2600 m day before the break is out
    assert "往前延伸到上一次中斷之後" in BM.window_text(win)


def test_nothing_interrupted_reaches_the_full_24_months():
    rows = foot(SINCE, TODAY) + [big(TODAY - dt.timedelta(days=600))]
    win = BM.ability_window(rows, TODAY)
    assert win["extended"] and win["stop"] is None and win["months"] == 24
    assert win["since"] == BM.months_back(TODAY, 24) and BM.long_best(win["rows"])["climb_m"] == 2100.0
    assert "沒有停練、減量或傷停" in BM.window_text(win)


def test_a_13_day_gap_is_not_a_break():
    a = TODAY - dt.timedelta(days=100)
    rows = [r for r in foot(SINCE, TODAY, every=1) if not (a < r["date"] < a + dt.timedelta(days=14))]
    assert not [x for x in BM.interruptions(rows, TODAY) if x["kind"] == "break"]
    rows = [r for r in foot(SINCE, TODAY, every=1) if not (a < r["date"] < a + dt.timedelta(days=15))]
    assert [x["days"] for x in BM.interruptions(rows, TODAY) if x["kind"] == "break"] == [14]


def test_four_weeks_under_half_the_usual_volume_is_reduced_three_are_not():
    """Usual ~2.3 h a week; 0.3 h runs (0.7 h a week, < 50 %) for 3 weeks = a taper / recovery, not 減量;
    for 5 weeks it is, and the window stops after it."""
    mon = BM.months_back(TODAY, 12)
    mon -= dt.timedelta(days=mon.weekday())
    three = foot(SINCE, TODAY, low=[(mon, mon + dt.timedelta(days=20))])
    assert not BM.interruptions(three, TODAY)
    five = foot(SINCE, TODAY, low=[(mon, mon + dt.timedelta(days=34))])
    lows = [x for x in BM.interruptions(five, TODAY) if x["kind"] == "low"]
    assert len(lows) == 1 and lows[0]["start"] == mon and lows[0]["weeks"] == 5
    win = BM.ability_window(five, TODAY)
    assert win["extended"] and win["since"] == lows[0]["end"] + dt.timedelta(days=1) and win["months"] == 11
    assert "連續 5 週運動量不到平常的一半" in BM.window_text(win)


def test_a_moderate_or_severe_injury_stops_it_mild_and_illness_do_not():
    rows = foot(SINCE, TODAY)
    onset = TODAY - dt.timedelta(days=400)
    ev = {"area": "knee", "side": "left", "onset_date": onset.isoformat(), "status": "resolved",
          "resolved_date": (onset + dt.timedelta(days=20)).isoformat(), "category": "injury"}
    win = BM.ability_window(rows, TODAY, [{**ev, "severity": "moderate"}])
    assert win["stop"]["kind"] == "injury" and win["since"] == onset + dt.timedelta(days=21)
    assert "傷停（左膝）" in BM.window_text(win)
    recent = {**ev, "severity": "severe", "onset_date": (TODAY - dt.timedelta(days=60)).isoformat(),
              "resolved_date": (TODAY - dt.timedelta(days=40)).isoformat()}
    assert not BM.ability_window(rows, TODAY, [recent])["extended"]
    for other in ({**ev, "severity": "mild"}, {**ev, "severity": "severe", "category": "illness"}):
        assert BM.ability_window(rows, TODAY, [other])["months"] == 24


def test_apply_reads_the_window_and_the_card_says_how_far_back(monkeypatch):
    """ability_best over the athlete's foot days: a break 3 months ago keeps it at 6 months — the effort item
    and the simulations say 「近 6 個月」 and the effort line says why."""
    gap = (TODAY - dt.timedelta(days=100), TODAY - dt.timedelta(days=80))
    rows = foot(SINCE, TODAY, off=[gap]) + [big(TODAY - dt.timedelta(days=300)), big(TODAY - dt.timedelta(days=30),
                                                                                    climb=1600.0, hours=6.0)]
    monkeypatch.setattr(BM, "foot_days", lambda ds, a, b: [r for r in rows if a <= r["date"] < b])
    b = BM.ability_best(object(), TODAY, injuries=[])
    assert b["climb_m"] == 1600.0 and b["window"]["months"] == 6 and not b["window"]["extended"]
    r = run(trip(), [row(3, 400.0)], b)
    c = check(r, "effort")
    assert "你近 6 個月一天最多 1600 m" in c["text"] and "「一天最多」看近 6 個月：" in c["text"]
    assert c["window"]["months"] == 6 and c["window"]["stop"]["kind"] == "break"
    assert "近 6 個月一天最多爬過 1600 m" in check(r, "summit_sim")["text"]
    json.dumps(r, ensure_ascii=False, default=str)
    with use_locale("en"):
        txt = BM.window_text(BM.ability_window(rows, TODAY))
        txt2 = BM.window_text(BM.ability_window(foot(SINCE, TODAY), TODAY))
        txt3 = BM.window_text(BM.ability_window(foot(SINCE, TODAY, low=[(TODAY - dt.timedelta(days=400),
                                                                          TODAY - dt.timedelta(days=360))]), TODAY))
        txt4 = BM.window_text(BM.ability_window(foot(SINCE, TODAY), TODAY, [
            {"area": "shin_calf", "side": "left", "severity": "severe", "category": "injury", "status": "resolved",
             "onset_date": "2025-09-01", "resolved_date": "2025-09-20"}]))
    assert "injury layoff (left shin / calf)" in txt4
    for t in (txt, txt2, txt3, txt4):
        assert t and not CJK.search(t), t


# ---- 2. a steep enough slope stands in for the pack ------------------------------------------------------------

def _profile(*legs, step=10.0):
    """(d, z) from legs of (metres, grade)."""
    d, z = [0.0], [1000.0]
    for dist, g in legs:
        for _ in range(int(dist / step)):
            d.append(d[-1] + step)
            z.append(z[-1] + g * step)
    return np.array(d), np.array(z)


def test_uphill_grade_is_the_climbing_parts_average():
    d, z = _profile((1000, 0.0), (2000, 0.25), (1000, -0.20), (1000, 0.02))
    assert BM.uphill_grade(d, z) == pytest.approx(0.25, abs=0.005)    # the flat, the descent, the 2 % out
    d, z = _profile((3000, 0.10), (3000, -0.10))
    assert BM.uphill_grade(d, z) == pytest.approx(0.10, abs=0.005)
    assert BM.uphill_grade(*_profile((3000, 0.0))) is None
    assert BM.STEEP_GRADE == 0.20 and BM.is_steep({"grade": 0.20}) and not BM.is_steep({"grade": 0.199})
    assert not BM.is_steep({"grade": None}) and not BM.is_steep({})


def test_a_steep_day_without_a_pack_is_a_simulation_in_feasibility_and_readiness():
    e = trip(start="2026-11-14")                                      # since 9/19, pack ≥ 9.6 kg
    rows = [steep(2, 1520.0, 0.22),                                   # steep, no pack: counts
            steep(4, 1600.0, 0.15),                                   # not steep enough, no pack
            row(6, 1550.0, 12.0),                                     # a pack: counts as before
            row(8, 1700.0, None),                                     # no grade known, no pack
            steep(9, 900.0, 0.30)]                                    # steep but too little climb
    r = run(e, rows, best(1700.0))
    c = check(r, "summit_sim")
    assert c["done"] == 2 and c["steep"] == 1
    rc = r["readiness"]["checks"][0]
    assert rc["count"] == 2 and rc["steep"] == 1 and rc["no_pack"] == 2
    assert "其中 1 次沒背包，靠坡夠陡算進來" in rc["text"] and "坡度也不到 20%" in rc["text"]
    assert "或沒背包但爬坡段平均坡度 ≥ 20%" in rc["text"]
    assert any("Pandolf 1977" in s and "Scott Johnston" in s and "推估" in s for s in r["src"])


# ---- 3. how hard, on the calculator's words; red only for the real danger --------------------------------

def test_effort_uses_the_race_calculators_words_and_cut_points():
    assert [BM.EFFORT_LABEL[k] for k, _n, _lo in DF.LABELS] == [n for _k, n, _lo in DF.LABELS]
    sd = {"day": 1, "climb_m": 1000.0, "hours": 1.0}
    for pct, key in ((0.79, "easy"), (0.80, "steady"), (0.90, "hard"), (0.97, "max"), (1.0, "max"), (1.01, "over")):
        ef = BM.effort(sd, best(1000.0 / pct, hours=100.0), None, False)
        assert ef["key"] == key and ef["driver"] == "climb", pct
    # the hardest item wins: hours over the longest day
    ef = BM.effort({"day": 1, "climb_m": 1000.0, "hours": 9.0}, best(2000.0, hours=8.0), None, False)
    assert ef["driver"] == "hours" and ef["key"] == "over"
    # the climb rate counts only with a turnaround (without one it is the predicted time read back)
    vam = {"need": 727, "steady": 602, "hard": 613}
    ef = BM.effort({"day": 1, "climb_m": 1167.0, "hours": 4.59}, best(1600.0, hours=9.0), vam, False)
    assert ef["key"] == "easy" and ef["driver"] == "climb"
    assert next(it for it in ef["items"] if it["id"] == "vam")["counted"] is False
    ef = BM.effort({"day": 1, "climb_m": 1167.0, "hours": 4.59}, best(1600.0, hours=9.0), vam, True)
    assert ef["key"] == "over" and ef["driver"] == "vam"


def test_card_says_how_hard_and_what_to_change():
    e = trip()
    r = run(e, [row(3, 400.0)], best(1600.0))                         # 1500 / 1600 = 94 %: 吃力
    c = check(r, "effort")
    assert r["checks"][0]["id"] == "effort" and c["key"] == "hard" and c["label"] == "吃力"
    assert r["label"] == "預估：吃力" and r["level"] == "tight" and r["effort"]["title"] == "預估：吃力"
    assert "攻頂日爬升 1500 m＝你近 24 個月一天最多 1600 m（2025-11-01）的 94%（吃力）" in c["text"]
    assert "攻頂日約 8.0 小時＝你近 24 個月最長一天 10.0 小時的 80%（穩定）" in c["text"]
    assert "80% 以下輕鬆、80–90% 穩定、90–97% 吃力、97–100% 極限、超過 100% 超出（推估）" in c["text"]
    adv = r["suggestions"][0]
    assert adv == "預估吃力，最吃力的是攻頂日爬升：多排一天把攻頂日拆短、換攻頂日爬升少一點的路線，或先做幾次攻頂日模擬"
    assert adv.hint_id == "PL9" and adv.hint_cls == 2
    easy = run(e, [row(3, 400.0)], best(3000.0, hours=20.0))          # 50 % / 40 %: 輕鬆, green
    assert easy["label"] == "預估：輕鬆" and easy["level"] == "ok" and not easy["suggestions"]
    assert all("來得及" not in x["label"] and "來不及" not in x["text"] for x in easy["checks"])


def test_too_few_simulations_is_a_yellow_suggestion_never_red():
    e = trip(start="2026-11-14")                                      # 6 weeks out, 400 m: none fits
    r = run(e, [row(5, 400.0)])
    c = check(r, "summit_sim")
    assert c["level"] == "tight" and c["label"] == "建議" and r["level"] == "tight"
    assert "建議做 3 次" in c["text"]
    assert "建議行程前做 3 次攻頂日模擬，目前排得下 0 次：每週排一次" in "".join(r["suggestions"])
    blob = json.dumps(r, ensure_ascii=False, default=str)
    assert "來不及" not in blob and '"level": "over"' not in blob
    assert not any(h["cls"] == 1 for h in r["hints"])                 # no red-bordered class-1 line
    assert r["readiness"]["checks"][0]["level"] == "tight" and r["readiness"]["checks"][0]["label"] == "建議"
    # the simulations do not set the level: an easy trip with only 2 weekends left stays green
    e = trip(start=(TODAY + dt.timedelta(days=24)).isoformat())       # window 9/4–10/19: 10/10, 10/17
    r = run(e, [], best(3000.0, hours=20.0))
    c = check(r, "summit_sim")
    assert c["projected"] == 2 and c["level"] == "tight" and r["level"] == "ok" and r["label"] == "預估：輕鬆"
    assert any("目前排得下 2 次：週末排一天" in s for s in r["suggestions"])


def test_turnaround_missed_and_late_stay_red():
    e = trip(cutoff_hours=5.0)
    r = run(e, [], best(3000.0, hours=20.0), summit={"day": 1, "hours": 6.0})
    assert check(r, "cutoff")["level"] == "over" and r["level"] == "over" and r["label"] == "太難了"
    assert r["effort"]["title"] == "預估：輕鬆"                       # shown as a second pill
    assert r["suggestions"][0].startswith("預估還沒到山頂就得撤退")
    e = trip(start=(TODAY + dt.timedelta(days=14)).isoformat())
    r = run(e, [], best(3000.0, hours=20.0))
    assert r["level"] == "late" and r["label"] == "時間不夠"


def test_step_two_classes_up_is_a_yellow_reminder_for_a_multi_day_baiyue():
    big = [{"km": 30.0, "gain_m": 2500, "loss_m": 600}, {"km": 15.0, "gain_m": 900, "loss_m": 900},
           {"km": 30.0, "gain_m": 400, "loss_m": 2600}]
    e = trip(day_plan=big, distance_km=75, climbing_m=3800)
    ln = line(e)
    r = F.assess(e, ln, TODAY, hist()[-4:], best={"ep": 10.0, "date": date(2026, 5, 1)})
    assert check(r, "step")["level"] == "over"                        # a trail race keeps it red
    BM.apply(r, e, ln, TODAY, None, [], best(3000.0, hours=20.0))
    assert check(r, "step")["level"] == "tight" and r["level"] == "ok"
    pl10 = next(s for s in r["suggestions"] if s.hint_id == "PL10")
    assert pl10.hint_cls == 2 and "低一級的路線" in pl10


# ---- 4. the climb-rate line ----------------------------------------------------------------------------------

def _rates():
    return {"steady": {"vam": 600.0, "n": 30, "z": None}, "hard": {"vam": 700.0, "n": 30, "z": None}}


@pytest.mark.parametrize("hours, lv, says", [(4.0, "ok", "在走得順以內"), (3.333, "tight", "超過走得順只是有點趕"),
                                              (2.0, "tight", "連走得辛苦都超過")])
def test_climb_rate_line_names_both_rates_and_which_one_is_compared(hours, lv, says):
    e = trip(pack_kg=None)
    vc = F.vam_check(e, {"km": 10.0, "climb_m": 1000.0, "hours": hours}, _rates(), None, None, 100.0)
    assert vc["level"] == lv and says in vc["text"]
    assert "你走得順約 558 m/h、走得辛苦約 652 m/h" in vc["text"] and "比的是走得順" in vc["text"]
    assert vc["steady"] == 558 and vc["hard"] == 652


def test_climb_rate_chip_shows_both_rates():
    for loc, words in (("zh-TW", ("走得順 {s}", "走得辛苦 {h}")), ("en", ("steady {s}", "hard {h}"))):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
        assert all(w in cat["feas.k_vam"] for w in words)
        assert {"feas.k_effort", "feas.feas_effort"} <= set(cat)


# ---- the card under node, zh-TW and en ----------------------------------------------------------------------

@needs_node
def test_the_card_shows_the_effort_and_both_climb_rates():
    e = trip()
    r = run(e, [row(3, 400.0)], best(1600.0))
    shown, hidden = _card(_render([json.loads(json.dumps(r, ensure_ascii=False, default=str))], "standard")[0])
    assert "以現在的能力" in shown and "預估：吃力" in shown and "最吃力：攻頂日爬升 94%" in shown
    assert "來不來得及練" not in shown
    assert "來不來得及練" not in hidden and "以現在的能力」：攻頂日的爬升" in hidden    # the details' heading and how-text
    en = _render([json.loads(json.dumps(r, ensure_ascii=False, default=str))], "standard", locale="en")[0]
    assert "With today&#39;s fitness" in en


def test_english_texts_have_no_chinese():
    with use_locale("en"):
        r = run(trip(), [steep(3, 1600.0, 0.25)], best(1600.0))
        r2 = run(trip(start="2026-11-14"), [row(5, 400.0)])
        vc = F.vam_check(trip(pack_kg=None), {"km": 10.0, "climb_m": 1000.0, "hours": 3.333}, _rates(), None, None, 100.0)
    assert r["label"] == "Estimate: Hard"
    eff = check(r, "effort")["text"]
    assert "；" not in eff and "。" not in eff and "; summit day about" in eff
    for x in (r, r2):
        for c in x["checks"] + x["readiness"]["checks"]:
            if c["id"] in ("effort", "summit_sim"):
                assert not CJK.search(c["text"]) and not CJK.search(c["label"]), c["text"]
        for s in x["suggestions"]:
            assert not CJK.search(s), s
        for s in x["src"][:2]:
            assert not CJK.search(s), s
    assert "steady rate" in vc["text"] and "only a bit tight" in vc["text"]
