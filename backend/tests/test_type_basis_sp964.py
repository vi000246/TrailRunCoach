"""SP-964: 設定 › 訓練依據 › 進階設定 — 目標依據 per session type (plan.prefs.type_basis).

Order: the session's own → its type's → the overall 目標依據 → 自動 (engine/target_policy.py). Only road easy / long,
the flat Zone 3 (節奏) / Zone 5 intervals, hill repeats and the road long run's marathon-pace segment can be set;
the trail days, long climbs, walking, downhill and the tests keep their own rules. The same answer in the week
plan's texts, the structure, the COROS push and the 現在用什麼練 card. Synthetic thresholds only."""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from backend.engine import interval_library as IL
from backend.engine import plan_prefs as PP
from backend.engine import power_state as PST
from backend.engine import target_policy as TP
from backend.engine import training_basis as TB
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW

D = dt.date(2026, 10, 27)
TPACE = 400.0


def _st(source, last_test=None):
    return PST.state(D, None, source=source, how="設定", last_cp_test=last_test)


ONE = _st("stryd", "2026-10-20")
TWO = _st("stryd", "2026-09-07")
THREE = _st("none")


def th_of(ps, tpace=TPACE):
    out = {"lthr": 165.0, "aet": 140.0, "power_state": ps, "cp": 260.0 if ps["state"] == PST.POWER else None}
    if tpace:
        out["tpace"] = tpace
    return out


def P(**types) -> PP.Prefs:
    return replace(PP.Prefs(), type_basis=tuple(sorted(types.items())))


EASY = {"kind": "easy", "title": "輕鬆跑"}
LONG = {"kind": "long", "title": "長跑"}
Z3 = {"kind": "quality", "title": "有氧間歇 3×10 分", "rung_key": "a2"}
Z5 = {"kind": "quality", "title": "VO2max 間歇 5×3 分", "rung_key": "z5b"}
HILL = {"kind": "quality", "title": "爬坡 6×3 分"}


# ---- 1. the order: own → type → overall → 自動 ---------------------------------------------------------

def test_the_type_overrides_the_overall_basis_and_the_session_overrides_the_type():
    th = th_of(ONE)
    assert TP.target_policy(Z5, PP.Prefs(), th)["basis"] == "power"                      # 自動
    assert TP.target_policy(Z5, replace(PP.Prefs(), target_basis="hr"), th)["basis"] == "hr"    # overall
    p = replace(P(z5="pace"), target_basis="hr")
    r = TP.target_policy(Z5, p, th)
    assert r["basis"] == "pace" and r["how"] == "type" and r["why"] == "進階設定：5 區間歇用配速"
    # the other types still follow the overall one
    assert TP.target_policy(Z3, p, th)["basis"] == "hr" and TP.target_policy(Z3, p, th)["how"] == "pref"
    # the session's own 目標用 (a user edit) wins over its type's
    assert TP.target_policy({**Z5, "target_basis": "power"}, p, th)["basis"] == "power"
    # a type absent = the overall one; nothing set = 自動
    assert TP.target_policy(EASY, P(z5="pace"), th)["how"] == "auto"


@pytest.mark.parametrize("sess,key,pick,want", [
    (EASY, "easy", "hr", "hr"), (LONG, "long", "hr", "hr"), (Z3, "z3", "hr", "hr"), (Z3, "z3", "pace", "pace"),
    (Z5, "z5", "hr", "hr"), (Z5, "z5", "pace", "pace"), (HILL, "hill", "hr", "hr")])
def test_each_type_takes_its_own_basis_in_one(sess, key, pick, want):
    th = th_of(ONE)
    r = TP.target_policy(sess, P(**{key: pick}), th)
    assert TP.type_key(sess) == key and r["basis"] == want and r["how"] == "type"
    # and only that type: the others keep 自動 (power in ①)
    others = [s for s in (EASY, LONG, Z3, Z5, HILL) if TP.type_key(s) != key]
    assert all(TP.target_policy(s, P(**{key: pick}), th)["basis"] == "power" for s in others)


def test_power_picked_for_a_type_while_the_overall_is_hr():
    th = th_of(ONE)
    p = replace(P(easy="power", hill="power"), target_basis="hr")
    assert TP.target_policy(EASY, p, th)["basis"] == "power" and TP.target_policy(HILL, p, th)["basis"] == "power"
    assert TP.target_policy(Z3, p, th)["basis"] == "hr"


@pytest.mark.parametrize("sess", [{"kind": "mountain", "title": "山路長天"}, {"kind": "long", "title": "長爬坡"},
                                  {"kind": "easy", "id": "steep", "title": "陡坡健走"},
                                  {"kind": "easy", "title": "下坡練習"}, {"kind": "test", "title": "CP 測試"},
                                  {"kind": "easy", "title": "越野輕鬆跑", "terrain": "trail"}])
