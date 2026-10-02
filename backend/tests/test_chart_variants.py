"""Chart variants (backend/engine/wko5expr/variants.py): one card, several
axes + series sets behind a toggle, `?variant=<key>` on the chart endpoint.

The 每日 TSS（跟體能比） chart in views/training.json uses it for TSS ↔ % CTL.
The % CTL bands are the TSS bands converted per day (CTL+30 → (CTL+30)/CTL),
so on synthetic FIT data every day must land in the same colour in both
variants, with % CTL = that day's TSS ÷ the previous day's CTL."""
import datetime as dt
from datetime import datetime, timezone

import pytest

from backend.api import wko5views as WV
from backend.engine.wko5expr import datasource as DSRC
from backend.engine.wko5expr import variants as VR
from backend.engine.wko5expr.customviews import CustomViewError, REPO_VIEWS, load_custom_views, parse_view
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.tests.fit_builder import build_run

TITLE = "每日 TSS（跟體能比）"
TODAY = dt.date(2026, 9, 30)


def _chart(c):
    return parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [c]}]})["dashboards"][0]["charts"][0]


def _two():
    return {"title": "t", "variants": [
        {"key": "a", "label": "A", "axes": [{"id": "TSS"}], "series": [{"name": "s1", "expression": "tss"}]},
        {"key": "b", "label": "B", "description": "說明 B", "axes": [{"id": "PERCENT"}],
         "series": [{"name": "s2", "type": "bar", "expression": "tss / 100"}]}]}


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def test_variants_parse_and_the_first_is_the_default_at_the_top_level():
    ch = _chart(_two())
    assert [v["key"] for v in ch["variants"]] == ["a", "b"]
    assert ch["series"] == ch["variants"][0]["series"] and ch["axes"] == [{"id": "TSS"}]
    # variant series get the same defaults as chart series
    s2 = ch["variants"][1]["series"][0]
    assert s2["type"] == "bar" and s2["y_axis"] == "NONE" and s2["line_style"] == "solid"


@pytest.mark.parametrize("bad", [
    {"variants": []},
    {"variants": [{"label": "A", "series": [{"name": "s"}]}]},                                   # no key
    {"variants": [{"key": "a", "series": [{"name": "s"}]}]},                                     # no label
    {"variants": [{"key": "a", "label": "A", "series": []}]},                                    # no series
    {"variants": [{"key": "a", "label": "A", "series": ["tss"]}]},                               # series not an object
    {"variants": [{"key": "a", "label": "A", "series": [{"name": "s"}]},
                  {"key": "a", "label": "B", "series": [{"name": "s"}]}]},                       # key twice
    {"series": [{"name": "s"}], "variants": [{"key": "a", "label": "A", "series": [{"name": "s"}]}]},  # both
])
def test_bad_variants_are_refused(bad):
    with pytest.raises(CustomViewError):
        _chart({"title": "t", **bad})


def test_variant_series_basis_is_checked_like_chart_series():
    with pytest.raises(CustomViewError):
        _chart({"title": "t", "variants": [{"key": "a", "label": "A",
                                            "series": [{"name": "s", "basis": "power"}]}]})


# ---------------------------------------------------------------------------
# apply_variant
# ---------------------------------------------------------------------------

def test_apply_variant_swaps_axes_series_and_description():
    ch = _chart({**_two(), "description": "說明 A"})
    a, ia = VR.apply_variant(ch, None)
    b, ib = VR.apply_variant(ch, "b")
    assert [s["name"] for s in a["series"]] == ["s1"] and a["description"] == "說明 A"
    assert [s["name"] for s in b["series"]] == ["s2"] and b["axes"] == [{"id": "PERCENT"}]
    assert b["description"] == "說明 B" and "variants" not in b
    assert ia["variant"] == "a" and ib["variant"] == "b" and ib["variant_toggle"]
    assert ib["variant_choices"] == [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}]
    assert VR.apply_variant(ch, "nope")[1]["variant"] == "a"                # unknown key -> default
    assert VR.apply_variant({"title": "t", "series": []}, "b") == ({"title": "t", "series": []}, None)


# ---------------------------------------------------------------------------
# views/training.json
# ---------------------------------------------------------------------------

def _index():
    v = load_custom_views([REPO_VIEWS])["我的訓練"]
    for di, d in enumerate(v["dashboards"]):
        for ci, c in enumerate(d["charts"]):
            if c["title"] == TITLE:
                return di, ci, c
    raise AssertionError(TITLE)


