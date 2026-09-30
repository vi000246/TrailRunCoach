"""
配速／功率 basis toggle on the drift charts (backend/engine/wko5expr/basis.py)
and the Pw:HR variant of workout_review.drift_of.

The golden part recomputes Pa:HR and Pw:HR on three real runs with a plain
loop that does not use workout_review's helpers, and checks it against

  * the review card (drift_of: first 10 min excluded, halves of the moving
    time) — equal to 1e-9;
  * WKO5's stored pahr / pwhr, which the season charts plot. WKO5 splits the
    *whole* recording at half its length and keeps the warm-up and stops
    (docs/wko5-internals/workout-metrics.md, "Halves"). Recomputed that way
    the stored values match to 5e-5 (pahr is stored rounded to 4 decimals)
    and 1e-9 (pwhr is not rounded).

The two definitions are not the same number: with the first 10 minutes left
out, these runs move by up to 9.4 percentage points (see DEFINITION_GAP),
downwards on all three. During the warm-up HR lags behind speed / power,
so a first half that includes it has a high output-per-beat and the
whole-run drift reads larger. (It is not always downwards: on 2026-08-03
the card reads 5.1 % against a stored 2.7 %; the card also drops stops,
which WKO5 keeps.) That is why the card says 「前 10 分鐘不算」
and why the season chart and the card can disagree on one run.
"""
import datetime as dt
import math
import os
import re
from pathlib import Path

import numpy as np
import pytest

from backend.engine import workout_review as R
from backend.engine.wko5expr import basis as BS
from backend.engine.wko5expr.customviews import CustomViewError, REPO_VIEWS, load_custom_views, parse_view


def _t(minutes):
    return np.arange(0, minutes * 60 + 1, 1.0)


# ---------------------------------------------------------------------------
# drift_of: Pw:HR with the same rules
# ---------------------------------------------------------------------------

def test_power_drift_uses_the_same_halves_and_warmup():
    t = _t(60)
    hr = np.where(t < 600, 100.0, np.linspace(135, 150, len(t)))   # low only in the warm-up
    v = np.full(len(t), 10.0)
    p = np.full(len(t), 200.0)
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["ok"] and r["pw_ok"]
    # constant speed and power: both bases see the same HR drift
    assert r["pw_drift"] == pytest.approx(r["drift"], abs=1e-12)
    assert r["pw_hr1"] == pytest.approx(r["hr1"]) and r["p1"] == pytest.approx(200.0)


def test_power_drift_differs_when_power_and_speed_do():
    t = _t(60)
    hr = np.full(len(t), 140.0)
    v = np.full(len(t), 10.0)
    p = np.where(t < 1800 + 600, 220.0, 200.0)       # power falls, speed holds
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert abs(r["drift"]) < 1e-9 and r["pw_drift"] > 0.05


def test_no_power_says_so_instead_of_zero():
    t = _t(60)
    r = R.drift_of(t, np.full(len(t), 140.0), np.full(len(t), 10.0))
    assert r["ok"] and not r["pw_ok"] and r["pw_drift"] is None
    assert r["pw_reason"] == "這次沒有功率"
    assert R.basis_drift(r, "power") == (None, "這次沒有功率")
    assert R.basis_drift(r, "pace")[0] == r["drift"]


def test_an_unfair_run_is_refused_on_both_bases():
    t = _t(60)
    r = R.drift_of(t, np.full(len(t), 140.0), np.full(len(t), 10.0), np.full(len(t), 200.0), trail=True)
    assert not r["ok"] and not r["pw_ok"]
    assert R.basis_drift(r, "power")[1] == R.basis_drift(r, "pace")[1] == r["reason"]


def test_aerobic_lines_power_mode_never_counts_the_streak():
    m = {"drift": {"ok": True, "drift": 0.02, "hr1": 140, "pw_ok": True, "pw_drift": 0.03, "pw_hr1": 140},
         "category": "road"}
    pace = R.aerobic_lines("easy", m, streak=3)
    power = R.aerobic_lines("easy", m, streak=3, basis="power")
    # the unsourced 「連續 3 次」 streak is gone on both bases (engine/quality_gate.py is the gate)
    assert pace and not any("連續" in ln or "間歇" in ln for ln in pace)
    assert power and "Pw:HR" in power[0] and not any("連續" in ln or "閾值下間歇" in ln for ln in power)


# ---------------------------------------------------------------------------
# the aerobic card on a fake dataset
# ---------------------------------------------------------------------------

