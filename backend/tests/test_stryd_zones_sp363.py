"""SP-363: Stryd's five running-power zones (% CP) as a choice next to Palladino's.

Where they show: the settings page's power-zone table (a second table at the same CP), the
activity page's and the period page's time-in-zone charts, and the analysis page's power-zone
card (switchable like the HR card). The 課表's power targets and what is pushed to the watch
stay Palladino's (not asked for on the ticket in a way that says how; left for the user)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from backend.engine import zones as Z
from backend.engine.panels import activity_charts as A
from backend.engine.panels import period_zones as PZ

ROOT = Path(__file__).resolve().parents[2]


def test_the_stryd_table_is_stryds_published_one():
    """Easy 65–80, Moderate 80–90, Threshold 90–100 (its top is CP), Interval 100–115,
    Repetition 115–130 % CP; Stryd's own example CP 252 W → Zone 1 164–202 W."""
    assert Z.STRYD_POWER_ZONES == [
        ("1", "Easy", 0.65, 0.80), ("2", "Moderate", 0.80, 0.90), ("3", "Threshold", 0.90, 1.00),
        ("4", "Interval", 1.00, 1.15), ("5", "Repetition", 1.15, 1.30)]
    z1 = Z.zones_json(252, Z.STRYD_POWER_ZONES)[0]
    assert z1["watts"] == [164, 202]
    assert "help.stryd.com" in Z.STRYD_SOURCE and "WKO" not in Z.STRYD_SOURCE
    # the default table is still Palladino's
    assert [r["id"] for r in Z.zones_json(250)] == [r[0] for r in Z.PALLADINO_POWER_ZONES]


def test_the_easy_run_band_is_stryd_zone_1():
    """SP-378 Q1-5: the road easy run's 65–80 % CP is Stryd Zone 1 — the 課表 keeps its bands."""
    z2 = next(r for r in Z.WORKOUT_TARGETS if r[0] == "z2")
    assert (z2[2], z2[3]) == Z.STRYD_POWER_ZONES[0][2:] == Z.EASY_BAND


def test_time_in_zone_counts_every_second():
    rows = Z.stryd_rows()
    assert rows[0][2] == 0.0 and rows[-1][3] is None
    assert [r[2:] for r in rows[1:-1]] == [r[2:] for r in Z.STRYD_POWER_ZONES[1:-1]]


def test_activity_chart_offers_stryd_and_counts_its_zones():
    from backend.tests.test_activity_charts import _ds, _run
    # CP 250: 150 W (60 %) and 180 W (72 %) are Zone 1, 210 W 84 % Z2, 240 W 96 % Z3,
    # 270 W 108 % Z4, 340 W 136 % Z5
    p = np.repeat([150.0, 180.0, 210.0, 240.0, 270.0, 340.0], 300)
    t = np.arange(len(p), dtype=float)
    ds = _ds(_run(t, np.full(len(p), 150.0), p))
    res = A.zone_times(ds, ds.workouts[0], "power")
    assert res["default"] == "palladino"
    st = next(m for m in res["models"] if m["id"] == "stryd")
    assert st["available"] and [r["seconds"] for r in st["rows"]] == [600.0, 300.0, 300.0, 300.0, 300.0]
    assert [(r["from"], r["to"]) for r in st["rows"]] == [
        (0, 200), (200, 225), (225, 250), (250, 288), (288, None)]   # W, rounded
    assert st["total_s"] == len(p)


def test_period_chart_offers_stryd():
    from backend.tests.test_period_zones import B, E, DatedDS, _act, _q, _rows
    d = dt.date(2026, 9, 10)
    p = np.repeat([150.0, 210.0], 300)                              # CP 250: 60 % and 84 %
    ds = DatedDS([_act(d, [140] * 600, p=p)])
    res = PZ.compute(ds, B, E, _q(zkind="power", zmodel="stryd"))
    assert res["model"]["id"] == "stryd" and "Stryd" in res["model"]["source"]
    assert _rows(res) == {"1": 300, "2": 300, "3": 0, "4": 0, "5": 0}
    assert PZ.compute(ds, B, E, _q(zkind="power"))["model"]["id"] == "palladino"   # the default
    from backend.i18n import use_locale
    with use_locale("en"):
        en = PZ.compute(ds, B, E, _q(zkind="power", zmodel="stryd"))
    assert en["model"]["title"] == "Stryd 5 zones (% CP)"
    assert "Palladino 10 zones (% CP)" in [m["title"] for m in en["models"]["power"]]


