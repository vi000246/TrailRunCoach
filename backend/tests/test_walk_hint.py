"""SP-298: 「照你輕鬆心率的爬升速度（約 N m/h），坡度超過約 X% 用走的比較省」 on hill easy / long runs
(engine/walk_hint.py, runwalk.walk_grade; docs/research/run-walk-threshold.md §3.2, §5.4 #2)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import walk_hint as WH
from backend.engine.racepower import runwalk as RW

TODAY = dt.date(2026, 10, 6)


# ---- runwalk.walk_grade: the §3.2 table (PTS column, default curve) ------------------------

@pytest.mark.parametrize("vam,pct", [(400, 6.0), (500, 8.0), (700, 11.0), (900, 15.0), (1000, 17.5), (1400, 28.0)])
def test_walk_grade_reproduces_the_research_table(vam, pct):
    assert RW.walk_grade(vam) * 100 == pytest.approx(pct, abs=0.6)


def test_walk_grade_edges():
    assert RW.walk_grade(None) is None and RW.walk_grade(0) is None and RW.walk_grade(-5) is None
    assert RW.walk_grade(50) == pytest.approx(RW.MIN_GRADE)        # a crawl: walk from the first climb grade
    assert RW.walk_grade(5000) is None                              # still run at 100 %
    # a faster personal transition (shift > 0) walks earlier
    assert RW.walk_grade(700, 0.2) < RW.walk_grade(700) < RW.walk_grade(700, -0.2)
    g = RW.walk_grade(700)
    assert RW.gait(g, 700 / 3600 / g) == "walk" and RW.gait(g - 0.002, 700 / 3600 / (g - 0.002)) != "walk"


# ---- which sessions -------------------------------------------------------------------------

def test_only_hill_easy_and_long_runs():
    assert WH.is_hill_session({"kind": "easy", "title": "輕鬆越野跑", "terrain": "trail"})
    assert WH.is_hill_session({"kind": "long", "title": "LSD（山路）", "terrain": None})
    assert WH.is_hill_session({"kind": "long", "title": "LSD（山路越野）", "terrain": "trail"})
    assert not WH.is_hill_session({"kind": "long", "title": "LSD", "terrain": None})              # 平路或緩坡
    assert not WH.is_hill_session({"kind": "long", "title": "LSD（路跑）", "terrain": "road"})
    assert not WH.is_hill_session({"kind": "easy", "title": "輕鬆跑（路跑）", "terrain": "road"})
    assert not WH.is_hill_session({"kind": "easy", "title": "輕鬆跑", "terrain": None})
    assert not WH.is_hill_session({"kind": "quality", "title": "有氧間歇 2×15 分（坡道）", "terrain": "trail"})
    h = {"vam": 600.0, "grade": 10.0, "personal": False, "n": 5}
    assert WH.for_session({"kind": "easy", "title": "輕鬆越野跑", "terrain": "trail", "state": "active"}, h)
    assert WH.for_session({"kind": "easy", "title": "輕鬆越野跑", "terrain": "trail", "state": "done"}, h) is None
    assert WH.for_session({"kind": "easy", "title": "輕鬆跑", "state": "active"}, h) is None       # flat: never
    assert WH.for_session({"kind": "easy", "title": "輕鬆越野跑", "terrain": "trail", "state": "active"}, None) is None


# ---- the climbing rate at easy HR ---------------------------------------------------------

def test_easy_vam_uses_only_climbs_at_or_under_the_cap():
    cl = [{"vam": 600, "hr": 140}, {"vam": 650, "hr": 145}, {"vam": 700, "hr": 150}, {"vam": 900, "hr": 165}]
    v = WH.easy_vam(cl, 150)
    assert v == {"vam": 650.0, "n": 3}
    assert WH.easy_vam(cl, 145) is None                     # 2 climbs < MIN_CLIMBS
    assert WH.easy_vam(cl, None) is None and WH.easy_vam([], 150) is None


def test_recent_climbs_reads_the_trail_runs_of_the_window(monkeypatch):
    from backend.engine.panels import climb_vam as PCV
    from backend.engine.wko5expr.dataset import date_to_day

    def w(days_ago, trail=True):
        d = TODAY - dt.timedelta(days=days_ago)
        return SimpleNamespace(day=date_to_day(d) + 0.3, trail=trail,
                               entry=SimpleNamespace(start=dt.datetime.combine(d, dt.time(7))))
    ws = [w(5), w(40), w(120), w(10, trail=False)]
    monkeypatch.setattr(PCV, "is_trail_run", lambda x: x.trail)
    monkeypatch.setattr(PCV, "_cached", lambda ds, x: {"segments": [{"vam": 600.0, "avg_hr": 140.0}]})
    got = WH.recent_climbs(SimpleNamespace(workouts=ws), TODAY)
    assert [c["date"] for c in got] == [(TODAY - dt.timedelta(days=5)).isoformat(),
                                        (TODAY - dt.timedelta(days=40)).isoformat()]
    assert got[0]["vam"] == 600.0 and got[0]["hr"] == 140.0


# ---- the hint: with VAM, without VAM, with a personal curve ------------------------------------

def test_no_climbs_no_number(monkeypatch):
    monkeypatch.setattr(WH, "recent_climbs", lambda ds, today: [])
    monkeypatch.setattr(WH, "personal_shift", lambda ds, today: pytest.fail("not needed"))
    assert WH.compute(object(), TODAY, 150) is None
    assert WH.hint(None) is None and WH.text(None) is None


def test_with_vam_the_default_curve(monkeypatch):
    monkeypatch.setattr(WH, "recent_climbs", lambda ds, today: [{"vam": v, "hr": 140} for v in (690, 700, 712)])
    monkeypatch.setattr(WH, "personal_shift", lambda ds, today: {"shift": 0.0, "personal": False, "n": 0})
    h = WH.compute(object(), TODAY, 150)
    assert h == {"vam": 700.0, "grade": 11.0, "personal": False, "n": 3}
    t = WH.text(h)
    assert t == "提示：照你輕鬆心率的爬升速度（約 700 m/h），坡度超過約 11% 用走的比較省（預設值）；規則仍是心率上限"


def test_with_a_personal_curve(monkeypatch):
    monkeypatch.setattr(WH, "recent_climbs", lambda ds, today: [{"vam": 700, "hr": 140}] * 4)
    monkeypatch.setattr(WH, "personal_shift", lambda ds, today: {"shift": 0.2, "personal": True, "n": 80})
    h = WH.compute(object(), TODAY, 150)
    assert h["personal"] and h["grade"] == round(RW.walk_grade(700, 0.2) * 100) and h["grade"] < 11
    assert "依你的跑走紀錄校正" in WH.text(h) and "預設值" not in WH.text(h)
    # a shift that isn't personal (no bin qualified) is the default curve
    assert WH.hint({"vam": 700.0, "n": 4}, {"shift": 0.2, "personal": False})["grade"] == 11.0


def test_text_in_english():
    from backend.i18n import use_locale
    with use_locale("en"):
        t = WH.text({"vam": 700.0, "grade": 11.0, "personal": False})
    assert t.startswith("Tip: at your easy-HR climbing rate (about 700 m/h)") and "11%" in t and "default" in t


def test_the_plan_view_adds_it_to_hill_sessions_only(monkeypatch):
    from backend.api import plan_sessions as API
    h = {"vam": 600.0, "grade": 10.0, "personal": False, "n": 5}
    prov = SimpleNamespace(status_of=lambda *a: {"status": "not_pushed"}, row_view=lambda r: {})
    inp = {"thresholds": {}}
    hill = {"uid": "a", "week_start": "2026-10-05", "minutes": 50, "day": "2026-10-08", "kind": "easy", "title": "輕鬆越野跑", "terrain": "trail", "state": "active", "detail": "x"}
    flat = {"uid": "b", "week_start": "2026-10-05", "minutes": 50, "day": "2026-10-08", "kind": "easy", "title": "輕鬆跑", "terrain": None, "state": "active", "detail": "y"}
    v = API._view(hill, inp, {}, "2026-10-06", prov, h)
    assert v["walk_hint"].startswith("提示：照你輕鬆心率的爬升速度（約 600 m/h），坡度超過約 10%")
    assert v["detail"] == "x"                                   # the stored text is not touched
    assert "walk_hint" not in API._view(flat, inp, {}, "2026-10-06", prov, h)
    assert "walk_hint" not in API._view(hill, inp, {}, "2026-10-06", prov, None)
