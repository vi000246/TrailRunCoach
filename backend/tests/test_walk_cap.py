"""
登山爬坡的心率上限 (SP-115): walking sessions (陡坡健走, 登山 / 健行 / 百岳) climb at
≤ 75 % HRmax or RPE ≤ 13 (萩原・山本 2011); without a max HR 0.75 × (220 − age)
(長野縣 Safety Book, 推估); never below the easy-run cap. Trail runs stay at the easy
cap. The texts, the COROS push and the structure editor follow. Synthetic only.
"""
import datetime as dt

from backend.engine import hr_profile as HP
from backend.engine import steep_hill as SH
from backend.engine import target_policy as TP
from backend.engine import workout_steps as WS
from backend.engine.planning import Plan, Threshold
from backend.sync import coros_workouts as CW
from backend.tests.wko5_fakes import FakeDataset

TODAY = dt.date(2026, 10, 5)
STEEP = {"id": "steep", "kind": "easy", "terrain": "trail", "title": "陡坡健走 14%（模擬負重 9 kg）",
         "minutes": 45, "day": "2026-10-08"}
TRAIL = {"kind": "easy", "terrain": "trail", "title": "輕鬆跑（山路）", "minutes": 45, "day": "2026-10-08"}
TECH = {"kind": "hike", "terrain": "trail", "title": "技術地形 40′（RPE 3–4）", "minutes": 45, "day": "2026-10-08"}


def test_cap_with_a_max_hr():
    w = HP.walk_cap(195.0, aet=145.0, mhr_source="你的設定 2026-09-01")
    assert w["value"] == 146 and w["basis"] == "hrmax" and not w["estimate"] and not w["floor"]
    assert w["rpe"] == 13 and w["mhr"] == 195
    assert HP.walk_cap_label(w) == "爬坡上限 146 bpm（75% 最大心率）"
    t = HP.walk_cap_hr(w)
    assert t.startswith("心率 ≤ 爬坡上限 146 bpm") and "RPE ≤ 13" in t and "先到的為準" in t
    # an estimated max HR (近 365 天跑步) says so
    assert "推估" in HP.walk_cap_label(HP.walk_cap(195.0, 145.0, mhr_estimate=True))


def test_no_max_hr_uses_the_age_and_says_estimate():
    w = HP.walk_cap(None, aet=130.0, age=40)
    assert (w["value"], w["basis"], w["estimate"], w["floor"]) == (135, "age", True, False)
    assert "220 − 年齡" in HP.walk_cap_label(w) and "推估" in HP.walk_cap_label(w)
    assert HP.walk_cap(None, aet=130.0) is None                             # no max HR, no age
    # then the text keeps the easy-run cap, still with the RPE
    assert HP.walk_cap_hr(None, 148.0) == "心率 ≤ 輕鬆跑上限 148 bpm 或 RPE ≤ 13，先到的為準"


def test_below_the_easy_cap_takes_the_easy_cap():
    w = HP.walk_cap(180.0, aet=150.0)                  # 75 % × 180 = 135 < 150
    assert (w["value"], w["floor"]) == (150, True)
    assert "低於輕鬆跑上限" in HP.walk_cap_label(w)
    w = HP.walk_cap(None, aet=140.0, age=40)           # 135 < 140
    assert (w["value"], w["floor"]) == (140, True)


def test_walk_cap_for_reads_the_plan_and_the_age(monkeypatch):
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    ds = FakeDataset([], TODAY, settings={"runthr": 165.0})
    ds.plan = Plan(thresholds=[Threshold("2026-09-01", mhr=196)], profile={"birth_year": 1986})
    w = HP.walk_cap_for(ds, TODAY, 145.0)
    assert (w["value"], w["basis"]) == (147, "hrmax") and "你的設定" in w["source"]
    ds.plan = Plan(profile={"birth_year": 1986})                            # 40 → 0.75 × 180
    w = HP.walk_cap_for(ds, TODAY, 130.0)
    assert (w["value"], w["basis"], w["estimate"]) == (135, "age", True)
    ds.plan = Plan()
    assert HP.walk_cap_for(ds, TODAY, 130.0) is None


def test_policy_walking_sessions_vs_trail_runs():
    assert TP.target_policy(STEEP)["type"] == "walk" and TP.target_policy(STEEP)["basis"] == "hr"
    assert "萩原" in TP.target_policy(STEEP)["source"]
    for title in ("登山 雪山主峰", "健行", "百岳 嘉明湖"):
        assert TP.is_walk({"kind": "long", "title": title})
    # trail runs stay at the easy cap: 越野跑 (kind hike), trail easy / long, B2B, quality uphill
    assert TP.target_policy(TECH)["type"] == "hike"
    assert TP.target_policy(TRAIL)["type"] == "trail_easy"
    assert TP.target_policy({"kind": "long", "title": "B2B 第 1 天｜LSD（山路）"})["type"] == "trail_long"
    assert not TP.is_walk({"kind": "quality", "title": "登山王 上坡 5×4 分"})


