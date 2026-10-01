"""
配速／功率 basis toggle on the drift charts (backend/engine/wko5expr/basis.py)
and the Pw:HR variant of workout_review.drift_of.

The golden part recomputes Pa:HR and Pw:HR on real runs with plain loops
that do not use workout_review's helpers, and checks them against

  * the review card (drift_of v8: first 10 min excluded, ≥ 40 min of moving
    time after it, no fast finish, one window and halves for both bases) on
    the one road run in the data that passes (FAIR) — equal to 1e-9; the
    three RUNS it took until v7 have 33–35 min after the warm-up: refused;
  * the season drift charts (能力 心率飄移, periodization ②), which plot the
    card's number through drift("pace" | "power");
  * WKO5's stored pahr / pwhr (still plotted by the 耐久度 charts and WKO5's
    own imported charts). WKO5 splits the
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


SEASON_TITLE = "心率飄移 Pa:HR（暖身後 ≥ 40 分鐘的路跑，30–40 分為參考）"       # views/training.json 能力


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
    # power falls 4 %, speed holds (v11: > 5 % between the halves is refused — drift v2)
    p = np.where(t < 1800 + 600, 220.0, 212.0)
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert abs(r["drift"]) < 1e-9 and r["pw_drift"] > 0.025


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
    want = {("我的訓練", SEASON_TITLE), ("我的訓練", "耐久度：長時間後段心率飄移"),
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


def test_season_drift_charts_use_the_card_definition():
    """The 能力 心率飄移 and periodization ② charts plot drift() (the card's
    definition) and say so; the 耐久度 charts stay on WKO5's stored values
    (trail runs are all refused by drift_of) and say that too."""
    views = load_custom_views([REPO_VIEWS])
    charts = {(name, c["title"]): c for name, v in views.items() for d in v["dashboards"] for c in d["charts"]}
    for key in (("我的訓練", SEASON_TITLE), ("周期化訓練", "長時間輕鬆跑的心率飄移")):
        c = charts[key]
        drawn = [s for s in c["series"] if s.get("basis")]
        assert {s["basis"] for s in drawn} == {"pace", "power"}
        for s in drawn:
            assert "pahr" not in s["expression"] and "pwhr" not in s["expression"], s
            if s["name"].startswith("參考"):
                # v9: the 參考 tier as its own markers, labelled in the legend
                assert f'drift("{s["basis"]}", "ref")' in s["expression"] and s["line_style"] == "none", s
                assert "（暖身後 30–40 分，未達 UA 測試標準）" in s["name"]
            elif "平均" in s["name"]:
                # v11 (drift v2): the 6-run mean ± SE next to the single runs
                assert f'drift_avg("{s["basis"]}"' in s["expression"], s
            else:
                assert f'drift("{s["basis"]}")' in s["expression"], s
        assert sum("平均" in s["name"] for s in drawn) == 6
        assert sum(s["name"].startswith("參考") for s in drawn) == 2
        assert "參考（暖身後 30–40 分，未達 UA 測試標準）" in c["description"] and "推估" in c["description"]
        assert "6 次" in c["description"] and "標準誤" in c["description"]
        assert "判讀卡" in c["description"] and "WKO5 存的 Pa:HR" in c["description"]
        assert "WKO5 存的 Pw:HR" in BS.apply_basis(c, "power")[0]["description"]
    for key in (("我的訓練", "耐久度：長時間後段心率飄移"), ("周期化訓練", "耐久度：長時間後段心率飄移")):
        c = charts[key]
        assert any("pahr" in s["expression"] for s in c["series"]) and "WKO5 存的 Pa:HR" in c["description"]


# ---------------------------------------------------------------------------
# golden: three real runs
# ---------------------------------------------------------------------------

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\WKO5\Athlete"))
needs_data = pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
# flat ≥ 40-min road runs with Stryd power; drift_of took them until v8, which
# counts the 40 min after the warm-up (they have 33–35 min): now refused
RUNS = {"2026/Athlete_2026_09_15_20_40.wko4", "2026/Athlete_2026_08_28_20_35.wko4",
        "2026/Athlete_2026_08_27_20_33.wko4"}
# the one road run in the data with ≥ 40 min after the warm-up that drift_of accepts (41.1 min)
FAIR = "2025/Athlete_2025_06_30_20_42.wko4"
# stored (whole run) vs the warm-up-excluded definition (10 min excluded), percentage points:
#   09-15  Pa:HR 2.72 vs 1.52   Pw:HR  1.35 vs −2.30
#   08-28  Pa:HR 9.93 vs 8.21   Pw:HR  9.26 vs  3.67
#   08-27  Pa:HR 6.55 vs −0.20  Pw:HR  6.61 vs −2.78
DEFINITION_GAP = 0.10


def plain_card(t, hr, speed, power, floor=2400, start=600.0, end=None):
    """drift_of v8, written out without workout_review's helpers: keep the
    samples after the first 10 min that are moving (gap ≤ 30 s, speed > 1.6
    km/h) with HR, speed and power all valid (one window for both bases;
    power covers 100 % on these runs); ≥ `floor` s kept (2400 = the strict
    tier, 1800 = v9's 參考 tier); the last 10 % of the kept time ≤ 5 % above
    the rest for power and for speed; then halves. v11: `start` / `end` (s
    after the first sample, end exclusive) = drift_of's window (the adaptive
    start, the return-leg cool-down and the trailing-idle cut)."""
    kept = []
    for i in range(1, len(t)):
        d = t[i] - t[i - 1]
        if not (0 < d <= 30) or t[i] - t[0] < start or (end is not None and t[i] - t[0] >= end):
            continue
        if math.isfinite(speed[i]) and speed[i] <= 1.6:
            continue
        if not all(math.isfinite(c[i]) and c[i] > 0 for c in (hr, speed, power)):
            continue
        kept.append((d, hr[i], speed[i], power[i]))
    total = sum(k[0] for k in kept)
    out = {"measured_s": total, "ok": total >= floor}
    if not out["ok"]:
        return out
    for j, name in ((3, "power"), (2, "speed")):
        acc = 0.0
        a = [0.0, 0.0]
        b = [0.0, 0.0]
        for k in kept:
            acc += k[0]
            s = b if acc > 0.9 * total else a
            s[0] += k[0]
            s[1] += k[j] * k[0]
        out[f"finish_{name}"] = (b[1] / b[0]) / (a[1] / a[0]) - 1
    if max(out["finish_power"], out["finish_speed"]) > 0.05:
        out["ok"] = False
        return out
    for j, name in ((2, "pa"), (3, "pw")):
        sums = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        acc = 0.0
        for k in kept:
            acc += k[0]
            s = sums[0 if acc <= total / 2 else 1]
            s[0] += k[0]; s[1] += k[1] * k[0]; s[2] += k[j] * k[0]
        r1, r2 = sums[0][2] / sums[0][1], sums[1][2] / sums[1][1]
        out[name] = (r1 - r2) / r1
    return out


def plain_drift(t, hr, x, speed):
    """The warm-up-excluded definition (drift_of up to v7, no 40-min-after-warm-up
    floor), written out: samples after the first 10 min, moving
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
    ds.activity_temps = {}               # no route_weather archive; these files have no watch temperature
    ws = [w for w in ds.workouts if w.entry.file in RUNS]
    fair = [w for w in ds.workouts if w.entry.file == FAIR]
    assert len(ws) == 3 and len(fair) == 1
    return ds, ws, fair[0]