def test_the_types_with_their_own_rules_are_not_choosable(sess):
    assert TP.type_key(sess) is None
    every = P(easy="hr", long="hr", z3="hr", z5="hr", hill="hr")
    assert TP.target_policy(sess, every, th_of(ONE))["how"] != "type"


def test_the_flat_zone_of_a_session():
    assert TP.flat_zone({"rung_key": "a1"}) == "z3" and TP.flat_zone({"rung_key": "z5c"}) == "z5"
    z5v = next(k for k, v in IL.ALL.items() if v.terrain != "hill" and v.cls == "Z5")
    z3v = next(k for k, v in IL.ALL.items() if v.terrain != "hill" and v.cls == "Z3sub")
    assert TP.flat_zone({"variant_key": z5v}) == "z5" and TP.flat_zone({"variant_key": z3v}) == "z3"
    assert TP.flat_zone({"detail": "3×3 分，105–110% CP"}) == "z5"
    assert TP.flat_zone({"detail": "2×15 分，88–95% CP"}) == "z3"
    assert TP.flat_zone({"variant_adj": {"band": [1.05, 1.10]}}) == "z5"
    assert TP.flat_zone({"title": "VO2max 間歇 5×4 分"}) == "z5"
    assert TP.flat_zone({"title": "有氧間歇 2×15 分（平路）"}) == "z3"


def test_only_choosable_values_are_stored():
    assert PP.clean_type_basis({"z5": "pace", "easy": "auto", "z3": None}) == (("z5", "pace"),)
    assert PP.clean_type_basis({"easy": "pace", "trail_long": "hr", "z5": "watts"}) == ()      # stored: dropped
    for bad in ({"easy": "pace"}, {"climb": "hr"}, {"hill": "pace"}):
        with pytest.raises(ValueError):
            PP.clean_type_basis(bad, strict=True)                                              # new: refused
    p = PP.from_settings({"plan.prefs.type_basis": {"z5": "pace", "mp": "hr"}})
    assert p.type_basis == (("mp", "hr"), ("z5", "pace")) and p.to_dict()["type_basis"] == {"mp": "hr", "z5": "pace"}
    assert p.active and not PP.Prefs().active


# ---- 2. ② / ③ and no threshold pace ----------------------------------------------------------------

def test_a_chosen_power_does_not_apply_without_power_and_says_why():
    p = P(z5="power", easy="power", hill="power")
    for ps in (TWO, THREE):
        th = th_of(ps)
        z5 = TP.target_policy(Z5, p, th)
        assert z5["basis"] == "pace" and z5["how"] == "type" and z5["fallback"]
        assert z5["why"].startswith("進階設定：5 區間歇用功率（")
        assert TP.target_policy(EASY, p, th)["basis"] == "hr" and TP.target_policy(HILL, p, th)["basis"] == "hr"
    assert "做一次 CP 測試" in TP.target_policy(Z5, p, th_of(TWO))["why"]
    assert "沒有功率計" in TP.target_policy(Z5, p, th_of(THREE))["why"]


def test_pace_without_a_threshold_pace_goes_to_hr_and_rpe_with_the_reason():
    for ps in (ONE, THREE):
        r = TP.target_policy(Z3, P(z3="pace"), th_of(ps, tpace=None))
        assert r["basis"] == "hr" and "沒有閾值配速：改用心率＋RPE" in r["why"]


def test_the_mp_segment():
    th = th_of(ONE)
    assert TP.mp_policy(PP.Prefs(), th)["basis"] == "pace"                      # 自動 (the overall never reached it)
    assert TP.mp_policy(replace(PP.Prefs(), target_basis="hr"), th)["basis"] == "pace"
    assert TP.mp_policy(P(mp="hr"), th)["basis"] == "hr"
    r = TP.mp_policy(P(mp="power"), th)
    assert r["basis"] == "power" and r["why"] == "進階設定：馬拉松配速段用功率"
    r = TP.mp_policy(P(mp="power"), th_of(THREE))
    assert r["basis"] == "pace" and r["fallback"] == "沒有功率計：用配速"
    assert TP.mp_policy(P(mp="power"), th_of(TWO))["basis"] == "pace"
    # no goal pace and no threshold pace → heart rate (+ RPE) with the reason; a goal pace is enough
    r = TP.mp_policy(P(mp="pace"), th_of(ONE, tpace=None))
    assert r["basis"] == "hr" and "沒有閾值配速" in r["fallback"]
    assert TP.mp_policy(P(mp="pace"), th_of(ONE, tpace=None), goal=300)["basis"] == "pace"
    # the power band is the pace's speed as % CP
    lo, hi = WS.mp_power_band(None, None)
    assert lo == pytest.approx(1 / 1.08, abs=1e-3) and hi == pytest.approx(1 / 1.04, abs=1e-3)
    lo, hi = WS.mp_power_band(300.0, 270.0)
    assert lo < 0.9 < hi


