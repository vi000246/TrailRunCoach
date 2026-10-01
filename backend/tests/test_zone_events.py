"""engine/zone_events.py — event-driven zone updates (docs/research/
zones-and-thresholds.md §2.5): the cool-day HR-at-power shift (wrist-HR
safe steady points), ≥ 4 weeks off, the first cool spell of the season, and
the suggestion objects in the testing indicator. Suggestions never schedule
a test. Pure inputs only — no WKO5 folder, no real DB, no real weather."""
import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import zone_events as ZE
from backend.engine.planning import Plan, Threshold

TODAY = dt.date(2026, 11, 20)


# ---- steady points: wrist HR ------------------------------------------------------

def _windows(n, hr, p=200.0, v=3.0, t0=600.0, k0=0, g=0.0):
    """n 100-m windows at v m/s from t0 s; hr: a number or a list."""
    hrs = hr if isinstance(hr, list) else [hr] * n
    dur = 100.0 / v
    return [{"k": k0 + i, "t": t0 + i * dur, "v": v, "p": p, "hr": hrs[i], "hr_lag": None, "g": g, "run": 1.0}
            for i in range(n)]


def test_run_point_drops_the_first_two_minutes_after_a_change():
    # 30 windows × 33.3 s = 1000 s; the first 4 (133 s ≥ 120 s) still show the wrist catching up
    hr = [175.0, 170.0, 165.0, 160.0] + [150.0] * 26
    pt = ZE.run_point(_windows(30, hr))
    assert pt["hr"] == 150.0 and pt["p"] == pytest.approx(200.0)
    assert pt["seconds"] == pytest.approx(26 * 100 / 3.0)


def test_run_point_needs_ten_steady_minutes_after_the_settle():
    assert ZE.run_point(_windows(20, 150.0)) is None             # 667 s − 133 s < 600 s
    # a hill in the middle cuts the stretch in two short ones
    w = _windows(40, 150.0)
    for x in w[18:22]:
        x["g"] = 0.06
    assert ZE.run_point(w) is None
    # the first 10 minutes of the run never count
    assert ZE.run_point(_windows(30, 150.0, t0=0.0)) is None      # 1000 s from the start: 400 s after 10 min


def test_run_point_rejects_unsteady_power():
    w = _windows(40, 150.0)
    for i, x in enumerate(w):
        x["p"] = 150.0 if i % 2 else 250.0                          # CV 25 %
    assert ZE.run_point(w) is None


# ---- the cool-day HR shift ------------------------------------------------------------

def _pts(days_ago, offset=0.0, temp=20.0, start=None):
    start = start or TODAY
    out = []
    for i, d in enumerate(days_ago):
        p = 160.0 + (i * 7) % 30
        out.append({"date": (start - dt.timedelta(days=d)).isoformat(), "p": p,
                    "hr": 120.0 + 0.18 * p + offset + (0.8 if i % 2 else -0.8), "temp_c": temp})
    return out


BASE = list(range(300, 100, -20))          # 10 cool runs, 100–300 days ago


def test_hr_shift_fires_on_a_persistent_rise():
    pts = _pts(BASE) + _pts([50, 40, 30, 20, 10, 2], offset=7.0)
    r = ZE.hr_shift(pts, TODAY)
    assert r["fired"] and r["direction"] == "up" and r["shift_bpm"] == pytest.approx(7.0, abs=1.0)
    assert r["n"] == 6 and r["same_side"] == 6 and r["line"]["n"] == 10


def test_hr_shift_small_or_mixed_does_not_fire():
    assert not ZE.hr_shift(_pts(BASE) + _pts([50, 40, 30, 20, 10, 2], offset=3.0), TODAY)["fired"]
    mixed = _pts(BASE) + _pts([50, 40, 30], offset=9.0) + _pts([20, 10, 2], offset=-9.0)
    r = ZE.hr_shift(mixed, TODAY)
    assert not r["fired"] and r["reason"]


def test_hr_shift_one_wrist_outlier_is_allowed():
    rec = _pts([50, 40, 30, 20, 10, 2], offset=-8.0)
    rec[2]["hr"] += 12.0                                             # one optical glitch the other way
    r = ZE.hr_shift(_pts(BASE) + rec, TODAY)
    assert r["fired"] and r["direction"] == "down" and r["same_side"] == 5


def test_hr_shift_ignores_hot_days_and_needs_a_baseline():
    hot = _pts([50, 40, 30, 20, 10, 2], offset=12.0, temp=29.0)
    r = ZE.hr_shift(_pts(BASE) + hot, TODAY)
    assert not r["fired"] and "只有 0 次" in r["reason"]
    r = ZE.hr_shift(_pts([150, 140]) + _pts([50, 40, 30, 20, 10, 2], offset=9.0), TODAY)
    assert not r["fired"] and "基準線" in r["reason"]


def test_hr_shift_compares_only_at_the_same_power():
    far = _pts([50, 40, 30, 20, 10, 2], offset=9.0)
    for p in far:
        p["p"] += 80.0                                              # far outside the baseline's 160–189 W
    r = ZE.hr_shift(_pts(BASE) + far, TODAY)
    assert not r["fired"] and "功率範圍" in r["reason"]


# ---- the first cool spell -------------------------------------------------------------

def _days(start, n, temp):
    return [{"date": (start + dt.timedelta(days=i)).isoformat(), "temp_c": temp} for i in range(n)]


def test_cool_season_after_summer():
    s = dt.date(2026, 9, 1)
    acts = _days(s, 40, 28.0) + _days(s + dt.timedelta(days=40), 3, 22.0)
    cs = ZE.cool_season(acts, TODAY)
    assert cs["start"] == "2026-10-11" and cs["summer_days"] >= 10
    # a spell with one warm day inside is not 3 in a row
    acts = _days(s, 40, 28.0) + _days(s + dt.timedelta(days=40), 2, 22.0) + _days(s + dt.timedelta(days=42), 1, 26.0)
    assert ZE.cool_season(acts, TODAY) is None