TH = {"cp": 250.0, "lthr": 165.0, "aet": 145.0, "walk_cap": HP.walk_cap(196.0, 145.0)}


def test_push_follows_the_walk_cap():
    th = CW.Thresholds.of(TH)
    st = CW.session_steps({**STEEP, "basis": "hr"}, th)
    assert st[0].intensity == ("hr", 124, 147) and "RPE ≤ 13" in st[0].name and "下坡" in st[0].name
    # a trail run keeps the easy-run cap
    for s in (TRAIL, TECH):
        assert CW.session_steps({**s, "basis": "hr"}, th)[0].intensity == ("hr", 124, 145)
    # no walk cap in the thresholds: the easy cap as before
    old = CW.Thresholds.of({k: v for k, v in TH.items() if k != "walk_cap"})
    assert CW.session_steps({**STEEP, "basis": "hr"}, old)[0].intensity == ("hr", 124, 145)
    # the floor: 75 % HRmax under the easy cap → the easy cap
    low = CW.Thresholds.of({**TH, "walk_cap": HP.walk_cap(180.0, 145.0)})
    assert CW.session_steps({**STEEP, "basis": "hr"}, low)[0].intensity == ("hr", 124, 145)


def test_editor_steps_follow_the_walk_cap():
    d = WS.derive(STEEP, TH)
    c = WS.session_ctx(STEEP, TH)
    assert c.walk and WS.easy_hr(c) == ("hr", 124, 147)
    got = WS.steps_to_coros(WS.normalize(d), c)
    assert got[0].intensity == ("hr", 124, 147) and "RPE ≤ 13" in got[0].name
    assert WS.zones_table(c)["hr"][0]["label"] == "≤ 爬坡上限"
    # the 陡坡健走 templates' 「≤ 輕鬆跑上限」 zone resolves to the walk cap in a walking session
    st = {"kind": "work", "dur": 600, "target": {"type": "hr", "mode": "zone", "zone": "aet"}}
    r = WS.resolve(st, c)
    assert (r.type, r.lo, r.hi) == ("hr", 124, 147)
    assert WS.session_ctx(TRAIL, TH).walk is None and WS.easy_hr(WS.session_ctx(TRAIL, TH)) == ("hr", 124, 145)


def test_steep_walk_session_texts():
    ev = {"id": "e1", "name": "嘉明湖", "start": (TODAY + dt.timedelta(weeks=5, days=4)).isoformat(),
          "days": 3, "kind": "baiyue", "pack_kg": None}
    info = SH.week_context(kind="specific", mode="build", monday=TODAY, event=ev, weight=68.0)
    d = lambda i: (TODAY + dt.timedelta(days=i)).isoformat()
    ss = [{"id": "long", "kind": "long", "day": d(5), "minutes": 150, "tss": 120.0, "title": "LSD"},
          {"id": "easy1", "kind": "easy", "day": d(2), "minutes": 45, "tss": 33.0, "title": "輕鬆跑"}]
    SH.apply(ss, info, aet=145.0, walk=HP.walk_cap(196.0, 145.0))
    s = next(x for x in ss if x["id"] == "steep")
    assert s["target"].startswith("心率 ≤ 爬坡上限 147 bpm（75% 最大心率）") and "RPE ≤ 13" in s["target"]
    assert "下坡看腿的感覺，不看心率" in s["detail"] and "比一般輕鬆跑的上限高" in s["detail"]
    assert "萩原・山本 2011" in s["detail"] and "長野縣 Safety Book" in s["source"]
    assert TP.target_policy(s)["type"] == "walk"


def test_week_plan_and_projection_carry_the_walk_cap(monkeypatch):
    """A 百岳 in the 專項期: the plan thresholds carry the cap (the push reads them) and the
    陡坡健走 of this week and of the projected weeks use it."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as PJ
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY as T0
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    ds = _history(T0)
    plan = _plan_with("2026-12-05", 4, T0)
    plan.thresholds.append(Threshold("2026-08-01", mhr=196))
    ds.plan = plan
    st = Status(ds, plan, T0, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, T0)
    w = wp["thresholds"]["walk_cap"]
    assert w["basis"] == "hrmax" and w["value"] == max(147, round(wp["thresholds"]["aet"]))
    weeks = PJ.project_weeks(wp, _phases(plan, T0), dt.date(2026, 11, 22))
    steep = [s for x in [wp] + weeks for s in x["sessions"] if s.get("id") == "steep"]
    assert steep
    for s in steep:
        assert s["target"].startswith(f"心率 ≤ 爬坡上限 {w['value']} bpm") and "RPE ≤ 13" in s["target"]
        assert "下坡看腿的感覺，不看心率" in s["detail"]