def _run(day, power=None, minutes=50):
    from backend.tests.wko5_fakes import FakeWorkout
    t = _t(minutes)
    ch = {"elapsedtime": list(t), "heartrate": list(np.linspace(135, 140, len(t))),
          "speed": [10.0] * len(t), "elapseddistance": list(t * 10 / 3600.0)}
    if power is not None:
        ch["power"] = [float(power)] * len(t)
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": minutes * 60.0, "movingduration": minutes * 60.0,
                                "distance": minutes / 6, "climbing": 20.0})


def _card_text(card):
    return {s["name"]: s["data"]["value"] for s in card["series"] if s["data"]["kind"] == "value"}


def test_aerobic_card_shows_the_chosen_basis():
    from backend.tests.wko5_fakes import FakeDataset
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_run(today, power=200)], today, settings={"runthr": 160.0, "runftp": 300.0})
    w = ds.workouts[0]
    pace = _card_text(R.review(ds, w, "aerobic"))
    power = _card_text(R.review(ds, w, "aerobic", basis="power"))
    assert "Pa:HR 飄移" in pace and "Pw:HR 飄移" not in pace and "前半／後半速度" in pace
    assert "Pw:HR 飄移" in power and "Pa:HR 飄移" not in power and power["前半／後半功率"] == "200 → 200 W"
    # the summary card (and so the overview) stays on pace
    assert R.review(ds, w, "summary", basis="power")["series"] == R.review(ds, w, "summary")["series"]


def test_aerobic_card_without_power_in_power_mode():
    from backend.tests.wko5_fakes import FakeDataset
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_run(today)], today, settings={"runthr": 160.0, "runftp": 300.0})
    card = R.review(ds, ds.workouts[0], "aerobic", basis="power")
    text = _card_text(card)
    assert text["Pw:HR 飄移"] == "這次沒有功率"
    assert not any("0.0%" in v for v in text.values())


# ---------------------------------------------------------------------------
# chart spec and server rewrite
# ---------------------------------------------------------------------------

CHART = {"title": "心率飄移 Pa:HR", "description": "每下心跳換到的速度（m/min per bpm）。",
         "basis": {"default": "pace", "choices": ["pace", "power"], "power_note": "越野只當參考。"},
         "series": [{"name": "路跑 Pa:HR", "basis": "pace", "expression": "pahr"},
                    {"name": "路跑 Pw:HR", "basis": "power", "expression": "pwhr"},
                    {"name": "5%", "expression": "(,0.05)"}]}


def _parsed(chart):
    return parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [chart]}]})["dashboards"][0]["charts"][0]


def test_apply_basis_keeps_the_matching_series_and_retitles():
    ch = _parsed(CHART)
    pace, info = BS.apply_basis(ch, None)
    assert [s["name"] for s in pace["series"]] == ["路跑 Pa:HR", "5%"]
    assert pace["title"] == "心率飄移 Pa:HR" and "越野" not in pace["description"]
    assert info == {"basis": "pace", "basis_default": "pace", "basis_choices": ["pace", "power"],
                    "basis_labels": {"pace": "配速", "power": "功率"}, "basis_toggle": True}
    power, info = BS.apply_basis(ch, "power")
    assert [s["name"] for s in power["series"]] == ["路跑 Pw:HR", "5%"]
    assert power["title"] == "心率飄移 Pw:HR"
    assert "功率" in power["description"] and "W per bpm" in power["description"]
    assert power["description"].endswith("越野只當參考。") and power["basis_chosen"] == "power"
    assert BS.apply_basis(ch, "nonsense")[1]["basis"] == "pace"
    assert ch["series"][0]["basis"] == "pace"          # the input chart is not changed


def test_basis_spec_is_validated():
    with pytest.raises(CustomViewError):
        _parsed({**CHART, "basis": {"default": "hr", "choices": ["pace", "hr"]}})
    with pytest.raises(CustomViewError):
        _parsed({**CHART, "basis": None})               # tagged series without a chart basis