# ---- 3. one answer: the texts, the structure, the push, the card --------------------------------------

def _lib(v_key, th, prefs=None):
    v = IL.get(v_key)
    f = {"variant": v, "level": "std", "reps": None, "equiv": True, "reason": "t", "rung": v.rung}
    return IL.session_for(f, th, prefs=prefs)


Z3_KEY = next(k for k, v in IL.ALL.items() if v.terrain != "hill" and v.cls == "Z3sub")
Z5_KEY = next(k for k, v in IL.ALL.items() if v.terrain != "hill" and v.cls == "Z5" and min(v.works) >= 180)


def test_the_library_texts_follow_the_type():
    th = th_of(ONE)
    p = P(z5="pace")
    assert _lib(Z5_KEY, th, p)["target"].startswith("配速 ")          # the Zone 5 by pace
    assert _lib(Z3_KEY, th, p)["target"].startswith("功率 ")          # the Zone 3 untouched
    assert TP.flat_pace(th, p, s={"_zone": "z5"}) and not TP.flat_pace(th, p, s={"_zone": "z3"})


def test_the_structure_and_the_push_follow_the_type():
    from backend.engine import plan_store as PS
    th = th_of(ONE)
    p = P(easy="hr", z5="pace")
    # the structure's context
    assert WS.session_ctx(EASY, th, p).basis == "hr" and WS.session_ctx(Z5, th, p).basis == "pace"
    assert WS.session_ctx(Z3, th, p).basis == "power"
    # push_dict resolves the basis the push uses
    row = lambda s: {"uid": "u", "week_start": "2026-10-26", "target": "", "state": "active", "day": "2026-10-28",
                     "minutes": 60, **s}
    assert PS.push_dict(row(Z5), p, th)["basis"] == "pace"
    assert PS.push_dict(row(Z3), p, th)["basis"] == "power"
    assert PS.push_dict(row(EASY), p, th)["basis"] == "hr"
    assert PS.push_dict(row(EASY), PP.Prefs(), th)["basis"] == "power"


MP_LONG = {"kind": "long", "title": "長跑＋馬拉松配速 30 分", "minutes": 120, "day": "2026-11-01",
           "detail": "平路；前 80 分輕鬆（心率 ≤ 140），接著 30 分馬拉松配速（目標配速 5:00/km），最後 10 分輕鬆收操"}


def _mp_step(steps):
    return next(x for x in steps if x.name == "馬拉松配速")


@pytest.mark.parametrize("pick,kind", [(None, "pace"), ("hr", "hr"), ("power", "power"), ("pace", "pace")])
def test_the_push_and_the_structure_of_the_mp_segment(pick, kind):
    from backend.engine import plan_store as PS
    th = th_of(ONE)
    p = P(mp=pick) if pick else PP.Prefs()
    row = {"uid": "u", "week_start": "2026-10-26", "target": "", "state": "active", **MP_LONG}
    s = PS.push_dict(row, p, th)
    assert s.get("mp_basis") == (kind if pick else None)
    st = _mp_step(CW.session_steps(s, CW.Thresholds.of(th)))
    assert st.intensity[0] == kind
    if kind == "hr":
        assert st.intensity[1:] == (round(0.88 * 165), round(0.95 * 165))
    if kind == "power":
        lo, hi = WS.mp_power_band(300, TPACE)
        assert st.intensity[1:] == (round(lo * 260), round(hi * 260))
    # the structure (the editor, the card) says the same
    d = WS.derive(s, th)
    mp = next(x for x in d["items"] if x.get("note") == "馬拉松配速")
    assert mp["target"]["type"] == kind


def test_the_mp_segment_in_three_falls_back_to_pace_in_the_push():
    from backend.engine import plan_store as PS
    th = th_of(THREE)
    row = {"uid": "u", "week_start": "2026-10-26", "target": "", "state": "active", **MP_LONG}
    s = PS.push_dict(row, P(mp="power"), th)
    assert s["mp_basis"] == "pace"
    assert _mp_step(CW.session_steps(s, CW.Thresholds.of(th))).intensity[0] == "pace"


def _by(rows):
    return {r["key"]: r for r in rows}