def test_analysis_power_card_switches_to_stryd(tmp_path):
    """The 「區間與課表強度」 power card: ?zsys=stryd shows Stryd's table, titled after it; an HR id
    or an unknown one keeps Palladino; the card lists both tables to choose from."""
    from datetime import datetime, timezone
    from backend.api.wko5views import _render
    from backend.engine.planning import Plan
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.customviews import REPO_VIEWS, load_custom_views
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    today = dt.date(2026, 9, 30)
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    (d / "000.fit").write_bytes(build_run(start=datetime(2026, 9, 28, 0, tzinfo=timezone.utc), seconds=1800,
                                          power=180, stryd=True))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=today,
                          estimate_thresholds=False, tz=timezone.utc)
    ds.plan = Plan()
    charts = {c.get("id"): c for v in load_custom_views([REPO_VIEWS]).values() if not v.get("error")
              for dash in v["dashboards"] for c in dash["charts"]}
    power = charts["palladino-power-zones"]
    end = date_to_day(today)
    res = _render(power, ds, end - 30, end, None, None, params={"zsys": "stryd"})
    assert res["title"] == "Stryd 功率區間（跑步）" == res["zones"]["title"]
    assert [r["id"] for r in res["zones"]["rows"]] == ["1", "2", "3", "4", "5"]
    assert "Stryd" in res["zones"]["source"]
    assert [c["id"] for c in res["zones"]["choices"]] == ["palladino", "stryd", "classic", "personal"]   # SP-363 R2
    for params in ({}, {"zsys": "frielhr"}, {"zsys": "nope"}):
        r = _render(power, ds, end - 30, end, None, None, params=params)
        assert r["title"] == power["title"] and r["zones"]["system"] == "palladino"
    # the HR card doesn't take a power table
    hr = charts["friel-hr-zones"]
    assert _render(hr, ds, end - 30, end, None, None, params={"zsys": "stryd"})["zones"]["system"] == "frielhr"


def test_settings_api_has_the_stryd_table(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as API
    from backend.engine import planning as PL
    path = tmp_path / "plan.json"
    PL.Plan(thresholds=[PL.Threshold("2026-01-01", lthr=160.0, cp=252.0, note="test")]).save(path)
    load, save = PL.Plan.load.__func__, PL.Plan.save
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, p=path: load(cls, p)))
    monkeypatch.setattr(PL.Plan, "save", lambda self, p=path: save(self, p))
    monkeypatch.setattr(API, "_wko5_settings",
                        lambda: {"runthr": None, "runmhr": None, "bikethr": None, "runtpace": None, "mftp": None})
    app = FastAPI()
    app.include_router(API.router)
    pz = TestClient(app).get("/api/v1/plan/thresholds").json()["power_zones"]
    assert pz["zones"][0]["id"] == "1A"                                  # Palladino first, as before
    st = pz["stryd"]
    assert [z["id"] for z in st["zones"]] == ["1", "2", "3", "4", "5"]
    assert st["zones"][0]["watts"] == [164, 202] and st["zones"][-1]["watts"] == [290, 328]
    assert "Stryd" in st["source"]


def test_settings_page_shows_both_tables_in_both_languages():
    html = (ROOT / "backend" / "static" / "settings.html").read_text("utf-8")
    assert 'id="thr-pz"' in html and 'id="thr-pz-stryd"' in html and 'id="thr-pzsrc-stryd"' in html
    for lang in ("zh-TW", "en"):
        cat = json.loads((ROOT / "backend" / "static" / "i18n" / lang / "settings.json").read_text("utf-8"))
        for k in ("thr.pz_h", "thr.pz_palladino", "thr.pz_stryd", "thr.pz_stryd_src"):
            assert cat.get(k) and "Stryd" in cat["thr.pz_stryd"], (lang, k)
            assert "WKO" not in cat[k]
    en = json.loads((ROOT / "backend" / "i18n" / "locales" / "en.json").read_text("utf-8"))
    assert en["Stryd 功率區間（跑步）"] == "Stryd power zones (running)"
    assert en["Stryd 5 區（% CP）"] == "Stryd 5 zones (% CP)"

