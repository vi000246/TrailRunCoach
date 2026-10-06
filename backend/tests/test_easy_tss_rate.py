"""SP-302: an easy run's planned TSS per hour from genuinely easy runs only
(overview._tss_per_hour / easy_tss_rates); < 3 → IF 0.80 = 64 TSS / h (推估; owner 2026-10-06, was
the easy cap's IF); the long run's and the quality sessions' rates unchanged; the projection's
default path (no 課表偏好) prices easy runs with the same rate as week_plan."""
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


def test_fewer_than_three_easy_runs_estimate_if_0_80(history):
    """Owner 2026-10-06: < 3 easy runs → IF 0.80 (≈ 64 TSS / h, 推估), not the easy cap ÷ LTHR (which gave
    150 / 170 → 77.9 here, more than the easy runs it stands for)."""
    history([_w(1, 50, EASY), _w(2, 52, EASY), _w(3, 90, Z3), _w(4, 80, MODERATE), _w(5, 85, MODERATE)])
    tph = O._tss_per_hour(None, TODAY, 150.0, 170.0)
    info = tph.pop("easy_info")
    assert O.EASY_EST_IF == 0.80 and tph["easy"] == pytest.approx(64.0)
    assert info == {"n": 2, "estimated": True, "rate": 64.0, "source": O.SRC_EASY_TSS_EST}
    assert "推估" in info["source"] and "0.80" in info["source"]
    assert tph["road"] == pytest.approx(80.0)                                    # long run: unchanged
    # no cap / LTHR: the same estimate
    tph = O._tss_per_hour(None, TODAY, None, None)
    assert tph["easy"] == pytest.approx(64.0) and tph["easy_info"]["source"] == O.SRC_EASY_TSS_EST


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


def test_projection_default_path_prices_easy_runs_like_week_plan():
    """The reported inconsistency: without 課表偏好 the projection priced easy runs at the all-runs
    rate (target tss_per_hour) while week_plan used the SP-302 easy rate — now both use "easy"."""
    from backend.engine import projection as P
    from backend.tests.test_overview import BOTH, _two_track_week
    wp = _two_track_week(BOTH)
    wp["tss_per_category"] = {**wp["tss_per_category"], "easy": 41.0}             # far from the all-runs rate
    assert abs(float(wp["target"]["tss_per_hour"]) - 41.0) > 5
    weeks = P.project_weeks(wp, [], dt.date.fromisoformat(wp["week"]["start"]) + dt.timedelta(weeks=3))
    easy = [s for w in weeks for s in w["sessions"] if s["kind"] == "easy" and s["minutes"] and s["id"].startswith("easy")]
    assert easy and all(s["tss"] / s["minutes"] * 60 == pytest.approx(41.0, rel=2.5 / s["minutes"]) for s in easy)
    # the week_sessions default path directly: rates' "easy", else the plain rate as before
    from backend.tests.test_plan_prefs import MON, TGT
    ss = P.week_sessions(MON, "base", "base", 5.0, 70.0, TGT, 5, 90.0, False, False, 17.5, 150.0,
                         rates={"easy": 41.0})
    old = P.week_sessions(MON, "base", "base", 5.0, 70.0, TGT, 5, 90.0, False, False, 17.5, 150.0)
    for new_s, old_s in zip(ss, old):
        if new_s["kind"] == "easy":
            assert new_s["tss"] == pytest.approx(old_s["tss"] * 41.0 / 70.0)
        else:
            assert new_s == old_s
