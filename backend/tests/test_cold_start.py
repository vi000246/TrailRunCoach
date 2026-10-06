"""冷啟動排課 (SP-288, engine/cold_start.py): the first week of a runner without history and the
RAMP_WEEKS after it — the old self-contradicting week reproduced on an empty dataset, the
questionnaire (as entered), no questionnaire (1.5 h, 3 runs, no long run), 「還不能連續跑 30 分鐘」,
the 40 % long-run share under 120 min, the run count / days apart, this week vs the projection, and
a runner with history getting exactly today's plan. Synthetic data only."""
from __future__ import annotations

import copy
import datetime as dt
import types

import pytest

from backend.engine import cold_start as CS
from backend.engine import experience as EX
from backend.engine import load_guard as LG
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.engine import rest_days as RD
from backend.engine.planning import Plan
from backend.engine.status import Status
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

MON = dt.date(2026, 10, 5)            # a Monday
AT = "2026-10-01T00:00:00+00:00"


def _ds(workouts=(), today=MON):
    ds = FakeDataset(list(workouts), today, settings={})
    ds.config = types.SimpleNamespace(parity=True)
    ds.setting_label = lambda name, default="": default
    ds.settings_from = "app"
    ds.aethr = lambda w: None              # a new runner: no threshold yet
    ds.cp = lambda w: None
    ds.mftp_run = None
    return ds


def _week(ds, today=MON, prefs=None, plan=None):
    plan = plan or Plan()
    ds.plan = plan
    st = Status(ds, plan, today, prefs=prefs or PP.Prefs()).compute()
    return O.week_plan(ds, st, today, prefs=prefs)


def _survey(monkeypatch, exp):
    monkeypatch.setattr(EX, "load", lambda user_id=1: exp)


def _runs(wp):
    return [s for s in wp["sessions"] if s["kind"] in O.RUN_KINDS]


def _old_rules(monkeypatch):
    """The planner before SP-288: no cold start, the long run's share only from 120 min."""
    monkeypatch.setattr(CS, "week_context", lambda *a, **k: None)
    monkeypatch.setattr(CS, "long_share_cap", lambda m, t: min(m, 0.5 * t) if t >= 120 else m)


def test_empty_dataset_reproduces_the_old_first_week(monkeypatch):
    """SP-288 驗收 1: the old rules on an empty dataset — 0.5 h a week next to a 60-min long run and no
    easy run (cold-start.md §1.1: the 60 min was read from the code, now run)."""
    _old_rules(monkeypatch)
    wp = _week(_ds())
    assert wp["target"]["hours"] == pytest.approx(0.5)
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert long_s["minutes"] == 60 and long_s["minutes"] > wp["target"]["hours"] * 60     # longer than the week
    assert not [s for s in wp["sessions"] if s["kind"] == "easy"]


def test_no_questionnaire_one_and_a_half_hours_three_runs_no_long_run(monkeypatch):
    _survey(monkeypatch, None)
    wp = _week(_ds())
    assert wp["target"]["hours"] == pytest.approx(CS.DEFAULT_HOURS) == pytest.approx(1.5)
    runs = _runs(wp)
    assert [s["kind"] for s in runs] == ["easy"] * 3 and [s["minutes"] for s in runs] == [30, 30, 30]
    days = sorted(dt.date.fromisoformat(s["day"]) for s in runs)
    assert all((b - a).days >= 2 for a, b in zip(days, days[1:]))          # not two days in a row
    note = next(n for n in wp["notes"] if n.get("src") == "cold_start")
    assert "還沒填跑步經驗" in note["text"] and "設定 → 個人資料 → 跑步經驗" in note["text"]
    assert wp["cold_start"]["level"] == 0 and wp["cold_start"]["source"] == "default"
    assert not [s for s in wp["sessions"] if s["kind"] in ("quality", "test")]


