"""沒有 LTHR 時的心率先驗 (SP-289): max HR's last layer 208 − 0.7 × age (Tanaka), the LTHR prior
0.90 × max HR (labelled 推估, low confidence), the easy runs' HR numbers + the talk test, the prior
left out once a test / estimate exists (and the PMC note on that day), and the week the LTHR test is
first suggested. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import types
from types import SimpleNamespace

import pytest

from backend.engine import cold_start as CS
from backend.engine import experience as EX
from backend.engine import hr_profile as HP
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import threshold_confidence as TC
from backend.engine.planning import Plan, Threshold
from backend.engine.status import Status
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

MON = dt.date(2026, 10, 5)
AT = "2026-10-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _no_account(monkeypatch):
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    monkeypatch.setattr(HP, "plan_model", lambda user_id=1: "lthr")
    monkeypatch.setattr(EX, "load", lambda user_id=1: None)
    from backend.engine import race_results as RR
    monkeypatch.setattr(RR, "load", lambda user_id=1: [])


def _ds(plan: Plan, workouts=(), today=MON):
    ds = FakeDataset(list(workouts), today, settings={})
    ds.config = types.SimpleNamespace(parity=False)
    ds.setting_label = lambda name, default="": default
    ds.settings_from = "app"
    ds.aethr = lambda w: None
    ds.cp = lambda w: None
    ds.mftp_run = None
    ds.plan = plan
    return ds


def _week(ds, today=MON):
    st = Status(ds, ds.plan, today, prefs=PP.Prefs()).compute()
    return O.week_plan(ds, st, today)


def test_no_threshold_at_all_gives_no_numbers():
    ds = _ds(Plan())
    m = HP.max_hr(ds, MON)
    assert m["value"] is None and HP.lthr_prior(ds, MON) is None
    from backend.engine.zones import training_targets
    tt = training_targets(ds, int(ds.today))
    assert tt["lthr"] is None and not tt["lthr_prior"]
    wp = _week(ds)
    easy = [s for s in wp["sessions"] if s["kind"] == "easy"]
    assert easy and not any("說話測試" in s["detail"] for s in easy)


def test_age_only_tanaka_then_lthr_prior_with_the_talk_test():
    ds = _ds(Plan(profile={"birth_year": MON.year - 40}))
    m = HP.max_hr(ds, MON)
    assert m["value"] == 180.0 and m["kind"] == "age"                     # 208 − 0.7 × 40
    assert m["source"] == "推估（年齡公式，個人可以差 10 bpm 以上）"
    p = HP.lthr_prior(ds, MON)
    assert p["value"] == 162.0 and p["source"] == "推估（最大心率的 90 %）"
    wp = _week(ds)
    th = wp["thresholds"]
    assert th["lthr"] == 162.0 and th["lthr_prior"] and th["lthr_source"] == "推估（最大心率的 90 %）"
    easy = [s for s in wp["sessions"] if s["kind"] == "easy"]
    assert easy and all("bpm" in s["target"] for s in easy)               # HR numbers now
    assert th["aet"] == pytest.approx(0.90 * 162, abs=1)                  # Z2 top = 0.90 × LTHR ≈ 81 % HRmax
    assert all("能完整講一句話的強度" in s["detail"] for s in easy)
    # low confidence from the start
    li = TC.lthr_info(ds, ds.plan, MON)
    assert li["source_kind"] == "prior" and li["value"] == 162.0
    mi = TC.mhr_info(ds, MON)
    assert mi["source_kind"] == "age"
    r = TC.assess(li, mi, {"value": None}, MON, [], {})
    assert r["lthr"]["confidence"] == "low" and r["hrmax"]["confidence"] == "low"
    # the walk cap keeps its own 220 − age rule (SP-115)
    assert HP.walk_cap_for(ds, MON, None)["basis"] == "age"


def test_max_hr_only_and_the_order():
    plan = Plan(profile={"birth_year": MON.year - 40}, thresholds=[Threshold("2026-09-01", mhr=190)])
    ds = _ds(plan)
    assert HP.max_hr(ds, MON)["kind"] == "manual" and HP.lthr_prior(ds, MON)["value"] == 171.0
    # the watch's max HR before the age formula
    acc = {"max_hr": 200}
    assert HP.max_hr(_ds(Plan(profile={"birth_year": 1986})), MON, acc)["value"] == 200.0
    assert HP.age_max_hr(30) == 187.0 and HP.age_max_hr(None) is None and HP.age_max_hr(5) is None


def test_a_test_wins_over_the_prior():
    plan = Plan(profile={"birth_year": MON.year - 40}, thresholds=[Threshold("2026-09-01", lthr=170)])
    ds = _ds(plan, [FakeWorkout(start=dt.datetime(2026, 9, 20, 7), sport="run", sport_type="running",
                                metrics={"duration": 1800.0})])
    ds.sport_setting = lambda kind, w: 170.0
    from backend.engine.zones import training_targets
    tt = training_targets(ds, int(ds.today))
    assert tt["lthr"] == 170.0 and not tt["lthr_prior"]
    li = TC.lthr_info(ds, plan, MON)
    assert li["source_kind"] in ("manual", "test") and li["value"] == 170.0


def _fit_stub(runs_from: dt.date, real=(), plan_rows=(), watch=False):
    from backend.engine.wko5expr.fitdataset import NOT_BEFORE
    ws = [SimpleNamespace(sport="run", entry=SimpleNamespace(start=dt.datetime.combine(runs_from, dt.time(7))))]
    return SimpleNamespace(
        workouts=ws, athlete=SimpleNamespace(settings={"runthr": [NOT_BEFORE] + list(real)} if real else {}),
        settings_ignored=[{"field": "lthr", "value": 180, "date": "2026-05-15"}] if watch else [],
        _setting_labels={}, plan=Plan(thresholds=[Threshold(d, lthr=165) for d in plan_rows]), memo={},
        today=FakeDataset([], MON).today, lthr_prior=None)


def test_fit_dataset_prior_until_the_first_real_lthr(monkeypatch):
    from backend.engine.wko5expr.fitdataset import NOT_BEFORE, SETTING_LABELS, FitFolderDataset
    monkeypatch.setattr(HP, "lthr_prior", lambda ds, day, *a, **k: {"value": 162.0, "mhr": 180.0, "mhr_kind": "age",
                                                                      "mhr_source": "x", "source": "y"})
    apply = FitFolderDataset._apply_lthr_prior
    first = dt.date(2026, 8, 1)
    # nothing else: open-ended prior from the first run, labelled
    ds = _fit_stub(first)
    assert apply(ds) and ds.athlete.settings["runthr"] == [NOT_BEFORE, (first, 162.0)]
    assert ds._setting_labels["runthr"] == SETTING_LABELS["prior"] and ds.lthr_prior["until"] is None
    # an estimate from 2026-09-01: the prior only before it; the PMC notes the switch that day
    est = [(dt.date(2026, 9, 1), 168.0)]
    ds = _fit_stub(first, est)
    assert apply(ds) and ds.athlete.settings["runthr"] == [NOT_BEFORE, (first, 162.0)] + est
    assert ds.lthr_prior["until"] == "2026-09-01" and ds.lthr_prior["until_kind"] == "estimate"
    assert "runthr" not in ds._setting_labels
    marks = O.pmc_marks(ds, dt.date(2026, 8, 20), dt.date(2026, 9, 20))
    assert marks and marks[0]["date"] == "2026-09-01" and "你自己資料的估算" in marks[0]["text"]
    assert O.pmc_marks(ds, dt.date(2026, 9, 2), dt.date(2026, 9, 20)) == []
    # a plan test earlier than the estimate ends it there
    ds = _fit_stub(first, est, plan_rows=["2026-08-15"])
    assert apply(ds) and ds.lthr_prior["until"] == "2026-08-15" and ds.lthr_prior["until_kind"] == "test"
    # a watch LTHR (the owner's case): untouched — the P7 rule governs
    ds = _fit_stub(first, est, watch=True)
    assert not apply(ds) and ds.athlete.settings["runthr"][1:] == est and ds.lthr_prior is None
    # a real LTHR from the first run on: no prior
    ds = _fit_stub(first, [(first, 168.0)])
    assert not apply(ds) and ds.lthr_prior is None


def _runs_weeks(n_weeks: int, end_monday: dt.date):
    ws = []
    for k in range(n_weeks, 0, -1):
        mon = end_monday - dt.timedelta(weeks=k)
        for i in (0, 2, 4):
            ws.append(FakeWorkout(start=dt.datetime.combine(mon + dt.timedelta(days=i), dt.time(7)), sport="run",
                                  sport_type="running", tags=["running"], metrics={"duration": 1800.0}))
    return ws


def test_lthr_test_is_suggested_from_week_5_for_a_new_runner(monkeypatch):
    ds0 = _ds(Plan())
    assert CS.lthr_test_from(ds0, MON) == (MON + dt.timedelta(weeks=4)).isoformat()
    # data started 2 weeks ago: week 3 now, the test from week 5
    ds = _ds(Plan(), _runs_weeks(2, MON))
    start = MON - dt.timedelta(weeks=2)
    assert CS.lthr_test_from(ds, MON) == (start + dt.timedelta(weeks=4)).isoformat()
    # ≥ 3 h a week and a race result: from week 2
    from backend.engine import race_results as RR
    exp = {"runs_per_week": 4, "minutes_per_run": 50, "at": AT}
    races = [RR.make(10, 2900, "2026-06-01")]
    assert CS.lthr_test_week(exp, races, MON) == 2
    assert CS.lthr_test_week(exp, [], MON) == 5 and CS.lthr_test_week({**exp, "minutes_per_run": 40}, races, MON) == 5
    assert CS.lthr_test_from(ds, MON, exp, races, load=False) == (start + dt.timedelta(weeks=1)).isoformat()
    # a runner with history: no limit
    assert CS.lthr_test_from(_ds(Plan(), _runs_weeks(5, MON)), MON) is None
    # assess drops the LTHR test before that day, keeps it after
    li = {"value": 162.0, "source_kind": "prior", "test_from": (MON + dt.timedelta(days=7)).isoformat()}
    mhr = {"value": 180.0, "source_kind": "test"}
    assert not [s for s in TC.assess(li, mhr, {}, MON, [], {})["suggestions"] if "tt30" in s["tests"]]
    later = MON + dt.timedelta(days=8)
    assert [s for s in TC.assess(li, mhr, {}, later, [], {})["suggestions"] if "tt30" in s["tests"]]
    assert TC.lthr_info(ds, ds.plan, MON)["test_from"] == (start + dt.timedelta(weeks=4)).isoformat()


def test_switch_note_is_translated():
    import re
    from backend.i18n import use_locale
    with use_locale("en"):
        for k in O.LTHR_SWITCH_NOTE.values():
            from backend.i18n import _
            assert not re.search("[一-鿿]", _(k))
        for k in (HP.AGE_MHR_SOURCE, HP.LTHR_PRIOR_SOURCE, HP.TALK_TEST):
            assert not re.search("[一-鿿]", _(k))
