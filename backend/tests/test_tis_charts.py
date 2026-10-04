"""The aerobic / anaerobic TIS charts in 我的訓練 › 負荷 PMC (views/training.json):
both render on synthetic power workouts, every series has numbers, the
per-workout chart matches the built-ins and the load lines are tl() of them.
The WKO5 parity of the TIS numbers is backend/tests/realdata/test_real_wko5_tis.py."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
import json
from pathlib import Path

import pytest

from backend.engine.wko5expr import evaluator as E
from backend.engine.wko5expr.customviews import parse_view
from backend.engine.wko5expr.render import render_chart
from backend.engine.wko5expr.viewi18n import translate_view
from backend.tests.test_wko5expr_functions import TODAY, _ds, model_curve, run_workout

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "views" / "training.json"


def _dashboard():
    v = parse_view(json.loads(VIEW.read_text("utf-8")), VIEW)
    return next(d for d in v["dashboards"] if d["id"] == "load-pmc")


def _chart(cid):
    return next(c for c in _dashboard()["charts"] if c["id"] == cid)


@pytest.fixture
def ds(monkeypatch):
    # the PD fit sees a known curve (the samples are too short to fit one)
    monkeypatch.setattr(E.Evaluator, "_workout_curve", lambda self, node, w: model_curve())
    n = 1800
    wk = [run_workout(dt.datetime(2026, 9, 10, 8), n=n, power=[200.0] * n),        # steady, below threshold
          run_workout(dt.datetime(2026, 9, 15, 8), n=n, power=[250.0] * n),        # at FTP
          run_workout(dt.datetime(2026, 9, 20, 8), n=n, power=[380.0, 150.0] * (n // 2)),   # intervals
          run_workout(dt.datetime(2026, 9, 22, 8), n=n, heartrate=[140.0] * n)]     # no power
    return _ds(wk)


def test_tis_charts_sit_at_the_end_of_the_load_dashboard():
    # appended after the existing charts: their dashboard / chart indexes (the chart URLs) stay put
    ids = [c["id"] for c in _dashboard()["charts"]]
    assert ids[-2:] == ["tis-per-workout", "tis-load"]
    assert ids[:6] == ["pmc-all", "daily-tss", "tss-total", "ramp-rate", "form-pct", "load-ratio"]
    for cid in ("tis-per-workout", "tis-load"):
        d = _chart(cid)["description"]
        assert all(k in d for k in ("怎麼看：", "看什麼：", "方法：")), cid


def test_per_workout_tis_chart_renders_the_builtins(ds):
    ch = _chart("tis-per-workout")
    out = render_chart(ch, ds, ds.today - 30, ds.today)
    by = {s["name"]: s["data"] for s in out["series"]}
    assert set(by) == {"有氧 TIS", "無氧 TIS"}
    ev = E.Evaluator(ds, ds.today - 30, ds.today)
    for name, expr in (("有氧 TIS", "tisaerobic"), ("無氧 TIS", "tisanaerobic")):
        data = by[name]
        assert data["kind"] == "points" and data["x"] == "datetime", data
        ys = [y for _x, y in data["points"] if y is not None]
        assert len(ys) == 3                                       # the run without power is not drawn
        assert all(1 <= y <= 10 and y == int(y) for y in ys)
        assert ys == [ev.evaluate(expr, w) for w in ds.workouts[:3]]
    ana = [y for _x, y in by["無氧 TIS"]["points"]]
    assert ana[0] == 1 and ana[2] > ana[0]                        # only the intervals go above threshold


def test_tis_load_chart_renders_four_daily_lines(ds):
    ch = _chart("tis-load")
    out = render_chart(ch, ds, ds.today - 30, ds.today)
    assert [s["name"] for s in out["series"]] == ["有氧 長期", "有氧 短期", "無氧 長期", "無氧 短期"]
    last = {}
    for s in out["series"]:
        assert s["data"]["kind"] == "points" and s["data"]["x"] == "date", s["data"]
        ys = [y for _x, y in s["data"]["points"] if y is not None]
        assert ys and max(ys) > 0, s["name"]
        last[s["name"]] = ys[-1]
    # the series are tl() of the per-workout built-ins with the athlete's own constants
    ev = E.Evaluator(ds, ds.today - 30, ds.today)
    want = ev.evaluate("tl((tisaerobic), atlconstant)")
    assert last["有氧 短期"] == pytest.approx(want.at(ds.today))
    assert last["有氧 短期"] > last["有氧 長期"]                  # 20 days of training: the 7-day line is ahead


def test_tis_charts_are_translated():
    v = parse_view(json.loads(VIEW.read_text("utf-8")), VIEW)
    tr = json.loads((ROOT / "views" / "i18n" / "en.json").read_text("utf-8"))["training"]
    en = translate_view(v, tr)
    d = next(d for d in en["dashboards"] if d["id"] == "load-pmc")
    by = {c["id"]: c for c in d["charts"]}
    assert by["tis-per-workout"]["title"].startswith("Aerobic / anaerobic TIS")
    assert [s["name"] for s in by["tis-load"]["series"]] == [
        "Aerobic long-term", "Aerobic short-term", "Anaerobic long-term", "Anaerobic short-term"]
    assert "How to read:" in by["tis-load"]["description"]