def test_no_power_note():
    ch = BS.apply_basis(_parsed({"title": "每公里心率與速度", "kind": "workout",
                                 "basis": {"default": "pace", "choices": ["pace", "power"]},
                                 "series": [{"name": "平均心率", "expression": "heartrate"},
                                            {"name": "功率", "basis": "power", "expression": "power"},
                                            {"name": "AeT", "expression": "(,aethr)"}]}), "power")[0]
    res = {"kind": "workout", "series": [
        {"name": "平均心率", "expression": "heartrate", "data": {"kind": "points", "points": [[0, 140]]}},
        {"name": "功率", "expression": "power", "data": {"kind": "points", "points": []}},
        {"name": "AeT", "expression": "(,aethr)", "data": {"kind": "hline", "y": 140}}], "empty": None}
    out = BS.no_power_note(res, ch, has_power=False)
    assert [s["name"] for s in out["series"]] == ["平均心率", "AeT"] and out["basis_note"] == "這次沒有功率"
    assert BS.no_power_note(res, ch, has_power=True) is res
    only = {**res, "series": [res["series"][1], res["series"][2]]}
    assert BS.no_power_note(only, ch, has_power=False)["empty"] == "這次沒有功率"


def test_bundled_drift_charts_have_the_toggle():
    views = load_custom_views([REPO_VIEWS])
    want = {("我的訓練", "心率飄移 Pa:HR（> 40 分鐘的跑步）"), ("我的訓練", "耐久度：長時間後段心率飄移"),
            ("周期化訓練", "長時間輕鬆跑的心率飄移"), ("周期化訓練", "耐久度：長時間後段心率飄移"),
            ("單次活動判讀", "飄移判讀"), ("單次活動判讀", "滾動有氧效率 EF（5 分鐘）"),
            ("單次活動判讀", "每公里心率與速度")}
    found = {(name, c["title"]) for name, v in views.items() for d in v["dashboards"]
             for c in d["charts"] if c.get("basis")}
    assert want <= found, want - found
    for name, v in views.items():
        for d in v["dashboards"]:
            for c in d["charts"]:
                if not c.get("basis") or c["kind"] == "review":
                    continue
                for b in ("pace", "power"):
                    got = BS.apply_basis(c, b)[0]
                    assert any(s.get("basis") == b for s in got["series"]), (c["title"], b)
                trail_power = any(s.get("basis") == "power" and re.search(r'(?<!!)hastag\("runningtrail"\)',
                                                                          s["expression"]) for s in c["series"])
                if trail_power:
                    # trail drift in power mode carries the Stryd grade caveat, pace mode doesn't
                    assert "8% 坡度" in BS.apply_basis(c, "power")[0]["description"], c["title"]
                    assert "Stryd" not in (BS.apply_basis(c, "pace")[0]["description"] or ""), c["title"]


# ---------------------------------------------------------------------------
# golden: three real runs
# ---------------------------------------------------------------------------

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\WKO5\Athlete"))
needs_data = pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
# road runs ≥ 40 min with Stryd power that drift_of accepts
RUNS = {"2026/Athlete_2026_09_15_20_40.wko4", "2026/Athlete_2026_08_28_20_35.wko4",
        "2026/Athlete_2026_08_27_20_33.wko4"}
# stored (whole run) vs card (10 min excluded), percentage points:
#   09-15  Pa:HR 2.72 vs 1.52   Pw:HR  1.35 vs −2.30
#   08-28  Pa:HR 9.93 vs 8.21   Pw:HR  9.26 vs  3.67
#   08-27  Pa:HR 6.55 vs −0.20  Pw:HR  6.61 vs −2.78
DEFINITION_GAP = 0.10


def plain_drift(t, hr, x, speed):
    """Card definition, written out: samples after the first 10 min, moving
    (gap ≤ 30 s, speed > 1.6 km/h), HR and x valid; split the kept time in
    half; drift = 1 − (x̄₂/HR̄₂)/(x̄₁/HR̄₁), time-weighted."""
    kept = []
    for i in range(1, len(t)):
        d = t[i] - t[i - 1]
        if not (0 < d <= 30) or t[i] - t[0] < 600:
            continue
        if math.isfinite(speed[i]) and speed[i] <= 1.6:
            continue
        if not (math.isfinite(hr[i]) and hr[i] > 0 and math.isfinite(x[i]) and x[i] > 0):
            continue
        kept.append((d, hr[i], x[i]))
    total = sum(k[0] for k in kept)
    sums = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    acc = 0.0
    for d, h, v in kept:
        acc += d
        s = sums[0 if acc <= total / 2 else 1]
        s[0] += d; s[1] += h * d; s[2] += v * d
    r1 = sums[0][2] / sums[0][1]
    r2 = sums[1][2] / sums[1][1]
    return (r1 - r2) / r1


