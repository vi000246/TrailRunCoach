"""SP-874: the Dataset's daily LTHR (fitdataset._estimate_settings → runthr: hrTSS, HR zones, the 課表's
HR targets) leaves out a grid day's automatic LTHR estimate the runs were too easy for — the settings
card's rule (SP-749, threshold_confidence.estimate_low_effort: < 85 % of the runner's own max HR or
< 80 % of the HR reserve), judged against that grid day's max / resting HR (owner 2026-10-21, Q1 ①a ②a):
the previous believable value goes on; none before it → the watch LTHR, else 0.90 × max HR. Runners
without a low estimate, and the days a plan LTHR test covers, keep their LTHR and HR load.
Synthetic data only (stubs, FIT files written by fit_builder)."""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.engine import hr_profile as HP
from backend.engine import threshold_confidence as TC
from backend.engine.planning import Plan, Threshold
from backend.engine.wko5expr.fitdataset import NOT_BEFORE, SETTING_LABELS, FitFolderDataset

FIRST_RUN = dt.date(2026, 1, 1)
D1, D2, D3 = dt.date(2026, 2, 1), dt.date(2026, 3, 1), dt.date(2026, 4, 1)
MHR, RHR = 185.0, 50.0                      # 150 = 81 % HRmax, 74 % HRR (low); 170 = 92 %, 89 % (fine)
WATCH = {"field": "lthr", "value": 166, "date": "2026-05-15"}


@pytest.fixture(autouse=True)
def _hr(monkeypatch):
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    monkeypatch.setattr(HP, "max_hr", lambda ds, day, acc=None, use_account=True:
                        {"value": MHR, "kind": "estimate", "source": "x"})
    monkeypatch.setattr(HP, "rest_hr", lambda ds, day, acc=None, use_account=True:
                        {"value": RHR, "kind": "manual", "source": "x"})


def _stub(est, watch=False, label="estimate"):
    ws = [SimpleNamespace(sport="run", entry=SimpleNamespace(start=dt.datetime.combine(FIRST_RUN, dt.time(7))))]
    from backend.engine.wko5expr.fitdataset import default_athlete
    ath = default_athlete()
    ath.settings = {"runthr": [NOT_BEFORE] + list(est)} if est else {}
    return SimpleNamespace(
        workouts=ws, athlete=ath, settings_ignored=[dict(WATCH)] if watch else [],
        _setting_labels={"runthr": SETTING_LABELS[label]} if est else {}, plan=Plan(), memo={"x": 1},
        today=0, lthr_prior=None, lthr_low_effort=[],
        _coros_lthr_prior=lambda: None)


def _drop(ds):
    return FitFolderDataset._drop_low_effort_lthr(ds)


def _on(ds, day):
    return ds.athlete.setting_on("runthr", day)


def test_the_rule_is_the_settings_cards():
    """One rule for the card (SP-749) and the Dataset: the same signals, the same exceptions."""
    mhr, rhr = {"value": MHR, "source_kind": "estimate"}, {"value": RHR}
    assert TC.estimate_low_effort(150, mhr, rhr) and TC.estimate_effort_warning(150, mhr, rhr)
    assert TC.estimate_low_effort(170, mhr, rhr) == [] and TC.estimate_effort_warning(170, mhr, rhr) is None
    assert TC.estimate_low_effort(150, {"value": MHR, "source_kind": "age"}, rhr) == []      # the age formula
    assert TC.estimate_low_effort(150, {"value": None}, rhr) == []                          # no max HR
    w = TC.estimate_effort_warning(150, mhr, rhr)
    assert w["signals"] == [s["text"] for s in TC.estimate_low_effort(150, mhr, rhr)]


def test_a_low_grid_day_keeps_the_previous_believable_value():
    ds = _stub([(D1, 170.0), (D2, 150.0), (D3, 172.0)])
    out = _drop(ds)
    assert out == [{"date": D2.isoformat(), "value": 150.0}] and ds.lthr_low_effort == out
    assert ds.athlete.settings["runthr"] == [NOT_BEFORE, (D1, 170.0), (D3, 172.0)]
    assert _on(ds, D2 + dt.timedelta(days=10)) == 170.0 and _on(ds, D3) == 172.0
    assert ds._setting_labels["runthr"] == SETTING_LABELS["estimate"] and ds.memo == {}


