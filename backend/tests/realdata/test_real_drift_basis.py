"""Real-data half of backend/tests/test_drift_basis.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import datetime as dt
import math
import numpy as np
import pytest
from backend.engine import workout_review as R
from backend.engine.wko5expr import basis as BS
from backend.engine.wko5expr.customviews import REPO_VIEWS, load_custom_views
from backend.tests.realdata._paths import ATHLETE_DIR
from backend.tests.test_drift_basis import SEASON_TITLE, _card_text


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
