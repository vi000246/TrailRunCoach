"""
SP-120 平衡／腳踝, as reworked by the owner 2026-10-05 (engine/balance_plan.py): part of strength —
a 12-minute block at the end of the week's strength session(s), one fixed set of moves (no stages,
no week counter), only before a 越野賽 / 百岳 A race, in the 轉換期 / 回量期 / 基礎期 / 專項期 / 減量期;
with the strength gone (SP-86 stop, race week) one balance-only strength session on an easy day,
before the race. No kind of its own. Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import balance_plan as BP
from backend.engine import projection as PJ
from backend.sync import coros_workouts as CW
from backend.tests.test_b2b import _phases
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30
from backend.tests.test_strength_plan import _ev, _week

TAG = "＋平衡／腳踝"


def _strength(ss):
    return [s for s in ss if s["kind"] == "strength"]


def test_week_context_only_a_trail_or_baiyue_a_race_and_these_phases():
    mon = date(2026, 10, 5)
    for k in ("transition", "rebuild", "base", "specific", "taper"):
        assert BP.week_context([_ev("2027-01-16")], mon, k)["active"]
    assert not BP.week_context([_ev("2027-01-16")], mon, "recovery")["active"]
    assert not BP.week_context([_ev("2027-01-16")], mon, "event")["active"]
    assert BP.week_context([_ev("2027-01-16", "baiyue")], mon, "base")["active"]
    assert not BP.week_context([_ev("2027-01-16", "road")], mon, "base")["active"]
    assert not BP.week_context([_ev("2027-01-16", "other")], mon, "base")["active"]
    assert not BP.week_context([], mon, "base")["active"]
    # the pack line: a 百岳's 專項期 / 減量期 only
    assert BP.week_context([_ev("2027-01-16", "baiyue")], mon, "specific")["pack"]
    assert not BP.week_context([_ev("2027-01-16", "baiyue")], mon, "base")["pack"]
    assert not BP.week_context([_ev("2027-01-16")], mon, "specific")["pack"]


def test_one_fixed_block_warm_up_sources_no_stages():
    ctx = BP.week_context([_ev("2027-01-16")], date(2026, 10, 5), "base")
    t = BP.block(ctx)
    assert t.startswith("平衡／腳踝 12 分：熱身 1–2 分承重式腳踝活動度") and "內翻、外翻（徐國峰）" in t
    assert "2 組 × 20–40 秒／腳" in t and "單腳站（張眼 → 閉眼）" in t and "小跳" in t and "提踵" in t
    assert "階段" not in t and "賽前 7 天" not in t and "背包" not in t
    assert "單腳站和伸腳點地可以背 5–10% 體重的背包" in BP.block({**ctx, "pack": True})
    s = BP.session(ctx, "2026-10-08")
    assert s["kind"] == "strength" and s["id"] == "balance" and s["minutes"] == 12 and s["tss"] == 0.0
    for src in ("Schiftan 2015", "Hupperets 2009", "Lesinski 2015", "徐國峰", "推估"):
        assert src in s["source"]
    assert not hasattr(BP, "first_day") and not hasattr(BP, "KIND")


def test_attach_adds_the_block_and_12_minutes_to_each_strength_session():
    ctx = {"active": True, "race_start": "2026-10-17"}
    ss = [{"id": "strength1", "kind": "strength", "title": "肌力（基礎循環 6 站）", "minutes": 35, "tss": 20.0,
           "detail": "6 站", "source": "Bompa", "day": "2026-10-06", "done": False},
          {"id": "strength2", "kind": "strength", "title": "肌力", "minutes": 35, "tss": 20.0, "detail": "",
           "source": "", "day": "2026-10-17", "done": False},                            # the race day
          {"id": "easy1", "kind": "easy", "title": "輕鬆跑", "minutes": 40, "day": "2026-10-07"}]
    assert BP.attach(ss, ctx) == 1
    s = ss[0]
    assert s["title"] == "肌力（基礎循環 6 站）" + TAG and s["minutes"] == 47 and s["tss"] == 20.0
    assert s["detail"].startswith("6 站；平衡／腳踝 12 分") and "Schiftan 2015" in s["source"]
    assert ss[1]["minutes"] == 35 and TAG not in ss[1]["title"]                         # not on / after the race
    assert BP.attach(ss, ctx) == 1 and ss[0]["minutes"] == 47                           # once
    assert BP.ensure(ss, ctx, [date(2026, 10, 5) + dt.timedelta(days=i) for i in range(7)]) is None


def test_ensure_adds_a_balance_only_strength_session_on_an_easy_day_before_the_race():
    ctx = {"active": True, "race_start": "2026-10-10"}
    days = [date(2026, 10, 5) + dt.timedelta(days=i) for i in range(7)]
    ss = [{"id": "quality", "kind": "quality", "day": "2026-10-06"}, {"id": "easy1", "kind": "easy", "day": "2026-10-08"}]
    s = BP.ensure(ss, ctx, days)
    assert s is ss[-1] and s["day"] == "2026-10-08" and s["kind"] == "strength" and "平衡照做" in s["detail"]
    # no easy day: a free day; 可練日; never on / after the race
    ss = [{"id": "quality", "kind": "quality", "day": "2026-10-06"}]
    s = BP.ensure(ss, ctx, days, allowed=lambda d: d.weekday() != 0)
    assert s["day"] == "2026-10-07"
    assert BP.ensure([], ctx, [date(2026, 10, 10), date(2026, 10, 11)]) is None


def test_week_plan_trail_race_strength_carries_it_road_none():
    _plan, wp = _week([_ev("2027-01-02")])
    st = _strength(wp["sessions"])
    assert st and all(s["title"].endswith(TAG) and "平衡／腳踝 12 分" in s["detail"] for s in st)
    assert not any(s["kind"] == "balance" for s in wp["sessions"]) and "balance" not in wp
    _plan, wr = _week([_ev("2027-01-02", "road")])
    assert _strength(wr["sessions"]) and not any(TAG in s["title"] for s in wr["sessions"])
    # the week's run minutes don't move (strength is not run time)
    run = lambda w: sum(s["minutes"] for s in w["sessions"] if s["kind"] != "strength")
    assert run(wp) == run(wr)


def test_taper_and_race_week_keep_balance_when_strength_stops():
    # trail A race Sat 10/17: 減量期 from 10/03 — SP-86 stops strength; balance stays (one a week)
    plan, wp = _week([_ev("2026-10-17")])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18))
    by = {w["start"]: w for w in weeks}
    for mon in ("2026-10-05", "2026-10-12"):
        w = by[mon]
        assert w["phase"] == "taper"
        st = _strength(w["sessions"])
        assert [s["id"] for s in st] == ["balance"] and st[0]["day"] < "2026-10-17" and st[0]["minutes"] == 12


def test_projection_specific_baiyue_gets_the_pack_line():
    plan, wp = _week([_ev("2027-01-02", "baiyue", name="嘉明湖")])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 22))
    spec = [w for w in weeks if w["phase"] == "specific"]
    pack = "單腳站和伸腳點地可以背"
    assert spec and all(any(pack in s["detail"] for s in _strength(w["sessions"])) for w in spec)
    base = [w for w in weeks if w["phase"] == "base"]
    assert base and all(not any(pack in s["detail"] for s in _strength(w["sessions"])) for w in base)


def test_never_pushed():
    s = BP.session({"active": True}, "2026-10-01")
    with pytest.raises(CW.Unsupported):
        CW.session_steps(s, CW.Thresholds(cp=250.0, lthr=165.0, aet=140.0))