def test_questionnaire_volume_as_entered_and_the_longest_run(monkeypatch):
    # 3 × 40 min (2.0 h), longest 45: not discounted; the long run ≤ +10 % over the reported longest
    _survey(monkeypatch, {"runs_per_week": 3, "minutes_per_run": 40, "longest_min": 45, "at": AT})
    wp = _week(_ds())
    assert wp["target"]["hours"] == pytest.approx(2.0)                      # not × 0.75
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert long_s["minutes"] <= 45 * LG.LONG_CAP
    assert any(n.get("src") == "long_cap" for n in wp["notes"])
    assert len(_runs(wp)) == 3
    assert "2.0 小時" in next(n for n in wp["notes"] if n.get("src") == "cold_start")["text"]
    # 「能連續跑 30 分鐘」: at least 1.5 h even when the runs reported are fewer
    _survey(monkeypatch, {"runs_per_week": 2, "minutes_per_run": 30, "can_run_30": True, "at": AT})
    assert _week(_ds())["target"]["hours"] == pytest.approx(1.5)
    # without that answer the reported 1.0 h stays (as entered)
    _survey(monkeypatch, {"runs_per_week": 2, "minutes_per_run": 30, "at": AT})
    wp = _week(_ds())
    assert wp["target"]["hours"] == pytest.approx(1.0) and not [s for s in wp["sessions"] if s["id"] == "long"]
    # more runs reported than 3: kept, so each run stays about the usual length
    _survey(monkeypatch, {"runs_per_week": 5, "minutes_per_run": 60, "longest_min": 100, "at": AT})
    wp = _week(_ds())
    assert wp["target"]["hours"] == pytest.approx(5.0) and len(_runs(wp)) == 5
    assert max(s["minutes"] for s in _runs(wp)) <= 100 * LG.LONG_CAP


def test_short_week_long_run_is_at_most_forty_percent_without_the_60_min_floor(monkeypatch):
    _survey(monkeypatch, {"runs_per_week": 3, "minutes_per_run": 30, "longest_min": 60, "at": AT})
    wp = _week(_ds())
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert wp["target"]["hours"] == pytest.approx(1.5)
    assert long_s["minutes"] <= CS.LONG_SHARE * 90 and long_s["minutes"] < 60
    assert CS.long_share_cap(60, 90) == pytest.approx(36) and CS.long_share_cap(80, 150) == 75
    assert CS.long_share_cap(60, 120) == 60


def test_cannot_run_30_minutes_gets_the_default_and_the_note(monkeypatch):
    _survey(monkeypatch, {"runs_per_week": 3, "minutes_per_run": 20, "can_run_30": False, "at": AT})
    wp = _week(_ds())
    assert wp["target"]["hours"] == pytest.approx(1.5)
    assert [s["kind"] for s in _runs(wp)] == ["easy"] * 3                   # no run-walk sessions
    assert not any("跑走" in (s["title"] + s["detail"]) for s in wp["sessions"])
    note = next(n for n in wp["notes"] if n.get("src") == "cold_start")
    assert "app 的自動排課要等你能連續跑 30 分鐘之後才準" in note["text"]


def test_preference_run_count_wins(monkeypatch):
    _survey(monkeypatch, None)
    wp = _week(_ds(), prefs=PP.Prefs(runs=4))
    assert len(_runs(wp)) == 4 and wp["target"]["hours"] == pytest.approx(1.5)
    # other preferences set but no 每週跑步次數: the cold week's 3 runs
    wp = _week(_ds(), prefs=PP.Prefs(days=(True, True, True, True, True, True, False)))
    assert len(_runs(wp)) == 3


def test_apart_penalty_spreads_three_runs():
    week = [MON + dt.timedelta(days=i) for i in range(7)]
    got = RD.pick_days(3, week, week, [], None, None, apart=True)
    assert all((b - a).days >= 2 for a, b in zip(got, got[1:]))
    assert RD.pick_days(3, week, week, [], None, None) == RD.pick_days(3, week, week, [])     # default unchanged


