"""
SP-581 (跟團 G5): multi-day — each day against the heaviest day you have walked, the descent with the
pack, and a next-day reminder after a heavy day (no fatigue model: f_day stays 1.0; SP-575 §3.3).

  * vs_biggest_pct = the day's EP (km + climb/100) ÷ your heaviest hiking day's EP − 1;
  * pack_descent_m = the day's metres down (with that day's pack);
  * a day over your heaviest: a reminder; a day with ≥ 1,000 m down with the pack or > 3 h above AeT
    following the group: the next day says go slower for the first hour (推估);
  * one-day plans: nothing added. zh-TW and en.
"""
from __future__ import annotations

import json
from pathlib import Path

from pytest import approx

from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.tests.test_baiyue_capacity import _synthetic_cap, _v1
from backend.tests.test_racepower_v2 import synthetic_track

ROOT = Path(__file__).resolve().parents[2]
HDAYS = [{"date": "2026-05-01", "name": "合歡山", "km": 8.0, "gain_m": 700, "ep": 15.0, "ep_per_h": 3.0, "solo": False},
         {"date": "2026-07-12", "name": "奇萊南華", "km": 14.0, "gain_m": 1200, "ep": 26.0, "ep_per_h": 3.1, "solo": False}]


def course():
    """16 km: 8 km at 15 % up (1200 m), 8 km at 15 % down."""
    return CO.build_course(synthetic_track({"len": 16000, "z": lambda x: 300 + (x if x < 8000 else 16000 - x) * 0.15}))


def plan(splits=(8.0,), hdays=HDAYS, cap=True, **opts):
    return PL.plan_hike(v1=_v1(), course=course(), hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": hdays}},
                        opts={"mode": "auto", "day_splits_km": list(splits), **opts}, validated={},
                        capacity=_synthetic_cap() if cap else None)


def test_each_day_against_the_heaviest_day_and_the_descent_with_the_pack():
    p = plan()
    d1, d2 = p["days"]
    for d in (d1, d2):
        ep = d["km"] + d["gain_m"] / 100
        assert d["ep"] == approx(ep) and d["vs_biggest_pct"] == approx((ep / 26.0 - 1) * 100)
        assert d["pack_descent_m"] == d["loss_m"]
    assert d1["over_biggest"] is False and d1["vs_biggest_pct"] < 0          # 8 km + 1200 m = EP 20 < 26
    assert d2["loss_m"] > 1000 and d2["recovery"] == []                     # the last day: no next day to flag
    assert p["summary"]["biggest_day"] == {"date": "2026-07-12", "name": "奇萊南華", "ep": 26.0, "km": 14.0, "gain_m": 1200}


def test_over_the_heaviest_day_and_after_a_big_descent_a_reminder():
    small = [dict(HDAYS[0])]                                                  # heaviest walked: EP 15
    p = plan(splits=(10.0,), hdays=small)                                    # day 1: 8 up + 2 down ≥ EP 20
    d1, d2 = p["days"]
    assert d1["over_biggest"] and d1["vs_biggest_pct"] > 0
    txt = " ".join(str(w.get("text") if isinstance(w, dict) else w) for w in p["warnings"])
    assert f"第 1 天比你走過最吃力的一天多 {d1['vs_biggest_pct']:.0f} %" in txt
    # a big descent on day 1 (two days down the same slope) flags day 2
    q = plan(splits=(12.0,), hdays=small)
    a, b = q["days"]
    assert a["pack_descent_m"] < 1000 and b["recovery"] == []
    r = plan(splits=(15.0,), hdays=small)
    a, b = r["days"]
    assert a["pack_descent_m"] >= 1000 and b["recovery"] == ["descent"]
    assert any("第 2 天腿可能還沒恢復" in str(w) for w in r["warnings"])


def test_a_long_day_above_aet_with_the_group_flags_the_next_day():
    days = [{"day": 1, "km": 8, "gain_m": 1200, "loss_m": 0}, {"day": 2, "km": 8, "gain_m": 0, "loss_m": 1200}]
    w: list = []
    PL.day_load(days, HDAYS, {"days": [{"day": 1, "long_hard": True}]}, w)
    assert days[1]["recovery"] == ["above_aet"] and any("AeT 以上超過 3 小時" in str(x) for x in w)


def test_one_day_plans_and_no_hiking_days():
    one = plan(splits=())
    assert "vs_biggest_pct" not in one["days"][0]
    none = plan(hdays=[])
    assert all(d["vs_biggest_pct"] is None and d["over_biggest"] is False for d in none["days"])
    assert none["summary"]["biggest_day"] is None
    v1 = plan(cap=False)                                                      # the old path has it too
    assert v1["summary"]["total_method"] == "v1" and v1["days"][0]["vs_biggest_pct"] is not None


def test_the_page_and_both_languages():
    html = (ROOT / "backend/static/racepower.html").read_text("utf-8")
    assert html.count("${dl ? dayLoadCells(d) : \"\"}") == 2 and "function dayLoadNote" in html
    for loc in ("zh-TW", "en"):
        c = json.loads((ROOT / f"backend/static/i18n/{loc}/racepower.json").read_text("utf-8"))
        assert all(c[f"dl.{k}"] for k in ("vs", "down", "tip", "rec", "rec_tip", "ref", "noref"))
    en = json.loads((ROOT / "backend/i18n/locales/en.json").read_text("utf-8"))
    assert en["第 {n} 天比你走過最吃力的一天多 {pct:.0f} %（EP {ep:.1f}；你最吃力的一天是 {date}，EP {big:.1f}）"].startswith("Day {n}")
