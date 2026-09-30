"""Charts from docs/research/competitor-charts.md §4 — formulas checked against
their sources (backend/engine/algorithms/chart_metrics.py) and, on the real
athlete, the view expressions checked against an independent recomputation.
The check results are recorded in docs/research/competitor-charts.md §7."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pytest

from backend.engine.algorithms import chart_metrics as CM

ROOT = Path(__file__).resolve().parents[2]


def _view(name):
    return json.loads((ROOT / "views" / f"{name}.json").read_text(encoding="utf8"))


def _chart(view, title_part):
    for d in _view(view)["dashboards"]:
        for c in d["charts"]:
            if title_part in (c.get("title") or ""):
                return c
    raise KeyError(title_part)


def _series(view, title_part, name_part):
    return next(s["expression"] for s in _chart(view, title_part)["series"] if name_part in s["name"])


# ---- worked examples from the sources ---------------------------------------------

@pytest.mark.parametrize("z, pi", [
    # Treff et al. 2019, Table 1 (TID in %, PI in a.U.)
    ((80, 0, 20), 3.18), ((72, 0, 28), 3.29), ((68, 6, 26), 2.47),
    ((67.3, 30.2, 2.5), 0.75), ((80.4, 17.9, 1.8), 0.91), ((74, 11, 15), 2.00),
])
def test_polarization_index_reproduces_treff_table_1(z, pi):
    z1, z2, z3 = (v / 100 for v in z)
    assert round(CM.polarization_index(z1, z2, z3), 2) == pi


def test_polarization_index_special_cases():
    assert CM.polarization_index(0.3, 0.3, 0.4) is None      # Z3 > Z1: not valid
    assert CM.polarization_index(0.8, 0.2, 0.0) == 0.0       # Z3 = 0: zero by definition
    # percentages instead of fractions would add +2 to every value
    assert CM.polarization_index(0.68, 0.06, 0.26) == pytest.approx(math.log10(0.68 / 0.06 * 0.26 * 100))


def test_foster_monotony_and_strain_by_hand():
    # mean 300/7, population SD sqrt(25000/7 - (300/7)^2) = 41.6497
    m, s = CM.monotony_strain([100, 0, 50, 0, 100, 0, 50])
    assert m == pytest.approx(42.857142 / 41.649656, rel=1e-5)
    assert s == pytest.approx(300 * m)
    # one session in seven days: 1/sqrt(6) — the floor Runalyze's newer
    # avg/(SD+avg) = 0.29 implies, i.e. the population SD
    m1, _ = CM.monotony_strain([0, 0, 0, 120, 0, 0, 0])
    assert m1 == pytest.approx(1 / math.sqrt(6))
    assert 1 / (1 + 1 / m1) == pytest.approx(0.29, abs=0.005)
    assert CM.monotony_strain([50] * 7)[0] is None             # SD 0


def test_course_constant_and_itra():
    # 6 h, 12 km, 1000 m up and down: 10.8 + 3.6 + 10 + 0.6
    assert CM.course_constant(6, 12, 1000, 1000) == pytest.approx(25.0)
    # ITRA worked example (as quoted by trailia.run): 42 km, 2000 m -> 62 -> S
    assert CM.km_effort(42, 2000) == pytest.approx(62)
    assert CM.itra_category(62) == "S"
    assert [CM.itra_category(x) for x in (24.9, 25, 44.9, 45, 114.9, 115, 209.9, 210)] == \
        ["XXS", "XS", "XS", "S", "M", "L", "XL", "XXL"]


def test_form_and_ewma():
    assert CM.form_pct(50, 65) == pytest.approx(-0.30)
    assert CM.form_zone(-0.31) == "high_risk" and CM.form_zone(-0.2) == "optimal"
    assert CM.form_zone(0.0) == "grey" and CM.form_zone(0.1) == "fresh" and CM.form_zone(0.3) == "transition"
    v = CM.ewma_loads([100] * 500, 42)
    assert v[41] == pytest.approx(100 * (1 - (41 / 42) ** 42))
    assert v[-1] == pytest.approx(100, rel=1e-4)


def test_downhill_weight_follows_its_sources():
    level_3ms = 3.0 * 3.6
    assert CM.downhill_weight(0.0, level_3ms) == pytest.approx(1.0)
    # Gottschall & Kram 2005: +54% impact peak at -9 deg, 3 m/s
    assert CM.downhill_weight(-math.tan(math.radians(9)), level_3ms) == pytest.approx(1.54)
    assert CM.downhill_weight(-0.40, level_3ms) == pytest.approx(1.54)       # held beyond -9 deg
    # Keller 1996: 1.2 BW at 1.5 m/s, 2.5 BW at 6 m/s
    assert CM.keller_fz(1.5) == pytest.approx(1.2) and CM.keller_fz(6.0) == pytest.approx(2.5)
    assert CM.keller_fz(0.5) == pytest.approx(1.2) and CM.keller_fz(9.0) == pytest.approx(2.5)
    top = CM.downhill_weight(-0.5, 30.0)
    assert top == pytest.approx(1.54 * 2.5 / (1.2 + 1.3 / 4.5 * 1.5))
    assert top < 3.0      # Garmin: downhill impact up to 3x the same speed on the flat
    # samples flatter than -3% or not moving count nothing
    assert CM.downhill_load([-0.02, -0.10, 0.1], [1, 1, 1], [10.8, 0, 10.8]) == 0.0


def test_downhill_expression_constants_match_the_reference():
    assert "0.158384" in CM.DOWNHILL_EXPR and abs(math.tan(math.radians(9)) - 0.158384) < 1e-6
    assert abs(1.3 / 4.5 - 0.288889) < 1e-6 and abs(CM.keller_fz(3.0) - 1.633333) < 1e-6
    # the weekly chart uses the same per-workout sum as the overview card
    for name in ("路跑", "越野跑", "登山健行"):
        assert CM.DOWNHILL_EXPR in _series("training", "每週下坡衝擊負荷", name)
    assert CM.DOWNHILL_EXPR in _series("training", "下坡負荷 近 7 天", "7 天")


def test_acute_chronic():
    assert CM.acute_chronic([1.0] * 28) == pytest.approx(1.0)
    assert CM.acute_chronic([0.0] * 21 + [4.0] * 7) == pytest.approx(4.0)
    assert CM.acute_chronic([0.0] * 28) is None


def test_climb_rate():
    g = [0.1] * 700 + [-0.1] * 700
    de = [0.1] * 700 + [-0.2] * 700
    dt_ = [1.0] * 1400
    v = [3.0] * 1400
    assert CM.climb_rate(g, de, dt_, v) == pytest.approx(360.0)
    assert CM.climb_rate(g, de, dt_, v, uphill=False) == pytest.approx(720.0)
    hr = [170.0] * 1400
    assert CM.climb_rate(g, de, dt_, v, hr, 160.0) is None          # all above LTHR
    assert CM.climb_rate(g[:500], de[:500], dt_[:500], v[:500]) is None   # < 10 min


# ---- the real athlete: view expressions vs independent recomputation --------------

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\WKO5\Athlete"))
TODAY = dt.date(2026, 9, 29)
needs_data = pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")


@pytest.fixture(scope="module")
def ds():
    from backend.engine.wko5expr.dataset import Dataset
    return Dataset(ATHLETE_DIR, today=TODAY)


@pytest.fixture(scope="module")
def ev(ds):
    from backend.engine.wko5expr.evaluator import Evaluator
    return Evaluator(ds, ds.today - 150, ds.today)


def _daily_tss(ds):
    first = int(math.floor(min(w.day for w in ds.workouts)))
    last = int(math.floor(ds.today))
    x = np.zeros(last - first + 1)
    for w in ds.workouts:
        t = w.metrics.get("tss")
        if t is not None and not math.isnan(t):
            x[int(math.floor(w.day)) - first] += t
    return first, x


def _days(ds, n=3):
    t = int(math.floor(ds.today))
    return [t - 1, t - 20, t - 60][:n]


@needs_data
@pytest.mark.golden
def test_form_pct_and_load_ratio_match_an_independent_pmc(ds, ev):
    first, x = _daily_tss(ds)
    ctl = CM.ewma_loads(x, ds.athlete.ctlconstant)
    atl = CM.ewma_loads(x, ds.athlete.atlconstant)
    form = ev.evaluate(_series("training", "Form%", "Form%"))
    ratio = ev.evaluate(_series("training", "負荷比", "ATL ÷ CTL"))
    for d in _days(ds):
        i = d - first
        assert form.at(d) == pytest.approx(CM.form_pct(ctl[i - 1], atl[i - 1]), rel=1e-6)
        assert ratio.at(d) == pytest.approx(atl[i] / ctl[i], rel=1e-6)


@needs_data
@pytest.mark.golden
def test_monotony_and_strain_match_foster_on_real_weeks(ds, ev):
    first, x = _daily_tss(ds)
    mono = ev.evaluate(_series("training", "單調度", "Monotony"))
    strain = ev.evaluate(_series("training", "單調度", "Strain"))
    checked = 0
    for d in _days(ds):
        i = d - first
        m, s = CM.monotony_strain(x[i - 6:i + 1])
        if m is None:
            continue
        assert mono.at(d) == pytest.approx(m, rel=1e-6)
        assert strain.at(d) == pytest.approx(s, rel=1e-6)
        assert 0.3 < m < 5
        checked += 1
    assert checked >= 2


def _hr_zone_weeks(ds, weeks):
    """{monday day: (z1, z2, z3, seconds)} from the raw heart-rate samples."""
    from backend.engine.wko5expr.dataset import day_to_date
    out = {}
    for w in ds.workouts:
        d = int(math.floor(w.day))
        mon = d - day_to_date(d).weekday()
        if mon not in weeks:
            continue
        hr = ds.channel(w.idx, "heartrate")
        t = ds.channel(w.idx, "elapsedtime")
        if hr is None or t is None:
            continue
        dtv = np.diff(t, prepend=0.0)
        aet, lt = ds.aethr(w), ds.sport_setting("thr", w)
        with np.errstate(invalid="ignore"):
            ok = hr > 0
            z = [np.nansum(dtv[ok & (hr < aet)]), np.nansum(dtv[ok & (hr >= aet) & (hr < lt)]),
                 np.nansum(dtv[ok & (hr >= lt)]), np.nansum(dtv[ok])]
        acc = out.setdefault(mon, [0.0, 0.0, 0.0, 0.0])
        for k in range(4):
            acc[k] += z[k]
    return {m: (a[0] / a[3], a[1] / a[3], a[2] / a[3], a[3]) for m, a in out.items() if a[3] > 0}


@needs_data
@pytest.mark.golden
def test_polarization_index_matches_raw_heart_rate_weeks(ds, ev):
    from backend.engine.wko5expr.dataset import date_to_day
    pi = ev.evaluate(_series("training", "極化指數", "PI"))
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    weeks = {int(date_to_day(mon - dt.timedelta(weeks=k))) for k in range(1, 12)}
    ref = _hr_zone_weeks(ds, weeks)
    checked = 0
    for m, (z1, z2, z3, secs) in sorted(ref.items()):
        want = CM.polarization_index(z1, z2, z3) if secs >= 3600 else None
        got = pi.at(m)
        if want is None:
            assert math.isnan(got)
        else:
            assert got == pytest.approx(want, abs=1e-6)
            assert 0 <= want < 4
            checked += 1
    assert checked >= 2


def _recent(ds, pred, n):
    ws = [w for w in ds.workouts if w.day <= ds.today and pred(w)]
    return ws[-n:]


@needs_data
@pytest.mark.golden
def test_sample_metrics_match_numpy_on_real_activities(ds, ev):
    trail = _recent(ds, lambda w: "runningtrail" in w.tags and (w.metrics.get("climbing") or 0) > 300, 2)
    hike = _recent(ds, lambda w: {"hiking", "mountaineering"} & set(w.tags)
                   and (w.metrics.get("climbing") or 0) > 1000, 1)
    assert len(trail) == 2 and len(hike) == 1
    up_t = _series("training", "上坡腳程", "越野跑")
    dn_t = _series("training", "下坡腳程", "越野跑")
    up_h = _series("training", "上坡腳程", "登山健行")
    rates = []

    def _same(got, want):
        if want is None:
            assert got is None or math.isnan(got)
        else:
            assert got == pytest.approx(want, rel=1e-6)

    for w in trail + hike:
        grade = ev.evaluate("rgrade", w)            # WKO5's own derived channel
        elev = ev.evaluate("_elevation", w)
        dist = ds.channel(w.idx, "elapseddistance")
        speed = ds.channel(w.idx, "speed")
        hr = ds.channel(w.idx, "heartrate")
        dtv = ds.channel(w.idx, "deltatime")
        de = np.diff(elev, prepend=np.nan)
        dd = np.diff(dist, prepend=np.nan)
        # downhill impact load
        got = ev.evaluate(CM.DOWNHILL_EXPR, w)
        want = CM.downhill_load(grade, dd, speed)
        assert got == pytest.approx(want, rel=1e-4)
        assert 0 < want < 3 * w.metrics["distance"]
        # up / downhill m/h
        lthr = ds.sport_setting("thr", w)
        is_trail = "runningtrail" in w.tags
        want_up = CM.climb_rate(grade, de, dtv, speed, hr, lthr)
        got_up = ev.evaluate(up_t if is_trail else up_h, w)
        _same(got_up, want_up)
        if want_up is not None:
            rates.append(want_up)
            assert 150 < want_up < 1500        # Naismith's rule of thumb is 600 m/h
        if is_trail:
            want_dn = CM.climb_rate(grade, de, dtv, speed, uphill=False)
            _same(ev.evaluate(dn_t, w), want_dn)
            assert want_dn is not None and 200 < want_dn < 2000
        # course constant and km-effort (workout chart)
        m = w.metrics
        cc = ev.evaluate(_series("workout", "路線難度", "コース定数"), w)
        assert cc == pytest.approx(CM.course_constant(m["movingduration"] / 3600, m["distance"],
                                                      m["climbing"], m["descending"]))
        ep = CM.km_effort(m["distance"], m["climbing"])
        assert ev.evaluate(_series("workout", "路線難度", "越野賽分級"), w) == CM.itra_category(ep)
    assert rates, "no activity had 10 min of climbing below LTHR"


@needs_data
@pytest.mark.golden
def test_descent_indicator_uses_the_chart_ratio(ds):
    from backend.engine.status import Status
    s = Status(ds, today=TODAY).compute()
    ind = next(i for i in s.indicators if i.id == "descent")
    assert ind.level in ("info", "watch", "na")
    if ind.value is not None:
        from backend.engine.wko5expr.evaluator import Evaluator
        r = Evaluator(ds, ds.today - 60, ds.today).evaluate(_series("training", "下坡負荷 近 7 天", "7 天"))
        assert ind.value == pytest.approx(r.at(int(math.floor(ds.today))), rel=1e-6)
