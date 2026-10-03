"""
engine/steep_hill.py: 陡坡健走（模擬負重）, which replaced the loaded-carry sessions —
the Pandolf grade, when it is planned, and the session it makes. Synthetic only.
"""
import datetime as dt

import pytest

from backend.engine import steep_hill as SH
from backend.engine.racepower.hike import pandolf

MON = dt.date(2026, 10, 5)


def _ev(weeks: int, **kw):
    return {"id": "e1", "name": "嘉明湖", "start": (MON + dt.timedelta(weeks=weeks, days=4)).isoformat(),
            "days": 3, "kind": "baiyue", "pack_kg": None, **kw}


def test_simulated_grade_costs_what_the_pack_would():
    for pct in (0.05, 0.10, 0.13):
        s = SH.simulated(pct)
        assert SH.BASE_GRADE < s["grade"] <= SH.TREADMILL_MAX and s["kmh"] == SH.BASE_KMH
        v = SH.BASE_KMH / 3.6
        # within the 0.5 % rounding of the grade
        assert pandolf(68, 0, v, s["grade"] - 0.5) <= pandolf(68, 68 * pct, v, SH.BASE_GRADE) <= pandolf(68, 0, v, s["grade"] + 0.5)
    assert SH.simulated(0.05)["grade"] < SH.simulated(0.10)["grade"] < SH.simulated(0.13)["grade"]
    big = SH.simulated(0.40)                 # above the treadmill limit: the speed rises instead
    assert big["grade"] == SH.TREADMILL_MAX and big["kmh"] > SH.BASE_KMH


def test_when_it_is_planned():
    on = SH.week_context(kind="specific", mode="build", monday=MON, event=_ev(5), weight=68.0)
    assert on["active"] and on["step"] == 3 and on["kg"] == 9.0 and on["sim"]["grade"] > SH.BASE_GRADE
    assert SH.week_context(kind="specific", mode="build", monday=MON, event=_ev(9), weight=68.0)["step"] == 1
    assert SH.week_context(kind="specific", mode="build", monday=MON, event=_ev(7), weight=68.0)["step"] == 2
    for kw in ({"kind": "base"}, {"mode": "recovery_week"}, {"tsb": -25.0}):
        a = {"kind": "specific", "mode": "build", "monday": MON, "event": _ev(5), "weight": 68.0, **kw}
        assert not SH.week_context(**a)["active"]
    assert not SH.week_context(kind="specific", mode="build", monday=MON, event=_ev(5, kind="race", days=1),
                               weight=68.0)["active"]
    assert not SH.week_context(kind="specific", mode="build", monday=MON, event=_ev(12), weight=68.0)["active"]


def _week():
    d = lambda i: (MON + dt.timedelta(days=i)).isoformat()
    return [{"id": "long", "kind": "long", "day": d(5), "minutes": 150, "tss": 120.0, "title": "LSD"},
            {"id": "quality", "kind": "quality", "day": d(1), "minutes": 55, "tss": 60.0, "title": "閾值"},
            {"id": "easy1", "kind": "easy", "day": d(2), "minutes": 40, "tss": 30.0, "title": "輕鬆跑"},
            {"id": "easy2", "kind": "easy", "day": d(3), "minutes": 45, "tss": 33.0, "title": "輕鬆跑"},
            {"id": "strength1", "kind": "strength", "day": d(3), "minutes": 35, "tss": 20.0, "title": "肌力"}]


def test_apply_turns_one_weekday_easy_run_into_the_steep_walk():
    info = SH.week_context(kind="specific", mode="build", monday=MON, event=_ev(5), weight=68.0)
    ss = _week()
    before = sum(s["minutes"] for s in ss if s["kind"] == "easy")
    SH.apply(ss, info, aet=148.0)
    st = [s for s in ss if s["id"] == "steep"]
    assert len(st) == 1
    s = st[0]
    assert s["day"] == (MON + dt.timedelta(days=3)).isoformat()      # not the day after the quality
    assert s["kind"] == "easy" and s["terrain"] == "trail" and s["target"] == "心率 ≤ 輕鬆跑上限 148 bpm"
    assert f"{info['sim']['grade']:g}%" in s["title"] and "9 kg" in s["title"] and "不背包" in s["detail"]
    assert SH.MINUTES[0] <= s["minutes"] <= SH.MINUTES[1]
    assert sum(x["minutes"] for x in ss if x["kind"] == "easy") == before          # the week doesn't grow
    assert not any("背" in x["title"] and "kg" in x["title"] and x["id"] != "steep" for x in ss)
    assert info["planned"][0]["grade"] == info["sim"]["grade"]
    # nothing in the last 7 days before the trip
    late = SH.week_context(kind="specific", mode="build", monday=MON, event={**_ev(0), "start": (MON + dt.timedelta(days=6)).isoformat()},
                           weight=68.0)
    assert not late["active"] or not any(x["id"] == "steep" for x in SH.apply(_week(), late, aet=148.0))


def test_activity_pack_moved_to_athlete():
    # the per-activity pack (百岳 prediction) survives the loaded-carry removal
    from types import SimpleNamespace
    from backend.engine.racepower import athlete as A
    w = SimpleNamespace(entry=SimpleNamespace(file="fake/0.wko4"))
    assert A.activity_pack(w, {})["pack_kg"] is None
    A.set_hike_meta("fake/0.wko4", 6.5)
    p = A.activity_pack(w)
    assert p["pack_kg"] == 6.5 and p["recorded"] and p["range"] == [0, 40]


def test_public_and_projection_glue():
    info = SH.week_context(kind="specific", mode="build", monday=MON, event=_ev(5), weight=68.0)
    p = SH.public(info)
    assert p["active"] and p["sim"] == info["sim"]
    nxt = SH.projected_context("specific", "build", MON + dt.timedelta(weeks=1), p)
    assert nxt["active"] and nxt["weeks_out"] == info["weeks_out"] - 1
    assert SH.next_state(info) == {}
