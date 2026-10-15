"""SP-465: 圖表分析 › 能力 › 跑步功率曲線 draws the mFTP line by default — a horizontal line at
the CP the 課表 and the settings page use (zones.cp_in_effect: a plan CP test, else the dated
setting, else the Stryd PD fit = WKO5's mFTP, SP-449), labelled with its value and date. No CP
(not enough Stryd runs to fit) = no line, and the chart draws as before.

Synthetic only — never the WKO5 folder, the real app DB or ~/.wko5coach."""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.engine import planning as PL
from backend.engine import zones as Z
from backend.engine.panels import cp_ref as CR
from backend.engine.wko5expr.dataset import date_to_day

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 13)
END = int(date_to_day(TODAY))
FIT = {"date": dt.date(2026, 10, 8), "cp": 191.04, "frc_j": 9000.0, "runs": 49, "n_points": 40}
CURVE = {"title": "跑步功率曲線", "series": [
    {"name": "MMP 曲線", "type": "line", "y_axis": "WATTS", "x_unit": "HMSSHORT", "unit": "W",
     "data": {"kind": "curve", "points": [[1, 600.0], [3600, 180.0]]}},
    {"name": "PD 模型曲線", "type": "line", "y_axis": "WATTS", "x_unit": "HMSSHORT", "unit": "W",
     "data": {"kind": "curve", "points": [[1, 610.0], [3600, 182.0]]}}]}


def _ds(tmp_path, fits=(FIT,), plan=None):
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    (d / "000.fit").write_bytes(build_run(start=datetime(2026, 10, 8, 0, tzinfo=timezone.utc), seconds=1800,
                                          power=170, stryd=True))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=TODAY,
                          estimate_thresholds=False, tz=timezone.utc, athlete_settings=[])
    ds.plan = plan or PL.Plan()
    ds._cp_est = [dict(f) for f in fits]
    return ds


def test_the_line_is_the_cp_the_plan_and_the_settings_page_use(tmp_path):
    ds = _ds(tmp_path)
    ref = CR.line(ds, END)
    ci = Z.cp_in_effect(ds, END)
    assert ref["value"] == round(ci["value"], 1) == 191.0
    assert ref["date"] == ci["date"] == "2026-10-08" and ref["kind"] == "estimate"
    res = CR.apply(CURVE, ref)
    line = res["series"][-1]
    assert [s["name"] for s in res["series"][:2]] == ["MMP 曲線", "PD 模型曲線"]
    assert line["role"] == "cp_ref" and line["data"] == {"kind": "hline", "y": 191.0}
    assert line["y_axis"] == "WATTS" and line["label_end"] is True
    assert "模型閾值功率" in line["name"] and "191" in line["name"] and "10/08" in line["name"]
    assert ci["source"] in line["tip"] and "課表" in line["tip"]
    assert res["cp_ref"] == ref
    # the optional 「mFTP 線」 of the range's own PD fit (render.mftp_plateau, off by default) is not
    # offered next to it: two lines both called mFTP with different numbers
    with_plateau = {**CURVE, "plateau": {"y": 205.0, "y_axis": "WATTS", "label": "mFTP 205 W", "tip": "x"}}
    assert CR.apply(with_plateau, ref)["plateau"] is None
    assert CURVE["series"][-1]["name"] == "PD 模型曲線"           # the input is not changed


def test_a_plan_cp_test_is_the_line_and_says_so(tmp_path):
    plan = PL.Plan(thresholds=[PL.Threshold("2026-10-10", cp=200.0, cp_method="2pt")])
    ref = CR.line(_ds(tmp_path, plan=plan), END)
    assert ref["value"] == 200.0 and ref["kind"] == "test" and ref["date"] == "2026-10-10"
    name = CR.apply(CURVE, ref)["series"][-1]["name"]
    assert name.startswith("CP") and "200" in name and "10/10" in name and "mFTP" not in name


def test_wko5_parity_mode_still_draws_the_settings_pages_cp(tmp_path):
    """WKO5 對照模式: the chart's Dataset has an empty plan (parity), but the settings page and the
    plan read the season plan's CP test (api/plan.py _effective): the line follows them."""
    ds = _ds(tmp_path)                                   # its own plan: empty, as in parity mode
    season = PL.Plan(thresholds=[PL.Threshold("2026-09-23", cp=260.0, cp_method="2pt")])
    ref = CR.line(ds, END, plan=season)
    assert ref == {"value": 260.0, "date": "2026-09-23", "kind": "test", "source": "你的測試 2026-09-23"}
    from backend.api import plan as API
    assert API._effective(season, TODAY)["cp"]["value"] == ref["value"]
    # a test after the range's end does not apply yet; then the PD fit of the Dataset
    later = PL.Plan(thresholds=[PL.Threshold("2026-10-20", cp=260.0, cp_method="2pt")])
    assert CR.line(ds, END, plan=later)["value"] == 191.0


