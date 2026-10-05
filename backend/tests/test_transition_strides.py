"""
SP-103: the 轉換期 keeps one short set of strides a week from its 2nd week (overview.strides_for /
transition_week / TRANSITION_STRIDES): the week's first easy run gets 4 × 15 s at about 5K pace (Jay
Johnson; the cyclists' transition trials, 推估 for running); none in week 1 or in the 恢復期.
Synthetic athlete and plan.
"""
import datetime as dt
from datetime import date

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import planning as P
from backend.engine import projection as PJ
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30


def test_strides_for_and_transition_week():
    ph = [{"kind": "recovery", "start": "2026-09-21", "end": "2026-09-27"},
          {"kind": "transition", "start": "2026-09-28", "end": "2026-10-18"}]
    assert O.transition_week(ph, date(2026, 9, 21)) is None
    assert [O.transition_week(ph, date(2026, 9, 28) + dt.timedelta(weeks=k)) for k in range(3)] == [1, 2, 3]
    mid = [{"kind": "transition", "start": "2026-09-30", "end": "2026-10-20"}]       # starts on a Wednesday
    assert O.transition_week(mid, date(2026, 9, 28)) == 1 and O.transition_week(mid, date(2026, 10, 5)) == 2
    assert O.strides_for("transition", "transition", 0, False, 1) is None
    assert O.strides_for("transition", "transition", 0, False, 2) == O.TRANSITION_STRIDES
    assert O.strides_for("transition", "transition", 1, False, 2) is None            # the first easy run only
    assert O.strides_for("recovery", "recovery", 0, False, None) is None
    assert O.strides_for("base", "base", 0, True, None) == O.ROAD_STRIDES             # base unchanged
    assert O.strides_for("base", "recovery_week", 0, False, None) is None
    t, d, s = O.TRANSITION_STRIDES
    assert "4×15 秒" in t and "5K" in d and "推估" in s and "Jay Johnson" in s


def _setup():
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = [P.Event("r", "路跑", "2026-09-20", kind="road", priority="A", est_hours=3.0)]
    ds.plan = plan
    return ds, plan, Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()


def test_week_plan_week_1_none_projected_weeks_2_and_3_once():
    ds, plan, st = _setup()
    assert st.kind == "transition"
    wp = O.week_plan(ds, st, TODAY)                                       # 轉換期 week 1
    assert not any("加速" in s["title"] for s in wp["sessions"])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 25))
    tr = [w for w in weeks if w["phase"] == "transition"]
    assert [w["start"] for w in tr] == ["2026-10-05", "2026-10-12"]
    for w in tr:
        st_ = [s for s in w["sessions"] if O.TRANSITION_STRIDES[0] in s["title"]]
        assert len(st_) == 1 and st_[0]["kind"] == "easy" and st_[0]["minutes"] <= O.TRANSITION_RUN_MAX
        assert "推估" in st_[0]["source"]
        first = min(s["day"] for s in w["sessions"] if s["kind"] == "easy")
        assert st_[0]["day"] == first                                     # the week's first easy run
        assert any(n.get("src") == "transition" and "15 秒加速" in n["text"] for n in w["notes"])
    # a week later (week 2 is now the current week): week_plan gives it too, with 課表偏好 越野 easy runs
    later = TODAY + dt.timedelta(weeks=1)
    st2 = Status(ds, plan, later, prefs=PP.Prefs()).compute()
    wp2 = O.week_plan(ds, st2, later, prefs=PP.Prefs(terrain_easy="trail"))
    (s,) = [s for s in wp2["sessions"] if O.TRANSITION_STRIDES[0] in s["title"]]
    assert s["title"].startswith("輕鬆越野跑") and "5K" in s["detail"] and "坡道衝刺" not in s["title"]