def _chan(ds, w, name):
    return np.asarray(ds.channel(w.idx, name), dtype=float)


def _chart_point(ds, w, view, title, basis, name):
    """(the chart's value on the workout's day or None when nothing is drawn, title)."""
    from backend.engine.wko5expr.dataset import day_to_date
    from backend.engine.wko5expr.render import render_chart
    ch = next(c for d in load_custom_views([REPO_VIEWS])[view]["dashboards"] for c in d["charts"]
              if c["title"] == title)
    ch = BS.apply_basis(ch, basis)[0]
    day = math.floor(w.day)
    res = render_chart(ch, ds, day, day)
    s = next(s for s in res["series"] if s["name"] == name)
    iso = day_to_date(day).isoformat()
    pts = [p[1] for p in (s["data"].get("points") or []) if p[0].startswith(iso) and p[1] is not None]
    assert len(pts) <= 1, pts
    return (pts[0] if pts else None), res["title"]


@pytest.mark.golden
@needs_data
def test_card_matches_a_plain_recomputation(real):
    ds, ws, fair = real
    t, hr, sp, pw = (_chan(ds, fair, c) for c in ("elapsedtime", "heartrate", "speed", "power"))
    assert plain_card(t, hr, sp, pw)["ok"]          # the v8 window: ≥ 40 min after the warm-up
    dr = R.measure(ds, fair)["drift"]
    # v11 (drift v2): the return leg through the city is the cool-down (drift-algorithm.md §1.1:
    # 41.1 → 35.9 min, so this run drops to the 參考 tier — the honest result)
    assert dr["tail"] is not None and dr["tier"] in ("test", "ref"), dr.get("reason")
    plain = plain_card(t, hr, sp, pw, floor=1800, start=dr["warmup_s"], end=dr["end_s"])
    assert plain["ok"]
    assert dr["tier"] == ("test" if plain["measured_s"] >= 2400 else "ref")
    assert dr["measured_s"] == pytest.approx(plain["measured_s"], abs=1e-6)
    assert dr["finish"] == pytest.approx(max(plain["finish_power"], plain["finish_speed"]), abs=1e-9)
    assert R.basis_drift(dr, "pace", ref=True)[0] == pytest.approx(plain["pa"], abs=1e-9)
    assert R.basis_drift(dr, "power", ref=True)[0] == pytest.approx(plain["pw"], abs=1e-9)
    # and the card prints that number, with its ± SE
    pace = _card_text(R.review(ds, fair, "aerobic"))
    power = _card_text(R.review(ds, fair, "aerobic", basis="power"))
    assert pace["Pa:HR 飄移"].startswith(R._pct(plain["pa"])) and " pp" in pace["Pa:HR 飄移"]
    assert power["Pw:HR 飄移"].startswith(R._pct(plain["pw"]))
    assert pace["溫度"].startswith("沒有溫度資料") and "回程市區段" in pace["已排除"]
    assert pace["飄移等級"].startswith("嚴格" if dr["tier"] == "test" else "參考")
    # the three runs the card took until v7: < 40 min after the warm-up, refused by the strict
    # tier on both bases; with a value only when the v2 window keeps ≥ 30 min and the v2 gate passes
    for w in ws:
        t, hr, sp, pw = (_chan(ds, w, c) for c in ("elapsedtime", "heartrate", "speed", "power"))
        dr = R.measure(ds, w)["drift"]
        assert not dr["ok"] and R.basis_drift(dr, "power")[0] is None
        if dr["tier"] == "ref":
            ref = plain_card(t, hr, sp, pw, floor=1800, start=dr["warmup_s"], end=dr["end_s"])
            assert ref["ok"], w.entry.file
            assert R.basis_drift(dr, "pace", ref=True)[0] == pytest.approx(ref["pa"], abs=1e-9)
            assert R.basis_drift(dr, "power", ref=True)[0] == pytest.approx(ref["pw"], abs=1e-9)
        else:
            assert dr["tier"] is None and dr["reason"], w.entry.file


