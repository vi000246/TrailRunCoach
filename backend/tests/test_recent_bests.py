"""WKO5's "New Bests" series (Season View > PDC耐力模型 > PD Curve with Metrics
(Run)) on synthetic data where the answer is known:

    @historic := athleterange(min({begindate,today-7}), min({enddate,today-7}), meanmax(runpower)),
    @recent   := athleterange(today-6, today, meanmax(runpower)),
    if(@recent > @historic, @recent)

History (9/10): 20 s @ 400 W, 1780 s @ 250 W, 1800 s @ 150 W.
Recent  (9/28): 300 s @ 320 W, 300 s @ 150 W, 3000 s @ 235 W.
The recent run beats history in two separate bands — about 40 s – 6 min and
about 36 min – 60 min — and not in between, so the result is a partial curve
with a gap."""
import datetime as dt
from datetime import datetime, timezone

import pytest

from backend.api import wko5views as WV
from backend.engine.wko5expr import datasource as DSRC
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.engine.wko5expr.render import render_chart
from backend.tests.fit_builder import build_run

NEW_BESTS = ("@historic := athleterange(min({begindate,today-7}), min({enddate,today-7}), meanmax(runpower)),\n"
             "@recent := athleterange(today-6, today, meanmax(runpower)),\n"
             "if(@recent > @historic, @recent)")
HISTORY = [400] * 20 + [250] * 1780 + [150] * 1800
RECENT = [320] * 300 + [150] * 300 + [235] * 3000
TODAY = dt.date(2026, 9, 30)
B, E = date_to_day(dt.date(2026, 7, 1)), date_to_day(TODAY)


def _write(root, day, watts, name):
    d = root / "2026"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(build_run(datetime(2026, 9, day, 8, tzinfo=timezone.utc), power=watts))


def _mm(watts, secs):
    return max(sum(watts[i:i + secs]) / secs for i in range(len(watts) - secs + 1))


@pytest.fixture
def ds(tmp_path):
    _write(tmp_path, 10, HISTORY, "history.fit")
    _write(tmp_path, 28, RECENT, "recent.fit")
    return FitFolderDataset(tmp_path, config=EngineConfig(parity=True), today=TODAY)


def test_date_bounds_of_the_two_ranges(ds):
    ev = Evaluator(ds, B, E)
    assert ev.evaluate("min({begindate,today-7})") == B
    assert ev.evaluate("min({enddate,today-7})") == E - 7
    # a range that ends before today-7: historic stops at the range end
    assert Evaluator(ds, B, E - 20).evaluate("min({enddate,today-7})") == E - 20


def test_new_bests_are_the_durations_where_the_last_7_days_beat_the_rest(ds):
    ev = Evaluator(ds, B, E)
    hist = ev.evaluate("athleterange(min({begindate,today-7}), min({enddate,today-7}), meanmax(runpower))")
    rec = ev.evaluate("athleterange(today-6, today, meanmax(runpower))")
    got = ev.evaluate(NEW_BESTS)
    hm, rm = dict(zip(hist.xs, hist.ys)), dict(zip(rec.xs, rec.ys))
    # the two ranges really are the history run and the recent run
    for x in (30, 300, 1800):
        assert hm[x] == pytest.approx(_mm(HISTORY, x), abs=0.5)
        assert rm[x] == pytest.approx(_mm(RECENT, x), abs=0.5)
    expect = [x for x in rec.xs if x in hm and rm[x] > hm[x]]
    assert list(got.xs) == expect
    assert list(got.ys) == [rm[x] for x in expect]
    # two separate bands: short (beats the 400 W sprint's tail) and long
    # (the mean-max grid is 1, 2, … 43, 45, 47 … so check bands, not exact durations)
    assert 60 in got.xs and any(250 <= x <= 330 for x in got.xs) and any(x >= 2400 for x in got.xs)
    assert not any(x <= 40 for x in got.xs) and not any(500 <= x <= 1800 for x in got.xs)