def wko5_drift(t, hr, x):
    """WKO5's pahr / pwhr: halves (0, L/2] and (L/2, L] of the whole recording,
    each sample holding over (t[i−1], t[i]], invalid samples skipped."""
    L = t[-1]

    def avg(ch, lo, hi):
        s = w = 0.0
        prev = 0.0
        for i in range(len(t)):
            a, b = max(prev, lo), min(t[i], hi)
            prev = t[i]
            if b > a and math.isfinite(ch[i]):
                s += ch[i] * (b - a)
                w += b - a
        return s / w
    e1 = avg(x, 0, L / 2) / avg(hr, 0, L / 2)
    e2 = avg(x, L / 2, L) / avg(hr, L / 2, L)
    return (e1 - e2) / e1


@pytest.fixture(scope="module")
def real():
    from backend.engine.wko5expr.dataset import Dataset
    ds = Dataset(ATHLETE_DIR, today=dt.date(2026, 9, 29))
    ws = [w for w in ds.workouts if w.entry.file in RUNS]
    assert len(ws) == 3
    return ds, ws


def _chan(ds, w, name):
    return np.asarray(ds.channel(w.idx, name), dtype=float)


def _chart_point(ds, w, view, title, basis, name):
    from backend.engine.wko5expr.dataset import day_to_date
    from backend.engine.wko5expr.render import render_chart
    ch = next(c for d in load_custom_views([REPO_VIEWS])[view]["dashboards"] for c in d["charts"]
              if c["title"] == title)
    ch = BS.apply_basis(ch, basis)[0]
    day = math.floor(w.day)
    res = render_chart(ch, ds, day, day)
    s = next(s for s in res["series"] if s["name"] == name)
    iso = day_to_date(day).isoformat()
    pts = [p[1] for p in s["data"]["points"] if p[0].startswith(iso)]
    assert len(pts) == 1, pts
    return pts[0], res["title"]


@pytest.mark.golden
@needs_data
def test_card_matches_a_plain_recomputation(real):
    ds, ws = real
    for w in ws:
        t, hr, sp, pw = (_chan(ds, w, c) for c in ("elapsedtime", "heartrate", "speed", "power"))
        dr = R.measure(ds, w)["drift"]
        assert dr["ok"] and dr["pw_ok"], (w.entry.file, dr.get("reason"))
        assert dr["drift"] == pytest.approx(plain_drift(t, hr, sp, sp), abs=1e-9)
        assert dr["pw_drift"] == pytest.approx(plain_drift(t, hr, pw, sp), abs=1e-9)
        # and the card prints that number
        pace = _card_text(R.review(ds, w, "aerobic"))
        power = _card_text(R.review(ds, w, "aerobic", basis="power"))
        assert pace["Pa:HR 飄移"].startswith(R._pct(plain_drift(t, hr, sp, sp)))
        assert power["Pw:HR 飄移"].startswith(R._pct(plain_drift(t, hr, pw, sp)))


@pytest.mark.golden
@needs_data
def test_stored_pahr_pwhr_match_the_whole_run_recomputation(real):
    ds, ws = real
    for w in ws:
        t, hr, sp, pw = (_chan(ds, w, c) for c in ("elapsedtime", "heartrate", "speed", "power"))
        assert w.metrics["pahr"] == pytest.approx(wko5_drift(t, hr, sp), abs=5e-5)
        assert w.metrics["pwhr"] == pytest.approx(wko5_drift(t, hr, pw), abs=1e-9)
        # the definitions differ by the warm-up and stops (module docstring)
        assert abs(w.metrics["pahr"] - plain_drift(t, hr, sp, sp)) < DEFINITION_GAP
        assert abs(w.metrics["pwhr"] - plain_drift(t, hr, pw, sp)) < DEFINITION_GAP


@pytest.mark.golden
@needs_data
def test_season_chart_plots_the_stored_value_for_each_basis(real):
    ds, ws = real
    for w in ws:
        t, hr, sp, pw = (_chan(ds, w, c) for c in ("elapsedtime", "heartrate", "speed", "power"))
        y, title = _chart_point(ds, w, "我的訓練", "心率飄移 Pa:HR（> 40 分鐘的跑步）", "pace", "路跑 Pa:HR")
        assert "Pa:HR" in title and y == pytest.approx(wko5_drift(t, hr, sp), abs=5e-5)
        y, title = _chart_point(ds, w, "我的訓練", "心率飄移 Pa:HR（> 40 分鐘的跑步）", "power", "路跑 Pw:HR")
        assert "Pw:HR" in title and y == pytest.approx(wko5_drift(t, hr, pw), abs=1e-9)