def test_no_cool_season_without_a_summer():
    acts = _days(dt.date(2026, 1, 5), 30, 18.0)
    assert ZE.cool_season(acts, dt.date(2026, 2, 20)) is None


# ---- suggestions ------------------------------------------------------------------------

DS = SimpleNamespace(workouts=[])


def test_break_suggests_retests_after_the_block():
    brk = {"days": 35, "return": "2026-10-20", "end": "2026-11-24", "aet_stale": True}
    out = ZE.suggestions(DS, Plan(), TODAY, acts=[], brk=brk, points=[])
    s = next(x for x in out["suggestions"] if x["id"] == "break")
    assert s["tests"] == ["cp", "tt30", "aet"] and s["earliest"] == "2026-11-24"
    assert s["kind"] == "test_suggestion" and s["estimate"] is True and "腕" in s["caveat"]
    assert not any("胸帶" in c for c in s["conditions"])             # the athlete has no chest strap
    # a CP test after the return covers cp; an applied LTHR estimate is not a test
    plan = Plan(thresholds=[Threshold("2026-11-10", cp=200, lthr=150, lthr_method="estimate")])
    s = next(x for x in ZE.suggestions(DS, plan, TODAY, acts=[], brk=brk, points=[])["suggestions"]
             if x["id"] == "break")
    assert s["tests"] == ["tt30", "aet"]
    # a 3-week break is not ≥ 4 weeks
    assert not ZE.suggestions(DS, Plan(), TODAY, acts=[], brk={**brk, "days": 21}, points=[])["suggestions"]


def test_hr_shift_suggestion_and_tests_done_since():
    pts = _pts(BASE) + _pts([50, 40, 30, 20, 10, 2], offset=7.0)
    out = ZE.suggestions(DS, Plan(), TODAY, acts=[], brk={}, points=pts)
    s = next(x for x in out["suggestions"] if x["id"] == "hr_shift")
    assert s["tests"] == ["tt30", "aet"] and s["earliest"] is None and "升高" in s["title"]
    assert out["checks"]["hr_shift"]["fired"]
    done = Plan(thresholds=[Threshold("2026-11-01", lthr=160, lthr_method="friel30", aethr=145, aethr_method="test")])
    assert not ZE.suggestions(DS, done, TODAY, acts=[], brk={}, points=pts)["suggestions"]


def test_cool_season_suggestion_from_road_runs_only():
    s = dt.date(2026, 9, 1)
    days = _days(s, 40, 28.0) + _days(s + dt.timedelta(days=40), 3, 22.0)
    ws, acts = [], []
    for i, a in enumerate(days):
        d = dt.date.fromisoformat(a["date"])
        ws.append(SimpleNamespace(idx=i, sport="run", tags=[], day=0,
                                  entry=SimpleNamespace(file=f"{i}.fit", start=dt.datetime(d.year, d.month, d.day, 6))))
        acts.append({**a, "file": f"{i}.fit"})
    ds = SimpleNamespace(workouts=ws)
    out = ZE.suggestions(ds, Plan(), TODAY, acts=acts, brk={}, points=[])
    s = next(x for x in out["suggestions"] if x["id"] == "cool_season")
    assert s["tests"] == ["tt30", "aet"] and s["detected"] == "2026-10-11"
    assert all("< 25 °C" in c for c in s["conditions"])
    # the same days on trail runs (a mountain is cool in August) do not count
    for w in ws:
        w.tags = ["runningtrail"]
    assert not ZE.suggestions(ds, Plan(), TODAY, acts=acts, brk={}, points=[])["suggestions"]
    # older than 60 days: no longer the first cool spell
    assert not any(x["id"] == "cool_season" for x in
                   ZE.suggestions(SimpleNamespace(workouts=[]), Plan(), dt.date(2027, 1, 30), acts=acts, brk={},
                                  points=[])["suggestions"])


def test_applied_test_is_a_zone_update_event():
    plan = Plan(thresholds=[Threshold("2026-11-15", cp=210, cp_method="tt20")])
    ev = ZE.applied_events(plan, TODAY)
    assert ev and ev[0]["field"] == "cp" and "區間已重算" in ev[0]["text"]
    assert not ZE.applied_events(plan, TODAY + dt.timedelta(days=30))


# ---- the testing indicator ---------------------------------------------------------------

def test_testing_indicator_carries_suggestions_but_never_cp_due(monkeypatch):
    from backend.engine import status as S
    sug = {"suggestions": [ZE._suggestion("cool_season", ["tt30", "aet"], "天氣轉涼了", "x", TODAY)],
           "events": [], "checks": {}}
    monkeypatch.setattr(ZE, "suggestions", lambda *a, **k: sug)
    st = S.Status.__new__(S.Status)
    st.ds = SimpleNamespace(workouts=[], plan=None)
    st.plan = Plan(thresholds=[Threshold(TODAY.isoformat(), cp=204, lthr=155,
                                         note="LTHR 自動估算（7 次跑步）；CP 204 W")])
    st.today, st.goals, st.indicators, st.cp_protocol = TODAY, {"days_to_next_a": None}, [], "quick"
    ind = st.i_testing()
    assert ind.extra["test_suggestions"] == sug["suggestions"] and ind.extra["cp_due"] is False
    assert ind.level == S.WATCH and "不會自動排課" in ind.action
    assert "LTHR 自動估算（已套用" in ind.why and "不是測試" in ind.why