def test_no_activity_in_the_last_7_days_gives_an_empty_curve(tmp_path):
    _write(tmp_path, 10, HISTORY, "history.fit")
    _write(tmp_path, 20, RECENT, "older.fit")      # 9/20: before today-6
    ds = FitFolderDataset(tmp_path, config=EngineConfig(parity=True), today=TODAY)
    assert Evaluator(ds, B, E).evaluate(NEW_BESTS).xs == []


def test_the_rendered_area_breaks_between_the_two_bands(ds):
    ch = {"kind": "athlete", "title": "t", "axes": [],
          "series": [{"name": "New Bests", "type": "area", "expression": NEW_BESTS,
                      "x_axis": "HMSSHORT", "y_axis": "WATTS"}]}
    pts = render_chart(ch, ds, B, E)["series"][0]["data"]["points"]
    gaps = [i for i, p in enumerate(pts) if p[1] is None]
    assert len(gaps) == 1, "one break between the short and the long band"
    before, after = pts[gaps[0] - 1][0], pts[gaps[0] + 1][0]
    assert before < pts[gaps[0]][0] < 600 and after > 1800
    assert pts[0][1] is not None and pts[-1][1] is not None


@pytest.fixture
def api(monkeypatch, tmp_path, _fit_root_in_tmp):
    """The chart API on a synthetic COROS folder (source patched in process)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    root = _fit_root_in_tmp / "coros"

    def write(day, watts, name):
        _write(root, day, watts, name)
        WV._dataset_cfg.cache_clear()
    from backend.engine.wko5expr.render_cache import RenderCache
    monkeypatch.setattr(WV, "RENDER_CACHE", RenderCache(tmp_path / "render-cache"))
    monkeypatch.setattr(WV, "ATHLETE_DIR", tmp_path / "no-wko5")
    monkeypatch.setattr(DSRC, "current_source", lambda user_id=1: "coros")
    monkeypatch.setattr(WV, "dataset_for_source", lambda s, d, config=None: FitFolderDataset(
        root, config=config, today=TODAY, source=s))
    app = FastAPI()
    app.include_router(WV.router)
    WV._dataset_cfg.cache_clear()
    yield TestClient(app), write
    WV._dataset_cfg.cache_clear()


def _pd_chart_index():
    v = WV._view("我的訓練")
    for di, d in enumerate(v["dashboards"]):
        for ci, c in enumerate(d["charts"]):
            if c.get("window"):
                return di, ci, d["title"]
    raise AssertionError("no 近 N 天新高 chart")


def _get(client, window=None):
    di, ci, _ = _pd_chart_index()
    q = "begin=2026-07-01&end=2026-09-30" + (f"&window={window}" if window else "")
    r = client.get(f"/api/v1/wko5/views/我的訓練/dashboards/{di}/charts/{ci}?{q}")
    assert r.status_code == 200
    return r.json()


def test_custom_pd_chart_sits_in_the_ability_tab():
    di, ci, title = _pd_chart_index()
    assert title == "能力"
    ch = WV._view("我的訓練")["dashboards"][di]["charts"][ci]
    assert ch["window"] == {"default": 7, "choices": [7, 14, 28]}
    assert [s["expression"] for s in ch["series"]][0] == "meanmax(runpower)"


def test_custom_pd_chart_highlights_and_lists_the_improvements(api):
    client, write = api
    write(10, HISTORY, "1_2026-09-10_run.fit")
    write(28, RECENT, "2_2026-09-28_run.fit")
    j = _get(client)
    names = [s["name"] for s in j["series"]]
    assert names == ["MMP 曲線", "近 7 天新高"]           # the gain series is not drawn
    assert j["window"] == 7 and j["window_toggle"] and j["window_choices"] == [7, 14, 28]
    rb = j["recent_bests"]
    got = {i["secs"]: i["gain"] for i in rb["items"]}
    # 1 min: 320 vs history's (20·400 + 40·250)/60 = 300 -> +20 W
    assert got[60] == 20
    assert 1800 not in got and 600 not in got
    assert rb["note"].startswith("近 7 天新高：") and "1 分鐘 +20 W" in rb["note"]
    hl = j["series"][1]["data"]["points"]
    assert sum(p[1] is None for p in hl) == 1                # two bands, one break


def test_window_toggle_changes_the_recent_range(api):
    client, write = api
    write(10, HISTORY, "1_2026-09-10_run.fit")
    write(20, RECENT, "2_2026-09-20_run.fit")               # 10 days ago
    j7 = _get(client)
    assert j7["series"][1]["data"]["points"] == []
    assert j7["recent_bests"]["note"] == "近 7 天沒有新活動（資料到 9/20）"
    j14 = _get(client, 14)
    assert j14["window"] == 14 and j14["title"].endswith("近 14 天新高")
    assert j14["series"][1]["name"] == "近 14 天新高"
    assert {i["secs"]: i["gain"] for i in j14["recent_bests"]["items"]}[60] == 20
    assert _get(client, 5)["window"] == 7                    # not a choice: the default


def test_recent_activity_without_a_best_says_so(api):
    client, write = api
    write(10, HISTORY, "1_2026-09-10_run.fit")
    write(27, [100] * 600, "2_2026-09-27_run.fit")
    rb = _get(client)["recent_bests"]
    assert rb["items"] == [] and rb["note"] == "近 7 天沒有超越先前的最佳（資料到 9/27）"


def test_window_rewrite_only_touches_the_leading_literal():
    from backend.engine.wko5expr import recentbests as RB
    ch = {"title": "功率：近 7 天新高", "window": {"default": 7, "choices": [7, 14, 28]},
          "series": [{"name": "近 7 天新高", "expression": "@win := 7, athleterange(today-6, today, 7)"}]}
    out, info = RB.apply_window(ch, "28")
    assert out["series"][0]["expression"] == "@win := 28, athleterange(today-6, today, 7)"
    assert out["title"] == "功率：近 28 天新高" and info["window"] == 28
    assert ch["series"][0]["expression"].startswith("@win := 7")   # the original is untouched
    assert RB.duration_label(720) == "12 分鐘" and RB.duration_label(90) == "1 分 30 秒"
    assert RB.duration_label(5400) == "1 小時 30 分"


def test_season_view_chart_through_the_coros_source(monkeypatch, tmp_path, _fit_root_in_tmp):
    """The real Season View chart, with charts.data_source = coros patched in
    process (as test_chart_source_wiring.py does); the saved setting is untouched."""
    root = _fit_root_in_tmp / "coros"
    _write(root, 10, HISTORY, "1_2026-09-10_run.fit")
    _write(root, 28, RECENT, "2_2026-09-28_run.fit")
    monkeypatch.setattr(WV, "ATHLETE_DIR", tmp_path / "no-wko5")
    monkeypatch.setattr(DSRC, "current_source", lambda user_id=1: "coros")
    monkeypatch.setattr(WV, "dataset_for_source",
                        lambda s, d, config=None: FitFolderDataset(
                            root, config=config, today=TODAY, source=s))
    WV._dataset_cfg.cache_clear()
    try:
        ds = WV._dataset()
        assert ds.source == "coros" and len(ds.workouts) == 2
        ch = WV._view("WKO5 Season View", True)["dashboards"][4]["charts"][0]
        s = next(x for x in ch["series"] if x["name"] == "New Bests")
        assert "athleterange(today-6, today" in s["expression"]
        out = render_chart(ch, ds, B, E)
        nb = next(x for x in out["series"] if x["name"] == "New Bests")["data"]["points"]
        assert sum(p[1] is not None for p in nb) > 20
    finally:
        WV._dataset_cfg.cache_clear()
