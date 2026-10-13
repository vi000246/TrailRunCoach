"""SP-467 (SP-449 Q2 ③ B): 設定 → 門檻變更紀錄 — which threshold changed on which day, from what to
what, and where it came from (手動／測試／PD 擬合／心率模型 …), so a 課表 session whose zones differ
from the last one of its kind can be traced to its cause. No new session format: the entries are
read from what is stored (plan rows, the Stryd PD fits, the as-of LTHR estimates, the watch
account's history); the 課表心率區間 switch had no history and is recorded from now on.
Synthetic data only (temp FIT files, an in-memory DB, a temp plan.json)."""
from __future__ import annotations

import datetime as dt
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.engine import planning as PL
from backend.engine import threshold_history as TH

STATIC = Path(__file__).resolve().parents[1] / "static"
TODAY = dt.date(2026, 10, 13)
T = PL.Threshold


def _ds(tmp_path, days=None, plan=None):
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    utc = timezone.utc
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    days = days or [dt.date(2026, 8, 1) + dt.timedelta(days=i) for i in range(0, 72, 4)]   # 8/1 … 10/10
    for i, day in enumerate(days):
        (d / f"{i:03d}.fit").write_bytes(build_run(start=datetime(day.year, day.month, day.day, 6, tzinfo=utc),
                                                   seconds=1800, power=180, stryd=True))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=TODAY,
                          estimate_thresholds=False, tz=utc)
    ds.plan = plan or PL.Plan()
    return ds


def _fit(day, cp):
    return {"date": day, "cp": cp, "frc_j": 15000.0, "runs": 20, "n_points": 40}


def _of(entries, key):
    return [e for e in entries if e["key"] == key]


# ---------------------------------------------------------------------------
# what the entries are made of
# ---------------------------------------------------------------------------

def test_pd_fits_are_listed_with_their_date_and_from_to(tmp_path):
    ds = _ds(tmp_path)
    ds._cp_est = [_fit(dt.date(2026, 8, 31), 175.4), _fit(dt.date(2026, 9, 30), 191.04),
                  _fit(dt.date(2026, 10, 10), 191.04)]          # the last run's day, same value: no change
    out = TH.build(PL.Plan(), ds, TODAY)
    cp = _of(out["entries"], "cp")
    assert [(e["date"], e["from"], e["to"], e["source"]) for e in cp] == [
        ("2026-09-30", 175.4, 191.0, "pd_fit"), ("2026-08-31", None, 175.4, "pd_fit")]   # newest first
    assert all(e["in_effect"] and e["unit"] == "W" and e["name"] == "CP" for e in cp)
    assert cp[0]["source_label"] == "PD 擬合"


def test_a_plan_cp_wins_and_a_later_fit_says_the_plan_did_not_take_it(tmp_path):
    plan = PL.Plan(thresholds=[T("2026-09-15", cp=200.0, cp_manual=True, note="typed")])
    ds = _ds(tmp_path, plan=plan)
    ds._cp_est = [_fit(dt.date(2026, 8, 31), 175.4), _fit(dt.date(2026, 9, 30), 191.0)]
    cp = _of(TH.build(plan, ds, TODAY)["entries"], "cp")
    by_day = {e["date"]: e for e in cp}
    assert (by_day["2026-09-15"]["from"], by_day["2026-09-15"]["to"]) == (175.4, 200.0)
    assert by_day["2026-09-15"]["source"] == "manual" and by_day["2026-09-15"]["in_effect"]
    fit = by_day["2026-09-30"]
    assert fit["source"] == "pd_fit" and fit["in_effect"] is False and (fit["from"], fit["to"]) == (175.4, 191.0)
    assert "200" in fit["note"]                   # 「課表仍用 200 W」: why this fit changed nothing


def test_a_cp_test_row_is_a_test_with_its_method(tmp_path):
    plan = PL.Plan(thresholds=[T("2026-09-30", cp=196.0, cp_method="pd_model", note="CP 測試")])
    ds = _ds(tmp_path, plan=plan)
    ds._cp_est = [_fit(dt.date(2026, 8, 31), 175.4)]
    e = next(e for e in TH.build(plan, ds, TODAY)["entries"] if e["key"] == "cp" and e["date"] == "2026-09-30")
    assert e["source"] == "test" and (e["from"], e["to"]) == (175.4, 196.0)
    assert "PD 模型" in e["detail"]


