"""Real-data half of backend/tests/test_chart_metrics.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import datetime as dt
import math
import numpy as np
import pytest
from backend.engine.algorithms import chart_metrics as CM
from backend.tests.realdata._paths import ATHLETE_DIR
from backend.tests.test_chart_metrics import DOWNHILL_RATIO_EXPR, _series


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
        # the athlete folder keeps growing past the pinned TODAY; later workouts don't belong in this PMC
        if t is not None and not math.isnan(t) and int(math.floor(w.day)) <= last:
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
def test_descent_indicator_matches_the_expression_ratio(ds):
    from backend.engine.status import Status
    s = Status(ds, today=TODAY).compute()
    ind = next(i for i in s.indicators if i.id == "descent")
    assert ind.level in ("info", "watch", "na")
    if ind.value is not None:
        from backend.engine.wko5expr.evaluator import Evaluator
        r = Evaluator(ds, ds.today - 60, ds.today).evaluate(DOWNHILL_RATIO_EXPR)
        assert ind.value == pytest.approx(r.at(int(math.floor(ds.today))), rel=1e-6)