def test_the_card_marks_a_type_set_by_hand():
    th = th_of(ONE)
    r = _by(TB.rows("road", th, P(z5="pace", mp="hr"), mp_goal_pace_s=300))
    assert r["z5"]["basis"] == "pace" and r["z5"]["manual"] and r["z5"]["own"] and r["z5"]["type_key"] == "z5"
    assert r["z3"]["basis"] == "power" and not r["z3"]["manual"] and not r["z3"]["own"]
    assert r["mp"]["basis"] == "hr" and r["mp"]["own"] and r["mp"]["target"] == "心率 145–157 bpm"
    # the overall 目標依據 marks the rows (手動) but they have no per-row 改回自動 (own)
    r = _by(TB.rows("road", th, replace(PP.Prefs(), target_basis="hr")))
    assert r["z3"]["manual"] and not r["z3"]["own"]


def test_the_advanced_rows_follow_the_main_sport_and_the_equipment():
    th = th_of(ONE)
    trail = TB.type_rows("trail", th, P(z5="pace"))
    road = TB.type_rows("road", th, PP.Prefs(), mp_goal_pace_s=300)
    assert [x["key"] for x in trail] == ["easy", "z3", "z5", "hill"]
    assert [x["key"] for x in road] == ["easy", "long", "z3", "z5", "mp"]
    t = _by(trail)
    assert t["z5"]["choice"] == "pace" and t["z5"]["auto"] == "power" and t["z5"]["auto_label"] == "功率"
    assert t["easy"]["options"] == ["hr", "power"] and t["z3"]["options"] == ["hr", "pace", "power"]
    assert _by(road)["mp"]["auto"] == "pace" and _by(road)["mp"]["options"] == ["hr", "pace", "power"]
    # 自動 shows what the overall 目標依據 gives that type
    assert _by(TB.type_rows("trail", th, replace(PP.Prefs(), target_basis="hr")))["z3"]["auto"] == "hr"
    # ③: no 功率 to choose; a stored one stays as the choice (shown, unused)
    t3 = _by(TB.type_rows("trail", th_of(THREE), P(hill="power")))
    assert all("power" not in x["options"] for x in t3.values()) and t3["hill"]["choice"] == "power"


# ---- 4. the API: PATCH merges, re-plans; PUT /prefs keeps it ----------------------------------------------

@pytest.fixture
def owner(monkeypatch, tmp_path):
    from backend.engine import plan_auto as PA
    from backend.tests.test_pwa_sp314 import _owner_client
    calls = []
    monkeypatch.setattr(PA, "after_thresholds", lambda *a, **k: calls.append("th"))
    with _owner_client(monkeypatch, tmp_path) as cl:
        cl.calls = calls
        yield cl


def test_patch_sets_one_type_at_a_time_and_replans(owner):
    cl = owner
    r = cl.patch("/api/v1/overview/plan/prefs", json={"type_basis": {"z5": "pace"}})
    assert r.status_code == 200, r.text
    assert r.json()["prefs"]["type_basis"] == {"z5": "pace"} and cl.calls == ["th"]
    r = cl.patch("/api/v1/overview/plan/prefs", json={"type_basis": {"easy": "hr"}})
    assert r.json()["prefs"]["type_basis"] == {"easy": "hr", "z5": "pace"} and cl.calls == ["th", "th"]
    cl.patch("/api/v1/overview/plan/prefs", json={"type_basis": {"easy": "hr"}})
    assert cl.calls == ["th", "th"]                                     # unchanged: nothing
    r = cl.patch("/api/v1/overview/plan/prefs", json={"type_basis": {"z5": "auto"}})
    assert r.json()["prefs"]["type_basis"] == {"easy": "hr"} and cl.calls == ["th", "th", "th"]
    # the 課表偏好 dialog's PUT (the whole set, without it) keeps it
    base = cl.get("/api/v1/overview/plan/prefs").json()["prefs"]
    body = {k: v for k, v in base.items() if k != "type_basis"}
    assert cl.put("/api/v1/overview/plan/prefs", json={**body, "runs": 5}).status_code == 200
    assert cl.get("/api/v1/overview/plan/prefs").json()["prefs"]["type_basis"] == {"easy": "hr"}


@pytest.mark.parametrize("body", [{"type_basis": {"easy": "pace"}}, {"type_basis": {"climb": "hr"}},
                                  {"type_basis": "z5=pace"}, {"type_basis": {"z5": "watts"}}])
def test_patch_refuses_what_cannot_be_chosen(owner, body):
    assert owner.patch("/api/v1/overview/plan/prefs", json=body).status_code == 400
    assert owner.calls == []