def test_lthr_estimates_and_plan_rows(tmp_path):
    from backend.engine.wko5expr.fitdataset import NOT_BEFORE, SETTING_LABELS
    plan = PL.Plan(thresholds=[T("2026-10-05", lthr=155.0, lthr_method="estimate"),
                               T("2026-10-12", lthr=158.0, lthr_method="friel30")])
    ds = _ds(tmp_path, plan=plan)
    ds.athlete.settings["runthr"] = [NOT_BEFORE, (dt.date(2026, 8, 31), 160.0), (dt.date(2026, 9, 30), 160.0),
                                     (dt.date(2026, 10, 8), 163.0)]
    ds._setting_labels["runthr"] = SETTING_LABELS["estimate"]
    lt = _of(TH.build(plan, ds, TODAY)["entries"], "lthr")
    got = [(e["date"], e["from"], e["to"], e["source"], e["in_effect"]) for e in lt]
    assert got == [("2026-10-12", 155.0, 158.0, "test", True),
                   ("2026-10-08", 160.0, 163.0, "auto", False),       # the applied 155 still wins
                   ("2026-10-05", 160.0, 155.0, "estimate", True),
                   ("2026-08-31", None, 160.0, "auto", True)]         # 9/30 = 160 again: no entry
    assert lt[0]["detail"] == "30 分鐘測試" and lt[2]["source_label"] == "套用的自動估算"
    assert lt[1]["detail"] == SETTING_LABELS["estimate"]      # how the Dataset labels its dated LTHR


def test_aet_and_max_and_resting_hr_rows(tmp_path):
    plan = PL.Plan(thresholds=[T("2026-09-01", aethr=140.0, aethr_method="test"),
                               T("2026-10-01", aethr=145.0, aethr_method="manual", mhr=190.0, mhr_method="test"),
                               T("2026-10-02", rhr=48.0),
                               T("2999-01-01", aethr=150.0)])                    # not in effect yet: not listed
    ds = _ds(tmp_path, plan=plan)
    e = TH.build(plan, ds, TODAY)["entries"]
    assert [(x["date"], x["from"], x["to"], x["source"]) for x in _of(e, "aethr")] == [
        ("2026-10-01", 140.0, 145.0, "manual"), ("2026-09-01", None, 140.0, "test")]
    assert [(x["date"], x["to"], x["source"]) for x in _of(e, "mhr")] == [("2026-10-01", 190.0, "test")]
    assert [(x["date"], x["to"], x["source"]) for x in _of(e, "rhr")] == [("2026-10-02", 48.0, "manual")]


def test_rows_before_the_first_run_and_repeated_values(tmp_path):
    """A plan row dated before the first run (nothing to look the 課表's value up on) reads from the
    plan row before it and is in effect; a row repeating the value before it is no change."""
    plan = PL.Plan(thresholds=[T("2025-10-20", cp=240.0, lthr=165.0, mhr=188.0),
                               T("2026-05-16", cp=255.0, lthr=165.0, mhr=188.0, aethr=149.0),
                               T("2026-10-03", aethr=149.0, aethr_method="test")])
    ds = _ds(tmp_path, plan=plan)                         # runs from 2026-08-01
    e = TH.build(plan, ds, TODAY)["entries"]
    assert [(x["date"], x["from"], x["to"], x["in_effect"]) for x in _of(e, "cp")] == [
        ("2026-05-16", 240.0, 255.0, True), ("2025-10-20", None, 240.0, True)]
    assert [x["date"] for x in _of(e, "lthr")] == ["2025-10-20"]
    assert [x["date"] for x in _of(e, "mhr")] == ["2025-10-20"]
    assert [x["date"] for x in _of(e, "aethr")] == ["2026-05-16"]          # the 10/03 test: 149 again