def _did(plan_week: dict, tss_per_h: float = 60.0) -> list:
    """The week's runs done as planned (no strength: the projection's hours are run hours)."""
    return [FakeWorkout(start=dt.datetime.combine(dt.date.fromisoformat(s["day"]), dt.time(7)), sport="run",
                        sport_type="running", tags=["running"],
                        metrics={"duration": s["minutes"] * 60.0, "movingduration": s["minutes"] * 60.0,
                                 "tss": s["minutes"] / 60 * tss_per_h})
            for s in plan_week["sessions"] if s["kind"] in O.RUN_KINDS and s.get("day")]


@pytest.mark.parametrize("exp", [None, {"runs_per_week": 3, "minutes_per_run": 40, "longest_min": 50, "at": AT}])
def test_this_week_and_the_projection_agree(monkeypatch, exp):
    """SP-288 驗收: the projection rolls the cold week forward by the existing rules (+10 %, at
    least +0.5 h) from the start level, and next week's real plan (week 1 done as planned) is the
    projection's week 1 — same hours, same run count, a long run or not alike."""
    _survey(monkeypatch, exp)
    w1 = _week(_ds())
    proj = PJ.project_weeks(w1, [], MON + dt.timedelta(weeks=3))
    h1 = w1["target"]["hours"]
    # never below the start level, at most +10 % / +0.5 h over it
    assert h1 - 1e-6 <= proj[0]["hours"] <= max(1.10 * h1, h1 + 0.5) + 1e-6
    nxt = MON + dt.timedelta(weeks=1)
    w2 = _week(_ds(_did(w1), nxt), nxt)
    assert w2["cold_start"]["level"] == 1 and w2["cold_start"]["week"] == 2
    assert w2["target"]["hours"] == pytest.approx(proj[0]["hours"], abs=0.05)
    p_runs = [s for s in proj[0]["sessions"] if s["kind"] in O.RUN_KINDS]
    assert len(_runs(w2)) == len(p_runs)
    assert any(s["id"] == "long" for s in _runs(w2)) == any(s["id"] == "long" for s in p_runs)
    # the ramp: no intervals in the first 4 weeks, the run count stays ≥ 3
    for w in proj:
        if w["start"] < w1["cold_start"]["until"]:
            assert not [s for s in w["sessions"] if s["kind"] in ("quality", "test")]
            if w["mode"] in ("base", "specific"):
                assert len([s for s in w["sessions"] if s["kind"] in O.RUN_KINDS]) >= 3


def test_ramp_keeps_the_start_level_as_the_base(monkeypatch):
    """§4.2 實際紀錄: in the ramp the base is max(start level, actual) — 4 h reported, one week of
    data: next week does not drop to the CTL-ramp hours of an empty history."""
    _survey(monkeypatch, {"runs_per_week": 4, "minutes_per_run": 60, "longest_min": 90, "at": AT})
    w1 = _week(_ds())
    nxt = MON + dt.timedelta(weeks=1)
    w2 = _week(_ds(_did(w1), nxt), nxt)
    assert w1["target"]["hours"] == pytest.approx(4.0)
    assert w2["target"]["hours"] >= 4.0 - 1e-6
    assert any(n.get("src") == "cold_start" and "第 2/4 週" in n["text"] for n in w2["notes"])
    # after the ramp the start level no longer counts
    later = MON + dt.timedelta(weeks=CS.RAMP_WEEKS)
    assert CS.week_context(_ds(_did(w1), later), later) is None


def _history_ds():
    from backend.tests.test_long_spike import _history
    return _history(120)


def test_runner_with_history_gets_exactly_todays_plan(monkeypatch):
    """The owner's case: weeks of data — the week plan and its projection are the ones the planner
    made before SP-288 (only the new `cold_start` key, None, is added)."""
    from backend.tests.test_long_spike import TODAY, _plan
    _survey(monkeypatch, {"runs_per_week": 2, "minutes_per_run": 20, "can_run_30": False, "at": AT})  # ignored
    ds = _history_ds()
    assert CS.week_context(ds, TODAY) is None
    plan = _plan()
    new = _week(ds, TODAY, plan=plan)
    new_p = PJ.project_weeks(copy.deepcopy(new), [], TODAY + dt.timedelta(weeks=4))
    assert new.pop("cold_start") is None
    with monkeypatch.context() as m:
        _old_rules(m)
        old = _week(_history_ds(), TODAY, plan=_plan())
        old.pop("cold_start")
        old_p = PJ.project_weeks(copy.deepcopy({**old, "cold_start": None}), [], TODAY + dt.timedelta(weeks=4))
    assert new == old
    assert new_p == old_p
    assert not any(n.get("src") == "cold_start" for n in new["notes"])


