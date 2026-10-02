"""
「長時間輕鬆跑的心率飄移」 verdict bars (engine/panels/drift_bars.py): each bar's hover line and
the latest bar's label. Synthetic workouts and a fake measure; nothing reads the athlete's data.
"""
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.engine.panels import drift_bars as DB
from backend.engine.wko5expr.customviews import CustomViewError, parse_view
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.render import _dt_iso


def run(idx, when, dur_s):
    return SimpleNamespace(idx=idx, day=date_to_day(when), metrics={"duration": dur_s}, sport="run", tags=set())


W = [run(0, dt.datetime(2026, 9, 1, 6, 30), 3 * 3600 + 5 * 60),
     run(1, dt.datetime(2026, 9, 8, 6, 30), 52 * 60),
     run(2, dt.datetime(2026, 9, 15, 6, 30), 75 * 60)]
DR = {0: {"ok": True, "temp_c": 22.4, "temp_band": "cool"},
      1: {"ref_ok": True, "temp_c": None},
      2: {"ok": True, "temp_c": 29.0, "temp_band": "hot"}}
DS = SimpleNamespace(workouts=W)


def measure(ds, w):
    return {"drift": DR[w.idx]}


def res():
    iso = [_dt_iso(w.day) for w in W]
    bar = lambda name, pts: {"name": name, "type": "bar", "data": {"kind": "points", "x": "datetime", "points": pts}}
    return {"series": [bar("穩定（< 5%）", [[iso[0], 0.031], [iso[1], 0.02]]),
                       bar("飄很多（> 10%）", [[iso[2], 0.124]]),
                       {"name": "5%", "type": "line", "data": {"kind": "hline", "y": 0.05}}]}


def test_each_bar_gets_date_duration_and_temperature_on_hover():
    out = DB.apply(res(), DS, measure)
    green, red, line = out["series"]
    assert green["tips"][0] == "2026-09-01・跑 3 小時 5 分・🌡 22 °C（< 25 °C）"
    assert green["tips"][1].startswith("2026-09-08・跑 52 分・🌡 溫度不明") and "只當參考" in green["tips"][1]
    assert "29 °C（> 28 °C）" in red["tips"][0] and "天熱" in red["tips"][0]
    assert "tips" not in line and "labels" not in line


def test_only_the_latest_bar_is_labelled():
    out = DB.apply(res(), DS, measure)
    green, red, _ = out["series"]
    assert red["labels"] == ["最新 12.4%"] and "labels" not in green


def test_no_jargon_words_in_the_hover():
    tips = [t for s in DB.apply(res(), DS, measure)["series"] for t in s.get("tips", [])]
    for word in ("標準誤", "SE", "信賴", "回歸", "Pa:HR"):
        assert not any(word in t for t in tips), word


def test_the_views_chart_puts_each_run_in_its_verdict_bar():
    # end to end on synthetic runs: the view's expressions + the panel
    from backend.engine import workout_review as R
    from backend.engine.wko5expr.basis import apply_basis
    from backend.engine.wko5expr.customviews import load_custom_views
    from backend.engine.wko5expr.render import render_chart
    from backend.tests.test_heat_bands import _temps
    from backend.tests.test_quality_gate import TODAY, _ds
    from backend.tests.test_workout_review import _run
    ends = (136.0, 150.0, 165.0)
    ds = _ds([_run(TODAY - dt.timedelta(days=d), minutes=52, hr=135.0, hr_end=e) for d, e in zip((9, 6, 3), ends)])
    ws = _temps(ds, [22.0, 22.0, 22.0])
    views = load_custom_views([Path(__file__).resolve().parents[2] / "views"])
    ch = next(c for d in views["周期化訓練"]["dashboards"] for c in d["charts"] if c["title"] == "長時間輕鬆跑的心率飄移")
    ch = apply_basis(ch, "pace")[0]
    lo, hi = min(w.day for w in ws) - 1, max(w.day for w in ws) + 1
    out = DB.apply(render_chart(ch, ds, int(lo), int(hi)), ds)
    bars = {s["name"]: s for s in out["series"] if s.get("type") == "bar"}
    seen = {}
    for name, s in bars.items():
        pts = s["data"].get("points") or []                     # a verdict with no run: no points, no tips
        assert len(s.get("tips", [])) == len(pts)
        for (x, y), tip in zip(pts, s.get("tips", [])):
            seen[x[:10]] = (name, y)
            assert tip.startswith(x[:10]) and "跑 52 分" in tip
    for w in ws:
        want = R.basis_drift(R.measure(ds, w)["drift"], "pace", ref=True)[0]
        name, y = seen[_dt_iso(w.day)[:10]]
        assert y == pytest.approx(want)
        assert name == ("穩定（< 5%）" if want < 0.05 else "有點飄（5–10%）" if want < 0.10 else "飄很多（> 10%）")
    assert sum(any(l for l in s.get("labels", [])) for s in bars.values()) == 1
    assert len({n for n, _ in seen.values()}) >= 2, seen            # the runs span verdicts


def test_the_view_turns_it_on_and_validates_it():
    root = Path(__file__).resolve().parents[2] / "views" / "periodization.json"
    v = parse_view(json.loads(root.read_text("utf-8")), root)
    ch = [c for d in v["dashboards"] for c in d["charts"] if c["title"] == "長時間輕鬆跑的心率飄移"]
    assert ch and ch[0]["drift_bars"] is True
    bad = {"name": "x", "dashboards": [{"title": "d", "charts": [{"title": "c", "drift_bars": "yes"}]}]}
    with pytest.raises(CustomViewError):
        parse_view(bad)