def test_each_grid_day_is_judged_on_its_own_max_hr(monkeypatch):
    """Q1 ②a: a max HR measured higher later does not make an early estimate low."""
    seen = []

    def max_hr(ds, day, acc=None, use_account=True):
        seen.append(day)
        return {"value": 170.0 if day < D3 else 200.0, "kind": "estimate", "source": "x"}
    monkeypatch.setattr(HP, "max_hr", max_hr)
    ds = _stub([(D1, 150.0), (D3, 165.0)])         # 150 / 170 = 88 %; 165 / 200 = 82.5 % (low that day)
    out = _drop(ds)
    assert seen == [D1, D3]
    assert out == [{"date": D3.isoformat(), "value": 165.0}]
    assert ds.athlete.settings["runthr"] == [NOT_BEFORE, (D1, 150.0)]


def test_low_from_the_start_without_a_watch_lthr_falls_to_the_prior(monkeypatch):
    ds = _stub([(D1, 150.0), (D2, 170.0)])
    _drop(ds)
    assert ds.athlete.settings["runthr"] == [NOT_BEFORE, (D2, 170.0)]
    monkeypatch.setattr(HP, "lthr_prior", lambda ds, day, *a, **k: {"value": 166.0, "mhr": MHR, "mhr_kind": "estimate",
                                                                      "mhr_source": "x", "source": "y"})
    assert FitFolderDataset._apply_lthr_prior(ds)
    assert ds.athlete.settings["runthr"] == [NOT_BEFORE, (FIRST_RUN, 166.0), (D2, 170.0)]
    assert ds.lthr_prior["until"] == D2.isoformat() and _on(ds, D1) == 166.0


def test_low_from_the_start_with_a_watch_lthr_uses_it_from_the_first_left_out_day():
    ds = _stub([(D1, 150.0), (D2, 170.0)], watch=True)
    _drop(ds)
    assert ds.athlete.settings["runthr"] == [NOT_BEFORE, (D1, 166.0), (D2, 170.0)]
    assert not FitFolderDataset._apply_lthr_prior(ds)                # P7: the watch value governs


def test_all_low_without_a_watch_lthr_is_as_without_an_estimate(monkeypatch):
    ds = _stub([(D1, 150.0), (D2, 151.0)])
    out = _drop(ds)
    assert len(out) == 2 and "runthr" not in ds.athlete.settings and "runthr" not in ds._setting_labels
    monkeypatch.setattr(HP, "lthr_prior", lambda ds, day, *a, **k: {"value": 166.0, "mhr": MHR, "mhr_kind": "estimate",
                                                                      "mhr_source": "x", "source": "y"})
    assert FitFolderDataset._apply_lthr_prior(ds)
    assert ds.athlete.settings["runthr"] == [NOT_BEFORE, (FIRST_RUN, 166.0)] and ds.lthr_prior["until"] is None
    assert ds._setting_labels["runthr"] == SETTING_LABELS["prior"]


def test_all_low_with_a_watch_lthr_uses_it_and_its_label():
    called = []
    ds = _stub([(D1, 150.0), (D2, 151.0)], watch=True)
    ds._coros_lthr_prior = lambda: called.append(1) or ds._setting_labels.update(runthr="watch")
    _drop(ds)
    assert called == [1] and ds._setting_labels["runthr"] == "watch"
    assert ds.athlete.settings["runthr"] == [NOT_BEFORE, (D1, 166.0)]


def test_believable_estimates_and_other_sources_are_left_alone(monkeypatch):
    est = [(D1, 170.0), (D2, 168.0), (D3, 172.0)]
    ds = _stub(est)
    before = ds.athlete.settings["runthr"]
    assert _drop(ds) == [] and ds.athlete.settings["runthr"] is before and ds.memo == {"x": 1}
    # a low value that is not an estimate (the watch LTHR, the prior): not this rule's
    for label in ("prior", "db"):
        ds = _stub([(D1, 150.0)], label=label)
        assert _drop(ds) == [] and ds.athlete.settings["runthr"] == [NOT_BEFORE, (D1, 150.0)]
    # no max HR of the runner's own (only the age formula, or none): kept, as on the card
    monkeypatch.setattr(HP, "max_hr", lambda ds, day, acc=None, use_account=True: {"value": 200.0, "kind": "age"})
    ds = _stub([(D1, 150.0)])
    assert _drop(ds) == [] and ds.athlete.settings["runthr"] == [NOT_BEFORE, (D1, 150.0)]
    monkeypatch.setattr(HP, "max_hr", lambda ds, day, acc=None, use_account=True: {"value": None, "kind": None})
    assert _drop(_stub([(D1, 150.0)])) == []
    # the check failing: kept
    monkeypatch.setattr(HP, "max_hr", lambda *a, **k: 1 / 0)
    ds = _stub([(D1, 150.0)])
    assert _drop(ds) == [] and ds.athlete.settings["runthr"] == [NOT_BEFORE, (D1, 150.0)]