@pytest.mark.golden
@needs_data
def test_stored_pahr_pwhr_match_the_whole_run_recomputation(real):
    ds, ws, _ = real
    for w in ws:
        t, hr, sp, pw = (_chan(ds, w, c) for c in ("elapsedtime", "heartrate", "speed", "power"))
        assert w.metrics["pahr"] == pytest.approx(wko5_drift(t, hr, sp), abs=5e-5)
        assert w.metrics["pwhr"] == pytest.approx(wko5_drift(t, hr, pw), abs=1e-9)
        # the definitions differ by the warm-up and stops (module docstring)
        assert abs(w.metrics["pahr"] - plain_drift(t, hr, sp, sp)) < DEFINITION_GAP
        assert abs(w.metrics["pwhr"] - plain_drift(t, hr, pw, sp)) < DEFINITION_GAP


@pytest.mark.golden
@needs_data
def test_season_charts_plot_the_cards_drift_for_each_basis(real):
    """The season drift charts plot the card's number (drift()), not WKO5's
    stored pahr / pwhr: equal on the fair run, nothing drawn on refused ones."""
    ds, ws, fair = real
    charts = (("我的訓練", SEASON_TITLE, "路跑 Pa:HR", "路跑 Pw:HR"),
              ("周期化訓練", "長時間輕鬆跑的心率飄移", "飄移 Pa:HR", "飄移 Pw:HR"))
    for w in [fair] + sorted(ws, key=lambda x: x.day)[:2]:
        dr = R.measure(ds, w)["drift"]
        want_pa, want_pw = R.basis_drift(dr, "pace")[0], R.basis_drift(dr, "power")[0]
        for view, title, pa_name, pw_name in charts:
            y, t1 = _chart_point(ds, w, view, title, "pace", pa_name)
            assert "Pw:HR" not in t1 and (y is None if want_pa is None else y == pytest.approx(want_pa, abs=1e-9))
            y, t2 = _chart_point(ds, w, view, title, "power", pw_name)
            assert (y is None if want_pw is None else y == pytest.approx(want_pw, abs=1e-9))
            if view == "我的訓練":
                assert "Pw:HR" in t2
        if w is fair:
            # v11: the return-leg cool-down can drop it to the 參考 tier (plotted by the 參考 series)
            assert (want_pa is not None and want_pw is not None) or R.drift_tier(dr) == "ref"
            # …and differs from WKO5's stored whole-run value
            shown = R.basis_drift(dr, "pace", ref=True)[0]
            assert shown is not None and abs(shown - w.metrics["pahr"]) > 1e-4
        else:
            assert want_pa is None and want_pw is None and w.metrics["pahr"] is not None
    # v9: the 參考 series plot exactly the reference-tier runs (and nothing on the strict one)
    ref_pa = "參考 Pa:HR（暖身後 30–40 分，未達 UA 測試標準）"
    ref_pw = "參考 Pw:HR（暖身後 30–40 分，未達 UA 測試標準）"
    for w in [fair] + ws:
        dr = R.measure(ds, w)["drift"]
        is_ref = R.drift_tier(dr) == "ref"
        for view, title, _, _ in charts:
            y, _ = _chart_point(ds, w, view, title, "pace", ref_pa)
            assert (y == pytest.approx(R.basis_drift(dr, "pace", ref=True)[0], abs=1e-9)) if is_ref else y is None
            y, _ = _chart_point(ds, w, view, title, "power", ref_pw)
            assert (y == pytest.approx(R.basis_drift(dr, "power", ref=True)[0], abs=1e-9)) if is_ref else y is None
