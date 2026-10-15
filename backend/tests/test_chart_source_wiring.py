"""charts.data_source wired into the chart page's Dataset factory
(api/wko5views._dataset, also used by overview and race power), the header
chip, and the viewer's tooltip / hover fixes."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.api import overview as OV, racepower as RP, wko5views as WV
from backend.engine.wko5expr import datasource as DSRC
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.tests.fit_builder import build_run

STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(WV, "ATHLETE_DIR", tmp_path / "no-wko5")
    WV._dataset_cfg.cache_clear()
    yield
    WV._dataset_cfg.cache_clear()


def _one_fit(root, source):
    d = root / source / "2026"
    d.mkdir(parents=True)
    (d / "a.fit").write_bytes(build_run(datetime(2026, 9, 1, 8, tzinfo=timezone.utc), seconds=300))


def test_coros_source_builds_a_fit_folder_dataset(isolated, monkeypatch, _fit_root_in_tmp):
    _one_fit(_fit_root_in_tmp, "coros")
    monkeypatch.setattr(DSRC, "current_source", lambda user_id=1: "coros")
    ds = WV._dataset()
    assert isinstance(ds, FitFolderDataset) and ds.source == "coros" and len(ds.workouts) == 1
    # overview and race power go through the same factory
    assert OV._dataset() is ds and RP._dataset() is ds
    # a new FIT (sync) changes source_stamp -> a fresh Dataset
    (_fit_root_in_tmp / "coros" / "2026" / "b.fit").write_bytes(
        build_run(datetime(2026, 9, 2, 8, tzinfo=timezone.utc), seconds=300))
    ds2 = WV._dataset()
    assert ds2 is not ds and len(ds2.workouts) == 2


def test_default_source_is_still_the_wko5_folder(isolated, monkeypatch):
    seen = []

    def fake(source, wko5_dir, config=None, today=None):
        seen.append((source, wko5_dir))
        return object.__new__(FitFolderDataset)            # any object; only the call matters
    monkeypatch.setattr(WV, "dataset_for_source", fake)
    monkeypatch.setattr(DSRC, "current_source", lambda user_id=1: "wko5")
    WV._dataset()
    assert seen == [("wko5", WV.ATHLETE_DIR)]
    stamp_args = []
    monkeypatch.setattr(DSRC, "source_stamp", lambda s, d: stamp_args.append(s) or "x")
    WV._dataset(source="tp")
    assert stamp_args == ["tp"] and seen[-1][0] == "tp"


def test_viewer_has_the_sport_chip_and_settings_switches_the_source():
    """SP-381: the viewer's top right switches 專項 (主要訓練項目), not the data source; the source
    is chosen in 設定 › 資料同步 (s-chart), which says it is live."""
    html = (STATIC / "charts.html").read_text(encoding="utf-8")
    assert '<span id="sport-chip"></span>' in html and "/api/v1/static/sportchip.js" in html
    assert "sourcechip.js" not in html
    js = (STATIC / "sportchip.js").read_text(encoding="utf-8")
    assert 'const SPORTS = ["auto", "trail", "road"];' in js
    assert "/api/v1/app/primary-sport" in js and "primary_sport: sel.value" in js and "location.reload()" in js
    import json
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text(encoding="utf-8"))
        for k in ("label", "auto", "trail", "road", "tip", "demo", "failed"):
            assert cat[f"sportchip.{k}"]
    s = (STATIC / "settings.html").read_text(encoding="utf-8")
    assert "接線完成後才會生效" not in s and "仍讀 WKO5" not in s and 'id="s-chart"' in s


def test_viewer_tooltip_units_follow_the_echarts_series():
    html = (STATIC / "charts.html").read_text(encoding="utf-8")
    # units / stack total from the source of out[i], not series[i] (vlines are skipped on category axes)
    assert "srcOf[p.seriesIndex]" in html and "srcOf[g[0].seriesIndex]" in html
    assert "series[p.seriesIndex]" not in html and "series[g[0].seriesIndex]" not in html
    assert "out.push(" not in html.replace("out.push(o)", "")
    # every hover-synced chart (time or distance x) keeps all points
    assert 'sampling: hx ? undefined : "lttb"' in html
    assert html.index("const hx = ") < html.index('sampling: hx ? undefined : "lttb"')