def test_watch_account_changes_are_listed():
    hist = [{"at": "2026-09-01T01:00:00+00:00", "lthr": 170, "max_hr": 190, "rest_hr": 50, "ratios": None},
            {"at": "2026-09-20T01:00:00+00:00", "lthr": 170, "max_hr": 190, "rest_hr": 50,
             "ratios": {"hrr": [0.6, 0.7, 0.8, 0.88, 0.95]}},
            {"at": "2026-10-03T01:00:00+00:00", "lthr": 152, "max_hr": 185, "rest_hr": 50,
             "ratios": {"hrr": [0.6, 0.7, 0.8, 0.88, 0.95]}}]
    out = TH.build(PL.Plan(), None, TODAY, coros_history=hist)
    w = [e for e in out["entries"] if e["source"] == "watch"]
    assert [(e["date"], e["key"], e["from"], e["to"]) for e in w] == [
        ("2026-10-03", "mhr", 190, 185), ("2026-09-20", "hr_zones", None, None)]
    assert any("2026-09-01" in n for n in out["notes"])      # 「手錶帳號的心率設定從 2026-09-01 起有紀錄」


def test_hr_model_switches_from_the_journal_and_a_note_says_older_ones_are_not_there():
    hist = [{"date": "2026-10-13", "from": "lthr", "to": "hrr"}]
    out = TH.build(PL.Plan(), None, TODAY, model_history=hist, model_now="hrr")
    m = _of(out["entries"], "hr_model")
    assert [(e["date"], e["from"], e["to"], e["source"]) for e in m] == [("2026-10-13", "lthr", "hrr", "hr_model")]
    assert m[0]["from_label"] and m[0]["to_label"] and m[0]["source_label"] == "心率模型"
    assert any("心率區間" in n and "之前" in n for n in out["notes"])


def test_the_history_never_refits_the_pd_model(tmp_path, monkeypatch):
    """設定頁 must not refit: the entries read the fits already on the Dataset."""
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    ds = _ds(tmp_path)
    ds._cp_est = [_fit(dt.date(2026, 8, 31), 175.4)]

    def boom(*a, **k):
        raise AssertionError("refit")
    monkeypatch.setattr(A, "pd_model", boom)
    monkeypatch.setattr(FitFolderDataset, "stryd_cp_fit", boom)
    monkeypatch.setattr(FitFolderDataset, "_estimate_settings", boom)
    assert _of(TH.build(PL.Plan(), ds, TODAY)["entries"], "cp")


def test_a_changed_easy_band_has_an_entry_between_the_two_sessions(tmp_path):
    """驗收 2: two easy runs of the 課表 with different power bands — the log has the change
    between their days, with the value the later one used."""
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.zones import training_targets
    ds = _ds(tmp_path)
    ds._cp_est = [_fit(dt.date(2026, 8, 31), 175.4), _fit(dt.date(2026, 9, 30), 191.0)]
    a, b = dt.date(2026, 9, 20), dt.date(2026, 10, 7)

    def easy(day):
        tt = training_targets(ds, int(date_to_day(day)))
        return next(r for r in tt["rows"] if r["id"] == "z2")["power"]
    assert easy(a) != easy(b)
    hit = [e for e in TH.build(PL.Plan(), ds, TODAY)["entries"]
           if a.isoformat() < e["date"] <= b.isoformat() and e["in_effect"]]
    assert [(e["key"], e["to"]) for e in hit] == [("cp", 191.0)]


# ---------------------------------------------------------------------------
# recording the 課表心率區間 switch (it had no history)
# ---------------------------------------------------------------------------

@pytest.fixture
def hr_api(tmp_path, monkeypatch):
    from backend.api import plan as API
    from backend.engine import plan_auto as PA
    path = tmp_path / "plan.json"
    load, save = PL.Plan.load.__func__, PL.Plan.save
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, p=path: load(cls, p)))
    monkeypatch.setattr(PL.Plan, "save", lambda self, p=path: save(self, p))
    monkeypatch.setattr(API, "_estimate_dataset", lambda: None)
    monkeypatch.setattr(API, "hr_profile_view", lambda ds, today: {"ok": True})
    monkeypatch.setattr(API, "today_local", lambda: TODAY)
    monkeypatch.setattr(PA, "after_thresholds", lambda: None)
    return API


