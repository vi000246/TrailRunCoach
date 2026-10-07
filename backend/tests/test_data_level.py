"""資料等級 (SP-291, engine/data_level.py): one function gives level 0 / 1 / 2 and the week of the data,
and the plan (cold_start / week_plan), the status page and the race feasibility read it. The level-2
condition is the Zone 3 gate's consistency rule (no new numbers). Boundaries: exactly 28 days, one
session short, a week off running; after the upgrade the questionnaire no longer moves the weekly
volume. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import re

import pytest

from backend.engine import cold_start as CS
from backend.engine import data_level as DL
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import quality_gate as QG
from backend.engine.planning import Plan
from backend.engine.status import Status
from backend.tests.test_cold_start import AT, MON, _did, _ds, _survey, _week
from backend.tests.wko5_fakes import FakeWorkout

RULE = {"weeks": 4, "runs": 3, "gap": 7, "relock": 21, "manual": False}     # = quality_gate.Z3_* defaults


@pytest.fixture(autouse=True)
def _default_rule(monkeypatch):
    """The 進階設定 rule without a settings DB: its defaults (QG.z3_rule falls back to them anyway)."""
    monkeypatch.setattr(QG, "z3_rule", lambda: dict(RULE))


def _run(d: dt.date, minutes: float = 40.0, sport: str = "run", tags=("running",)) -> FakeWorkout:
    return FakeWorkout(start=dt.datetime.combine(d, dt.time(7)), sport=sport, sport_type=tags[0], tags=list(tags),
                       metrics={"duration": minutes * 60.0, "movingduration": minutes * 60.0, "tss": minutes * 0.8})


def _weeks(n: int, per_week=(0, 2, 4), end: dt.date = MON, skip_week=None, short_week=None) -> list:
    """`n` complete weeks before `end` (a Monday) with runs on the weekdays `per_week`; `skip_week` (index,
    0 = the oldest) has none, `short_week` one run fewer."""
    out = []
    for i in range(n):
        m = end - dt.timedelta(weeks=n - i)
        days = () if i == skip_week else per_week[:-1] if i == short_week else per_week
        out += [_run(m + dt.timedelta(days=k)) for k in days]
    return out


def test_level_zero_and_the_28_day_edge():
    lv = DL.level(_ds(), MON)
    assert (lv["level"], lv["week"], lv["survey"]) == (0, 1, True)
    # a run exactly 28 days before this Monday still counts: 等級 1; 29 days: 等級 0
    assert DL.level(_ds([_run(MON - dt.timedelta(days=28))]), MON)["level"] == 1
    assert DL.level(_ds([_run(MON - dt.timedelta(days=29))]), MON)["level"] == 0
    # read on the Monday: a first run this week leaves this week at 0
    wed = MON + dt.timedelta(days=2)
    assert DL.level(_ds([_run(wed)], wed), wed)["level"] == 0


def test_level_two_is_the_zone3_consistency_rule():
    ds = _ds(_weeks(4))
    lv = DL.level(ds, MON)
    assert lv["level"] == 2 and lv["weeks_ok"] == 4 and lv["week"] == 5 and not lv["survey"]
    # the same days through the gate's own function: open
    days = sorted({O.wdate(w) for w in ds.workouts})
    assert QG.z3_consistency(days, MON, rule=RULE)["open"]


def test_one_session_short_or_a_week_off_stays_level_one():
    short = DL.level(_ds(_weeks(4, short_week=2)), MON)            # week 3 has 2 runs
    assert short["level"] == 1 and short["weeks_ok"] == 1          # only the last week counts after it
    off = DL.level(_ds(_weeks(5, skip_week=2)), MON)               # a week without running in the middle
    assert off["level"] == 1 and off["weeks_ok"] == 2
    # one more good week each: still short of 4 in a row
    assert DL.level(_ds(_weeks(5, short_week=2)), MON)["level"] == 1
    # 3 good weeks only (the 4th coming): 1, week 4 of 4 — the questionnaire still stands in
    three = DL.level(_ds(_weeks(3)), MON)
    assert (three["level"], three["week"], three["survey"]) == (1, 4, True)


def test_hikes_count_like_runs():
    hikes = [_run(MON - dt.timedelta(weeks=4 - i, days=-k), 120.0, sport="other", tags=("hiking",))
             for i in range(4) for k in (0, 2, 5)]
    assert {O.category(w) for w in _ds(hikes).workouts} == {"hike"}
    assert DL.level(_ds(hikes), MON)["level"] == 2


def test_rule_from_advanced_settings_moves_the_level(monkeypatch):
    """No new numbers: a 進階設定 rule of 2 weeks makes level 2 after 2 good weeks (week 3) — and the
    questionnaire is no longer used then, though week 3 is inside the default 4-week ramp."""
    monkeypatch.setattr(QG, "z3_rule", lambda: {**RULE, "weeks": 2})
    ds = _ds(_weeks(2))
    lv = DL.level(ds, MON)
    assert (lv["level"], lv["week"], lv["need"], lv["survey"]) == (2, 3, 2, False)
    assert CS.week_context(ds, MON, exp={"runs_per_week": 5, "minutes_per_run": 60, "at": AT}) is None


def test_level_one_base_is_the_larger_of_survey_and_actual(monkeypatch):
    """等級 1: weekly base = max(questionnaire as entered, actual) — the actual 4 h beats a reported 1.5 h
    (the other way round: test_cold_start.test_ramp_keeps_the_start_level_as_the_base)."""
    _survey(monkeypatch, {"runs_per_week": 3, "minutes_per_run": 30, "at": AT})
    nxt = MON + dt.timedelta(weeks=1)
    done = [_run(MON + dt.timedelta(days=k), 60.0) for k in (0, 2, 4, 6)]          # 4 h in week 1
    wp = _week(_ds(done, nxt), nxt)
    assert wp["data_level"]["level"] == 1 and wp["data_level"]["week"] == 2
    assert wp["cold_start"]["hours"] == pytest.approx(1.5)
    assert wp["target"]["hours"] >= 4.0 - 1e-6


@pytest.mark.parametrize("exp", [None, {"runs_per_week": 6, "minutes_per_run": 120, "longest_min": 180, "at": AT}])
def test_after_the_upgrade_the_questionnaire_no_longer_moves_the_volume(monkeypatch, exp):
    """4 good weeks → 等級 2 on the next Monday: no cold start, no questionnaire — a reported 12 h a week
    gives the same week as no answer at all."""
    ds_ = lambda: _ds(_weeks(4))
    _survey(monkeypatch, None)
    base = _week(ds_())
    _survey(monkeypatch, exp)
    wp = _week(ds_())
    assert wp["data_level"]["level"] == 2 and wp["cold_start"] is None
    assert wp["target"]["hours"] == pytest.approx(base["target"]["hours"])
    assert not any(n.get("src") == "cold_start" for n in wp["notes"])
    # one week earlier (3 good weeks, week 4 of 4): the questionnaire still stands in
    prev = MON - dt.timedelta(weeks=1)
    w4 = _week(_ds(_weeks(3, end=prev), prev), prev)
    assert w4["data_level"]["level"] == 1 and w4["cold_start"] is not None and w4["cold_start"]["week"] == 4


def test_plan_line_text_and_translation():
    ctx = {**CS.start_level({"runs_per_week": 3, "minutes_per_run": 40, "at": AT}), "level": 1, "week": 2, "need": 4}
    assert CS.note(ctx, hr_prior=True)["text"] == "你的資料還在累積（第 2 週／4）：週量依你填的問卷，心率區間是推估"
    assert CS.note(ctx)["text"] == "你的資料還在累積（第 2 週／4）：週量依你填的問卷"
    dflt = {**CS.start_level(None), "level": 1, "week": 3, "need": 4}
    assert CS.note(dflt, hr_prior=True)["text"] == "你的資料還在累積（第 3 週／4）：週量從預設的每週 1.5 小時起算，心率區間是推估"
    own = {"level": 1, "week": 7, "need": 4, "runs": 3, "weeks_ok": 1, "survey": False}
    assert "現在 1/4 週" in DL.line(own) and DL.line({**own, "level": 2}) is None
    from backend.i18n import use_locale
    with use_locale("en"):
        for t in (CS.note(ctx, hr_prior=True)["text"], CS.note(dflt)["text"], DL.line(own, hr_prior=True),
                  DL.public(own)["label"]):
            assert t and not re.search("[一-鿿]", t), t


def test_feasibility_reads_the_same_level(monkeypatch):
    from backend.engine import race_feasibility as RF
    from backend.tests.test_race_feasibility import ev
    monkeypatch.setattr(RF, "activity_rows", lambda ds, today, days=42: [])
    plan = Plan(events=[ev(eid="a", start="2027-03-06")])
    for ds in (_ds(), _ds(_weeks(2)), _ds(_weeks(4))):
        out = RF.races(plan, ds, MON, predict=lambda e, c=None: None, gpx=lambda e: None, finish=lambda e, c: None)
        assert out[0]["data_level"]["level"] == DL.level(ds, MON)["level"]
    # no level (an error reading it): today's behaviour, no level in the card
    monkeypatch.setattr(DL, "level", lambda *a, **k: 1 / 0)
    out = RF.races(plan, _ds(), MON, predict=lambda e, c=None: None, gpx=lambda e: None, finish=lambda e, c: None)
    assert out[0]["data_level"] is None


def test_plan_and_status_read_the_same_level(monkeypatch):
    _survey(monkeypatch, None)
    for ds_ in (lambda: _ds(), lambda: _ds(_weeks(3)), lambda: _ds(_weeks(4))):
        wp = _week(ds_())
        ds = ds_()
        ds.plan = Plan()
        st = Status(ds, ds.plan, MON, prefs=PP.Prefs()).compute()
        d = st.to_dict()["data_level"]
        assert (d["level"], d["week"]) == (wp["data_level"]["level"], wp["data_level"]["week"])
        card = next((i for i in st.indicators if i.id == "level"), None)
        if d["level"] == 2:
            assert card is None                                    # nothing to say at level 2
        else:
            assert card.level == "info" and card.text == f"等級 {d['level']}：{d['label']}"
            assert card.verdict == DL.line(d, "default", CS.DEFAULT_HOURS)
            assert "設定 → 個人資料 → 跑步經驗" in card.action