def test_a_break_with_history_is_reentry_not_a_cold_start(monkeypatch):
    """Data, then 5 weeks off: the 停訓後的恢復期 (reentry.py) handles it, not the cold start."""
    from backend.tests.test_long_spike import _history
    _survey(monkeypatch, None)
    ds = _history(120, today=MON - dt.timedelta(weeks=5))
    ds.today = FakeDataset([], MON).today
    ds.setting_label = lambda name, default="": default
    ds.settings_from = "app"
    from backend.engine import reentry as RE
    rp = RE.find(ds, MON)
    assert rp and rp.get("prev_hours")
    assert CS.week_context(ds, MON, reentry=rp) is None


def test_data_start_and_levels():
    d = lambda k: MON + dt.timedelta(days=k)
    assert CS.data_start([], MON) == MON                                   # nothing: the data starts now
    assert CS.data_start([d(1)], MON) == MON                               # a run this week, none before
    assert CS.data_start([d(-10), d(-8)], MON) == MON - dt.timedelta(weeks=2)
    assert CS.data_start([d(-200), d(-10)], MON) == MON - dt.timedelta(weeks=2)     # after a > 28-day gap
    assert CS.data_start([d(-40)], MON) == MON                             # nothing in the 28 days before
    lv = CS.start_level({"runs_per_week": 3, "minutes_per_run": 50, "at": AT})
    assert lv["hours"] == pytest.approx(2.5) and lv["long"] and lv["source"] == "survey"
    assert CS.start_level(None)["source"] == "default" and not CS.start_level(None)["long"]
    assert CS.start_level({"can_run_30": False, "runs_per_week": 5, "minutes_per_run": 60, "at": AT})["hours"] == 1.5
    assert CS.start_level({"at": AT})["source"] == "default"               # answered, volume left blank


def test_notes_are_translated():
    import re
    from backend.i18n import use_locale
    with use_locale("en"):
        for src in ("default", "no_run30", "survey"):
            for lv in (0, 1):
                t = CS.note({"level": lv, "source": src, "hours": 1.5, "runs": 3, "week": 2})["text"]
                assert t and not re.search("[一-鿿]", t), t
        assert not re.search("[一-鿿]", CS.why({"source": "survey", "hours": 2.0, "runs": 3}))


def test_no_cp_test_suggestion_in_the_first_weeks_hill_strides_stay(monkeypatch):
    """Owner 2026-10-06 (SP-288): a new runner's first RAMP_WEEKS weeks have no CP-test suggestion — not in
    the week plan, its notes, the 基線測試 box rows or the zone retests — the base phase's hill strides
    stay; with the cold start off (the old rules) the same week still offered it."""
    from backend.engine import baseline_test as BT
    _survey(monkeypatch, None)
    wp = _week(_ds())
    assert wp["cold_start"] is not None
    assert not [t for t in wp["test_suggestions"] if t["kind"] == "cp"]
    assert not any("CP 測試" in n["text"] for n in wp["notes"])
    assert any("坡道衝刺" in s["title"] for s in wp["sessions"])
    assert BT.due(MON.isoformat(), {}, {"cold_start": True}) == [
        x for x in BT.due(MON.isoformat(), {}, {}) if x["kind"] != "cp"]
    assert any(x["kind"] == "cp" for x in BT.due(MON.isoformat(), {}, {}))
    with monkeypatch.context() as m:
        m.setattr(CS, "week_context", lambda *a, **k: None)
        assert [t for t in _week(_ds())["test_suggestions"] if t["kind"] == "cp"]