def test_switching_the_hr_model_is_recorded_once_per_change(hr_api):
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import make_db, run
    API = hr_api

    async def go():
        db = await make_db(logged_in=False)
        await API.put_hr_profile(API.HrProfileIn(model="hrr"), db)
        await API.put_hr_profile(API.HrProfileIn(model="hrr"), db)       # same model: nothing new
        await API.put_hr_profile(API.HrProfileIn(model="lthr"), db)
        return await SettingsRepository(db, 1).get(TH.MODEL_HISTORY_KEY)
    assert run(go()) == [{"date": "2026-10-13", "from": "lthr", "to": "hrr"},
                         {"date": "2026-10-13", "from": "hrr", "to": "lthr"}]


def test_the_model_history_setting_is_validated():
    from backend.settings.repository import validate
    validate(TH.MODEL_HISTORY_KEY, None)
    validate(TH.MODEL_HISTORY_KEY, [{"date": "2026-10-13", "from": "lthr", "to": "hrr"}])
    for bad in ([{"date": "x", "from": "lthr", "to": "hrr"}], [{"date": "2026-10-13", "from": "a", "to": "hrr"}],
                {"date": "2026-10-13"}):
        with pytest.raises(ValueError):
            validate(TH.MODEL_HISTORY_KEY, bad)


# ---------------------------------------------------------------------------
# the endpoint and the settings page
# ---------------------------------------------------------------------------

def test_endpoint_lists_the_entries(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as API
    plan = PL.Plan(thresholds=[T("2026-10-05", lthr=155.0, lthr_method="estimate")])
    path = tmp_path / "plan.json"
    plan.save(path)
    load = PL.Plan.load.__func__
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, p=path: load(cls, p)))
    ds = _ds(tmp_path, plan=plan)
    ds._cp_est = [_fit(dt.date(2026, 8, 31), 175.4)]
    monkeypatch.setattr(API, "_estimate_dataset", lambda: ds)
    monkeypatch.setattr(API, "_estimate_pending", lambda ds: None)
    monkeypatch.setattr(API, "today_local", lambda: TODAY)
    monkeypatch.setattr(API, "_history_settings", lambda: (None, None, "lthr"))
    app = FastAPI()
    app.include_router(API.router)
    d = TestClient(app).get("/api/v1/plan/threshold-history").json()
    assert d["today"] == "2026-10-13" and d["pending"] is None
    assert {(e["key"], e["date"], e["source"]) for e in d["entries"]} >= {("lthr", "2026-10-05", "estimate"),
                                                                       ("cp", "2026-08-31", "pd_fit")}
    assert d["notes"]


def test_settings_page_has_the_history_in_the_thresholds_section():
    s = (STATIC / "settings.html").read_text("utf-8")
    sec = s[s.index('<section id="thresholds">'):]
    sec = sec[:sec.index("</section>")]
    assert 'id="thr-hist"' in sec and 'id="thr-hist-body"' in sec
    assert '"/api/v1/plan/threshold-history"' in s
    # every key the page uses exists in both catalogs
    import json
    keys = set(re.findall(r'"settings\.(thr\.hist[\w.]*)"', s)) | set(re.findall(r':settings\.(thr\.hist[\w.]*)', s))
    keys |= set(re.findall(r'data-i18n="settings\.(thr\.hist[\w.]*)"', s))
    assert keys
    for lang in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / lang / "settings.json").read_text("utf-8"))
        assert keys <= set(cat), (lang, keys - set(cat))


def test_the_labels_have_english():
    import json
    en = json.loads((Path(__file__).resolve().parents[1] / "i18n" / "locales" / "en.json").read_text("utf-8"))
    for msgid in [*TH.SOURCES.values(), *TH.NAMES.values(), *TH.NOTE_MSGIDS]:
        if re.search(r"[一-鿿]", msgid):
            assert en.get(msgid), msgid