def test_daily_tss_chart_has_tss_and_pct_variants():
    _, _, ch = _index()
    tss, pct = ch["variants"]
    assert (tss["key"], tss["label"], pct["key"], pct["label"]) == ("tss", "TSS", "pct", "% CTL")
    # TSS variant: the chart as it was (fixed 0–150 axis, 4 bands + CTL/ATL)
    assert tss["axes"] == [{"id": "TSS", "min": 0, "max": 150}] and len(tss["series"]) == 6
    # % CTL: auto range (no max), a 100 % line, the same 4 bands; its own explanation
    assert pct["axes"] == [{"id": "PERCENT", "min": 0}]
    assert any(s["expression"] == "(,1)" for s in pct["series"])
    assert [s["color"] for s in pct["series"][:4]] == [s["color"] for s in tss["series"][:4]]
    assert "% CTL" in pct["label"] and "(CTL+30) ÷ CTL" in pct["description"]


@pytest.fixture
def api(monkeypatch, tmp_path, _fit_root_in_tmp):
    """The chart API on a synthetic COROS folder: runs of varying length, so the
    days spread over the four bands."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    root = _fit_root_in_tmp / "coros" / "2026"
    root.mkdir(parents=True)
    minutes = [30, 0, 45, 90, 0, 20, 150, 60, 0, 240, 30, 0, 75, 120, 0, 40, 300, 0, 50, 90]
    for i, m in enumerate(minutes):
        if m:
            day = 1 + i
            (root / f"{i}_2026-09-{day:02d}_run.fit").write_bytes(build_run(
                datetime(2026, 9, day, 8, tzinfo=timezone.utc), power=[260] * (m * 60), hr=150, stryd=True))   # Stryd power: power TSS (watch power is not used)
    from backend.engine.wko5expr.render_cache import RenderCache
    monkeypatch.setattr(WV, "RENDER_CACHE", RenderCache(tmp_path / "render-cache"))
    monkeypatch.setattr(WV, "ATHLETE_DIR", tmp_path / "no-wko5")
    monkeypatch.setattr(DSRC, "current_source", lambda user_id=1: "coros")
    def dataset(s, d, config=None):
        ds = FitFolderDataset(_fit_root_in_tmp / "coros", config=config, today=TODAY, source=s)
        ds.athlete.settings["runftp"] = [(dt.date(2020, 1, 1), 260.0)]    # an hour of these runs = 100 TSS
        for w in ds.workouts:
            w.metrics = ds._metrics(w)
        return ds
    monkeypatch.setattr(WV, "dataset_for_source", dataset)
    app = FastAPI()
    app.include_router(WV.router)
    WV._dataset_cfg.cache_clear()
    yield TestClient(app)
    WV._dataset_cfg.cache_clear()


def _get(client, variant=None):
    di, ci, _ = _index()
    q = "begin=2026-08-01&end=2026-09-30" + (f"&variant={variant}" if variant else "")
    r = client.get(f"/api/v1/wko5/views/我的訓練/dashboards/{di}/charts/{ci}?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def _bars(j):
    """{x: (band index, value)} over the four stacked-bar bands."""
    out = {}
    for bi, s in enumerate(j["series"][:4]):
        for x, y in (s["data"].get("points") or []):
            if y is not None:
                assert x not in out, "a day is in one band only"
                out[x] = (bi, y)
    return out


def test_pct_variant_matches_the_tss_variant_day_by_day(api):
    t = _get(api)
    p = _get(api, "pct")
    assert t["variant"] == "tss" and p["variant"] == "pct" and p["variant_toggle"]
    assert [c["key"] for c in p["variant_choices"]] == ["tss", "pct"]
    assert p["description"] != t["description"] and "Daily % of CTL" in p["description"]
    tb, pb = _bars(t), _bars(p)
    day = lambda x: dt.date.fromisoformat(str(x)[:10])          # noqa: E731 — x is an ISO date
    ctl = {day(x): y for x, y in t["series"][4]["data"]["points"] if y is not None}
    prev_ctl = lambda x: ctl.get(day(x) - dt.timedelta(days=1)) or 0.0   # noqa: E731
    # a day after CTL 0 (the very first run) has no % of CTL; every other day is in both
    assert tb and set(pb) == {x for x in tb if prev_ctl(x) > 0} and len(pb) == len(tb) - 1
    assert len({bi for bi, _ in pb.values()}) >= 3, "the synthetic days spread over the bands"
    for x, (bi, tss) in tb.items():
        prev = prev_ctl(x)
        if not prev:
            continue
        assert pb[x][0] == bi, f"day {x}: same colour in both variants"
        assert pb[x][1] == pytest.approx(tss / prev, rel=1e-6)
    # the reference line sits at 100 %
    ref = next(s for s in p["series"] if s["name"] == "100% = CTL")
    assert ref["data"]["kind"] == "hline" and ref["data"]["y"] == pytest.approx(1.0)


def test_unknown_variant_falls_back_to_the_default(api):
    assert _get(api, "nope")["variant"] == "tss"
