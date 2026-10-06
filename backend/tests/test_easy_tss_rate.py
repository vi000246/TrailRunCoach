"""SP-302: an easy run's planned TSS per hour from genuinely easy runs only
(overview._tss_per_hour / easy_tss_rates); < 3 → the easy cap's IF (推估); the long run's and
the quality sessions' rates unchanged."""
import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import overview as O
from backend.engine import workout_review as WR

TODAY = dt.date(2026, 10, 6)


def _w(i, tss_h, cls, trail=False, hours=1.0):
    """A run of `hours` at `tss_h` TSS / h; `cls` = the classifier's answer."""
    return SimpleNamespace(idx=i, sport="run", sport_type="trail running" if trail else "running",
                           tags=["runningtrail"] if trail else [], metrics={"movingduration": hours * 3600,
                                                                            "tss": tss_h * hours},
                           cls=cls)


EASY = {"type": "easy", "moderate": False, "stim": {"easy_hr": True}}
MODERATE = {"type": "easy", "moderate": True, "stim": {"easy_hr": False}}
Z3 = {"type": "quality", "stimulus": "z3", "stim": {"easy_hr": False}}
Z5_LOW_HR = {"type": "quality", "stimulus": "z5", "stim": {"easy_hr": True}}     # power says Zone 5
LONG_EASY = {"type": "long", "stim": {"easy_hr": True}}
LONG_HARD_HR = {"type": "long", "stim": {"easy_hr": False}}


@pytest.fixture
def history(monkeypatch):
    def use(ws):
        from backend.engine.algorithms import classify as CL
        monkeypatch.setattr(O, "workouts_between", lambda ds, a, b: list(ws))
        monkeypatch.setattr(CL, "is_trail", lambda w: "runningtrail" in w.tags)
        monkeypatch.setattr(WR, "classify", lambda ds, w, m=None: w.cls)
    return use


def test_easy_rate_reads_only_the_easy_runs(history):
    ws = [_w(1, 50, EASY), _w(2, 52, EASY), _w(3, 54, LONG_EASY),
          _w(4, 90, Z3), _w(5, 95, Z5_LOW_HR), _w(6, 80, MODERATE), _w(7, 82, MODERATE), _w(8, 85, LONG_HARD_HR)]
    history(ws)
    tph = O._tss_per_hour(None, TODAY, 150.0, 170.0)
    info = tph.pop("easy_info")
    assert tph["easy"] == pytest.approx(52.0)                  # median of 50 / 52 / 54 (the easy LSD counts)
    assert info == {"n": 3, "estimated": False, "rate": 52.0, "source": O.SRC_EASY_TSS}
    # the category rate (long runs) is the all-runs median as before
    assert tph["road"] == pytest.approx(81.0)


def test_fewer_than_three_easy_runs_estimates_from_the_cap(history):
    history([_w(1, 50, EASY), _w(2, 52, EASY), _w(3, 90, Z3), _w(4, 80, MODERATE), _w(5, 85, MODERATE)])
    tph = O._tss_per_hour(None, TODAY, 150.0, 170.0)
    info = tph.pop("easy_info")
    assert tph["easy"] == pytest.approx(round((150 / 170) ** 2 * 100, 1))      # IF 0.88 → 77.9
    assert info["estimated"] and info["n"] == 2 and "推估" in info["source"] and "LTHR" in info["source"]
    assert tph["road"] == pytest.approx(80.0)                                    # long run: unchanged
    # no cap / LTHR: the road default, still labelled 推估
    tph = O._tss_per_hour(None, TODAY, None, 170.0)
    assert tph["easy"] == O.TSS_PER_HOUR_DEFAULT["road"] and tph["easy_info"]["source"] == O.SRC_EASY_TSS_DEFAULT
    assert O.easy_cap_rate(180.0, 170.0) == 100.0                                # IF capped at 1


def test_trail_runs_fill_in_and_have_their_own_rate(history):
    # a trail runner: 1 easy road run, 3 easy trail runs → "easy" from all four; "easy_trail" the trail ones
    history([_w(1, 70, EASY), _w(2, 40, EASY, trail=True), _w(3, 44, EASY, trail=True),
             _w(4, 48, EASY, trail=True), _w(5, 75, Z3, trail=True)])
    tph = O._tss_per_hour(None, TODAY, 150.0, 170.0)
    assert tph["easy"] == pytest.approx(46.0) and tph["easy_trail"] == pytest.approx(44.0)
    assert tph["trail"] == pytest.approx(46.0)                                   # trail long runs: unchanged
    # < 3 easy trail runs: a trail easy run takes the easy rate
    history([_w(i, 50 + i, EASY) for i in range(4)] + [_w(9, 60, EASY, trail=True)])
    tph = O._tss_per_hour(None, TODAY, 150.0, 170.0)
    assert tph["easy_trail"] == tph["easy"] == pytest.approx(51.5)


def test_plan_prefs_and_the_editor_use_the_easy_rate():
    from backend.api import plan_sessions as API
    from backend.engine import plan_prefs as PP
    rates = {"road": 80.0, "trail": 60.0, "easy": 50.0, "easy_trail": 45.0, "strength": 25.0}
    c = PP.Ctx(kind="base", mode="build", allow_quality=True, rates=rates, aet=150.0)
    assert PP._easy(None, 0, 60, PP.Prefs(), c)["tss"] == pytest.approx(50.0)
    assert PP._easy(None, 0, 60, PP.Prefs(terrain_easy="trail"), c)["tss"] == pytest.approx(45.0)
    assert PP._easy(None, 0, 60, PP.Prefs(terrain_easy="road"), c)["tss"] == pytest.approx(50.0)
    # rates without the easy keys (an older stored plan): the category rates as before
    old = PP.Ctx(kind="base", mode="build", allow_quality=True, rates={"road": 80.0, "trail": 60.0}, aet=150.0)
    assert PP._easy(None, 0, 60, PP.Prefs(), old)["tss"] == pytest.approx(80.0)
    r = API.tss_rates(rates, [])
    assert r["easy"] == 50.0 and r["long"] == 80.0 and r["quality"] == 70.0
    assert API.tss_rates({"road": 80.0}, [])["easy"] == 80.0


def test_week_plan_prices_easy_runs_with_the_easy_rate():
    from backend.tests.test_overview import BOTH, _two_track_week
    wp = _two_track_week(BOTH)
    tph, info = wp["tss_per_category"], wp["easy_tss"]
    assert "easy" in tph and "easy_info" not in tph and set(info) == {"n", "estimated", "rate", "source"}
    easy = [s for s in wp["sessions"] if s["kind"] == "easy" and s["minutes"]]
    # (the minutes are rounded to 5 after the TSS: within one 5-min step)
    assert easy and all(s["tss"] / s["minutes"] * 60 == pytest.approx(tph["easy"], rel=2.5 / s["minutes"])
                        for s in easy)
    assert any(n.get("src") == "easy_tss" for n in wp["notes"]) == info["estimated"]