# ---- through a real FIT dataset build: the hrTSS follows ------------------------------------------

def _folder(tmp_path):
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    start = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    for i in range(16):                       # a run every 10 days: grid days 01-31, 03-02, 04-01 …
        (d / f"{i}.fit").write_bytes(build_run(start + dt.timedelta(days=10 * i), seconds=900,
                                               hr=150 + (i % 5), speed_m_s=3.0))
    return tmp_path / "fit" / "coros"


def _build(tmp_path, monkeypatch, lthr_of, plan=None):
    from backend.engine import thresholds as TH
    from backend.engine.racepower import weather as WX
    from backend.engine.wko5expr import dataset as DSM
    from backend.engine.wko5expr.config import EngineConfig
    monkeypatch.setattr(WX, "HOME", tmp_path / "home")
    monkeypatch.setattr(TH, "estimate", lambda ds, today=None, cp_of=None: {"lthr": {"value": lthr_of(today)}})
    monkeypatch.setattr(DSM, "tenant_plan", lambda: plan or Plan())
    return FitFolderDataset(_folder(tmp_path), config=EngineConfig(parity=False), today=dt.date(2026, 6, 1),
                            classifications={}, athlete_settings=[], estimate_thresholds=True)


def _hrtss(ds):
    from backend.engine.wko5expr.fitdataset import F_HRTSS
    return {w.entry.file: w.entry.metrics.get(F_HRTSS) for w in ds.workouts}


def test_a_low_estimate_no_longer_scores_the_hr_load(tmp_path, monkeypatch):
    low = dt.date(2026, 3, 2)
    ds = _build(tmp_path, monkeypatch, lambda d: 150.0 if d == low else 170.0)
    assert ds.lthr_low_effort == [{"date": low.isoformat(), "value": 150.0}]
    grid = [(d, v) for d, v in ds.athlete.settings["runthr"][1:] if d > FIRST_RUN]   # [0]: SP-289's prior
    assert low not in [d for d, _ in grid] and dt.date(2026, 1, 31) in [d for d, _ in grid]
    assert all(v == 170.0 for _, v in grid)
    runs = [w for w in ds.workouts if low <= w.entry.start.date() < dt.date(2026, 4, 1)]
    assert runs
    from backend.engine.wko5expr.fitdataset import F_HRTSS
    for w in runs:
        assert ds.hr_lthr(w) == 170.0
        assert w.entry.metrics[F_HRTSS] == pytest.approx(ds._hr_tss(w, 170.0)[0])
        assert w.entry.metrics[F_HRTSS] < ds._hr_tss(w, 150.0)[0]       # the low LTHR overstated it


def test_without_a_low_estimate_the_lthr_and_hr_load_do_not_change(tmp_path, monkeypatch):
    lthr_of = (lambda d: 168.0 if d.month < 3 else 172.0)
    a = _build(tmp_path / "a", monkeypatch, lthr_of)
    monkeypatch.setattr(FitFolderDataset, "_drop_low_effort_lthr", lambda self: [])   # before SP-874
    b = _build(tmp_path / "b", monkeypatch, lthr_of)
    assert a.lthr_low_effort == []
    assert a.athlete.settings["runthr"] == b.athlete.settings["runthr"]
    assert list(_hrtss(a).values()) == list(_hrtss(b).values()) and any(_hrtss(a).values())


def test_a_plan_lthr_test_still_wins(tmp_path, monkeypatch):
    plan = Plan(thresholds=[Threshold("2026-02-15", lthr=175)])
    ds = _build(tmp_path, monkeypatch, lambda d: 150.0, plan=plan)
    later = [w for w in ds.workouts if w.entry.start.date() >= dt.date(2026, 2, 15)]
    assert later and all(ds.hr_lthr(w) == 175.0 for w in later)
