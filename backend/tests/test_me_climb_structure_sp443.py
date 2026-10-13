"""
SP-443: the multi-day 百岳's 「ME 負重爬坡」 (specific_phase.me_session, kind quality, no variant) derives and
pushes as what its text says, not 「看不懂間歇結構」.

me_session sizes it as 暖身 ME_WARM_MIN → the climb up with the pack at ME_UP_MPH → down light (water poured
out at the top) at ME_DOWN_MPH, the session rounded to 5′. The structure: 暖身 → 上坡 (the climb, the walking
session's uphill cap: target_policy walk, unchanged) → 下坡 (the rest of the session; 「下坡看腿的感覺，不看
心率」: no HR on the watch, an RPE in its name). Synthetic sessions; nothing reads the athlete's data.
"""
import pytest

from backend.engine import hr_profile as HP
from backend.engine import specific_phase as SP
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW
from backend.tests.test_baiyue_multiday import me_week, trip

TH_WALK = {"cp": 250.0, "lthr": 165.0, "aet": 145.0, "walk_cap": HP.walk_cap(196.0, 145.0)}


def _me(weight=60.0):
    """The generator's own ME session (賽前第 7 週: 750 m, 150′, 背 9 kg) with its walking target."""
    info, ss = me_week()
    SP.apply_me(ss, info, 0.08, weight)
    SP.walk_targets(ss, TH_WALK["walk_cap"], 145.0)
    return next(s for s in ss if s["id"] == "me")


def test_the_generated_me_session_derives_warm_up_climb_descent():
    me = _me()
    assert me["title"] == "ME 負重爬坡（爬升 750 m、背 9 kg）" and me["minutes"] == 150 and "暖身 10 分" in me["detail"]
    d = WS.derive(me, TH_WALK)
    assert d is not None
    items = d["items"]
    assert [x["kind"] for x in items] == ["warm", "work", "work"]
    # 暖身 10 → 上坡 750 m at 500 m/h = 90′ → 下坡 the rest (45′ at 1000 m/h + the 5′ me_session adds)
    assert [x["dur"]["value"] for x in items] == [600, 90 * 60, 50 * 60]
    up, down = items[1], items[2]
    assert "750 m" in up["note"] and "9 kg" in up["note"] and "RPE ≤ 13" in up["note"]
    assert up["target"]["type"] == "auto" and up["target"]["intent"] == "easy"
    assert down["target"]["type"] == "rpe" and down["target"]["down"] == 750 and "下山" in down["note"]
    assert WS.totals(WS.normalize(d), WS.session_ctx(me, TH_WALK))["sec"] == me["minutes"] * 60


def test_the_me_session_pushes_its_structure_with_the_walk_cap_uphill():
    me = _me()
    st = CW.session_steps({**me, "basis": "hr"}, CW.Thresholds.of(TH_WALK))
    assert [x.kind for x in st] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_TRAIN]
    assert [x.seconds for x in st] == [600, 5400, 3000]
    # the uphill: the walking session's cap (the same band as the 陡坡健走's steep part, SP-409)
    assert st[1].intensity == ("hr", 124, 147) and "750 m" in st[1].name
    # the descent: by feel, no HR on the watch, the RPE and the descent in its name
    assert st[2].intensity is None and "RPE 3–4" in st[2].name and "下降 750 m" in st[2].name
    # the push and the editor's structure are the same steps
    c = WS.session_ctx(me, TH_WALK)
    assert WS.steps_to_coros(WS.normalize(WS.derive(me, TH_WALK)), c) == st


def test_a_stored_me_row_without_its_id_still_derives():
    """A stored week keeps the title, not always the id (target_policy.ME_WORDS)."""
    s = {"kind": "quality", "title": "ME 負重爬坡（爬升 400 m、背 10 kg）", "minutes": 85,
         "detail": "背 10 kg（沒有體重紀錄，先用這個重量）爬最陡的坡…；暖身 10 分。做完 3 天只排輕鬆"}
    items = WS.derive(s, TH_WALK)["items"]
    # 10 + 400 m at 500 m/h = 48′ up; the rest of the 85′ down (24′ at 1000 m/h + me_session's rounding)
    assert [x["dur"]["value"] for x in items] == [600, 48 * 60, 27 * 60]
    st = CW.session_steps({**s, "basis": "hr"}, CW.Thresholds.of(TH_WALK))
    assert sum(x.seconds for x in st) == 85 * 60


def test_the_structure_tss_is_near_the_card():
    """The structure's TSS (SP-437 reads it) stays near the card's rate × hours, not the no-target 1.05."""
    me = _me()
    t = WS.totals(WS.normalize(WS.derive(me, TH_WALK)), WS.session_ctx(me, TH_WALK))
    assert t["tss"] == pytest.approx(me["tss"], rel=0.15)


def test_an_me_session_cut_below_its_climb_is_not_guessed():
    """Minutes the user cut below the written climb: nothing to read, as before (no structure)."""
    me = _me()
    assert WS.derive({**me, "minutes": 90}, TH_WALK) is None


def test_week_plan_me_session_derives_and_pushes():
    """The planner's real path (overview.week_plan, as test_baiyue_multiday's gate test)."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    from backend.tests.test_quality_gate import TODAY as T0
    ds = _history(T0)
    plan = _plan_with("2026-12-05", 1, T0)
    plan.events = [trip(start="2026-11-07", est_hours=21.0)]
    ds.plan = plan
    st = Status(ds, plan, T0, prefs=PP.Prefs()).compute()
    next(i for i in st.indicators if i.id == "gate").extra["gap"] = 0.07
    wp = O.week_plan(ds, st, T0)
    me = next(s for s in wp["sessions"] if s["id"] == "me")
    d = WS.derive(me, wp.get("thresholds"))
    assert d is not None and [x["kind"] for x in d["items"]] == ["warm", "work", "work"]
    steps = CW.session_steps(me, CW.Thresholds.of(wp.get("thresholds")))
    assert sum(x.seconds for x in steps) == me["minutes"] * 60


def test_other_quality_sessions_derive_as_before():
    """A 「N×M 分」 quality session still reads its text; an unreadable one still raises."""
    s = {"kind": "quality", "title": "VO2max 間歇 5×4 分上坡", "minutes": 60, "detail": "暖身 15 分；休 3 分"}
    items = WS.derive(s, TH_WALK)["items"]
    assert [x["kind"] for x in items] == ["warm", "repeat", "cool"] and items[1]["times"] == 5
    with pytest.raises(CW.Unsupported):
        CW.session_steps({"kind": "quality", "title": "隨便跑", "minutes": 40}, CW.Thresholds.of(TH_WALK))
    assert WS.derive({"kind": "quality", "title": "隨便跑", "minutes": 40}, TH_WALK) is None