def test_no_cp_no_line_and_the_chart_is_unchanged(tmp_path):
    ds = _ds(tmp_path, fits=())
    assert CR.line(ds, END) is None
    assert CR.apply(CURVE, None) == CURVE
    plateau = {**CURVE, "plateau": {"y": 205.0, "y_axis": "WATTS", "label": "mFTP 205 W", "tip": "x"}}
    assert CR.apply(plateau, None) == plateau                     # no CP: the optional line stays


def test_the_power_curve_asks_for_the_line():
    from backend.engine.wko5expr import chartscope as CS
    from backend.engine.wko5expr.customviews import CustomViewError, REPO_VIEWS, load_custom_views, parse_view
    v = load_custom_views([REPO_VIEWS])["我的訓練"]
    ch = next(c for d in v["dashboards"] for c in d["charts"] if c["id"] == "run-power-recent-bests")
    assert ch["cp_ref"] == "mftp" and "模型閾值功率" in ch["description"]
    assert "cp_ref" in CS.POST_ROOTS and "cp_ref" in CS._post(ch)
    for bad in ({"cp_ref": "lthr"}, {"cp_ref": "mftp", "kind": "zones"}):
        raw = {"name": "x", "dashboards": [{"title": "d", "charts": [{"title": "c", **bad}]}]}
        with pytest.raises(CustomViewError):
            parse_view(raw, Path("x.json"))


@pytest.fixture
def api(monkeypatch, tmp_path, _fit_root_in_tmp):
    """The chart API on a synthetic COROS folder (eight Stryd runs); the PD fit is faked."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import wko5views as WV
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr import datasource as DSRC
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.engine.wko5expr.render_cache import RenderCache
    from backend.tests.fit_builder import build_run
    root = _fit_root_in_tmp / "coros"
    st = {"n": 8, "plan": PL.Plan()}

    def write(n):
        d = root / "2026"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            start = datetime(2026, 10, 8, 0, tzinfo=timezone.utc) - dt.timedelta(days=6 * i)
            (d / f"{i}_run.fit").write_bytes(build_run(start=start, seconds=1800, power=170, stryd=True))
    monkeypatch.setattr(A, "pd_model", lambda ds, day, runs, ref, any_power=False:
                        {"mftp": 191.04, "frc": 9000.0, "n_points": 40})
    monkeypatch.setattr(WV, "RENDER_CACHE", RenderCache(tmp_path / "render-cache"))
    monkeypatch.setattr(WV, "ATHLETE_DIR", tmp_path / "no-wko5")
    monkeypatch.setattr(DSRC, "current_source", lambda user_id=1: "coros")

    def make(s, d_, config=None):
        ds = FitFolderDataset(root, config=config, today=TODAY, source=s, athlete_settings=[],
                              estimate_thresholds=True)        # as with the app DB: the 課表's fits
        ds._plan_fixed = st["plan"]
        return ds
    monkeypatch.setattr(WV, "dataset_for_source", make)
    app = FastAPI()
    app.include_router(WV.router)
    WV._dataset_cfg.cache_clear()
    yield TestClient(app), write, st
    WV._dataset_cfg.cache_clear()


def _get(client):
    from backend.api import wko5views as WV
    v = WV._view("我的訓練")
    di, ci = next((di, ci) for di, d in enumerate(v["dashboards"]) for ci, c in enumerate(d["charts"])
                  if c.get("id") == "run-power-recent-bests")
    r = client.get(f"/api/v1/app/views/我的訓練/dashboards/{di}/charts/{ci}?begin=2026-07-15&end=2026-10-13")
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_api_draws_the_line_on_opening(api):
    client, write, _st = api
    write(8)
    j = _get(client)
    line = next(s for s in j["series"] if s.get("role") == "cp_ref")
    assert line["data"] == {"kind": "hline", "y": 191.0} and "模型閾值功率" in line["name"]
    assert [s["name"] for s in j["series"] if s.get("role") != "cp_ref"] == ["MMP 曲線", "近 7 天新高", "PD 模型曲線"]


def test_api_without_a_fit_draws_no_line(api):
    client, write, _st = api
    write(2)                                       # two Stryd runs: no PD fit (5 needed), no CP
    j = _get(client)
    assert not any(s.get("role") == "cp_ref" for s in j["series"]) and j.get("cp_ref") is None
    assert [s["name"] for s in j["series"]] == ["MMP 曲線", "近 7 天新高", "PD 模型曲線"]


def test_api_a_new_cp_is_not_served_from_the_cache(api):
    client, write, st = api
    write(8)
    assert next(s for s in _get(client)["series"] if s.get("role") == "cp_ref")["data"]["y"] == 191.0
    # a CP test saved on the settings page: the same Dataset, only the plan changed (not plan.json
    # here, so only the line's own key part can tell the cached drawing is out of date)
    st["plan"].thresholds.append(PL.Threshold("2026-10-10", cp=200.0, cp_method="2pt"))
    assert next(s for s in _get(client)["series"] if s.get("role") == "cp_ref")["data"]["y"] == 200.0
