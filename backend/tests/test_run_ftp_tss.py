"""Run FTP for power TSS on a COROS / TP source (docs/plans/run-ftp-auto.plan.md
part 1): the plan's CP test on / before the date -> athlete_settings
run_ftp_w -> the Stryd-only PD mFTP as of the date (推估) -> none (rTSS /
hrTSS), the same value as `ds.cp`; watch power neither scores power TSS nor
feeds the fit; a run that falls back to hrTSS gets the moving-time hrTSS and
the elevation bonus (tss_source, not "has NP"); parity mode keeps WKO5's
rule. Synthetic FITs only (fit_builder); the PD fit and the LTHR estimate are
stubbed — never the WKO5 folder, the real app DB or ~/.wko5coach."""
import datetime as dt
from datetime import datetime, timezone

import pytest

from backend.engine import power_source as PS
from backend.engine.planning import Plan, Threshold
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 10, 2)
UTC = timezone.utc
MFTP = 175.0
PLAN = Plan(thresholds=[Threshold(date="2026-07-01", lthr=150.0), Threshold(date="2026-09-20", cp=250.0)])


def _t(month, day):
    return datetime(2026, month, day, 8, tzinfo=UTC)


# Aug 1: a Stryd trail run (climbing, a 5-min stop) before the first PD fit;
# Aug 2-6: Stryd runs (the fit needs >= CP_FIT_MIN_RUNS); Sep 10: Stryd, after
# the fit (Aug 31), before the CP test; Sep 12: watch power; Sep 25: Stryd,
# after the CP test (2026-09-20)
RUNS = [dict(start=_t(8, 1), power=220, stryd=True, sub_sport=3, climb_m_per_s=0.3,
             speeds_m_s=[3.0] * 1500 + [0.0] * 300)] + \
       [dict(start=_t(8, d), seconds=1800, power=220, stryd=True) for d in range(2, 7)] + \
       [dict(start=_t(9, 10), seconds=1800, power=200, stryd=True),
        dict(start=_t(9, 12), seconds=1800, power=400),
        dict(start=_t(9, 25), seconds=1800, power=200, stryd=True)]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Tenant plan = PLAN; the Stryd PD fit -> MFTP (records the runs it is
    given); the as-of LTHR estimate -> nothing (the plan's LTHR is used)."""
    from backend.engine import planning
    from backend.engine import thresholds as TH
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import weather as WX
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: PLAN))
    monkeypatch.setattr(WX, "HOME", tmp_path / "home")          # no synced FIT folder for cptest.curves
    fitted = []
    monkeypatch.setattr(A, "pd_model", lambda ds, day, runs, ref, any_power=False:
                        fitted.append(list(runs)) or {"mftp": MFTP, "frc": 15000.0, "n_points": 40})
    monkeypatch.setattr(TH, "estimate", lambda ds, day, cp_of=None: {})
    return fitted


def _ds(tmp_path, config=None, estimate=True, settings=()):
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    for i, kw in enumerate(RUNS):
        (d / f"{i:02d}.fit").write_bytes(build_run(**kw))
    return FitFolderDataset(tmp_path / "fit" / "coros", config=config or EngineConfig(parity=False),
                            today=TODAY, classifications={}, athlete_settings=list(settings),
                            estimate_thresholds=estimate, tz=UTC)


def _by_day(ds):
    return {w.entry.start.date(): w for w in ds.workouts}


def _power_tss(w, ftp):
    m = w.metrics
    return m["np"] ** 2 * m["tssduration"] / (ftp ** 2 * 36.0)


def test_stryd_run_without_a_cp_test_uses_the_pd_mftp(tmp_path, env):
    ds = _ds(tmp_path)
    w = _by_day(ds)[dt.date(2026, 9, 10)]
    m = w.metrics
    assert m["tss_source"] == "power"
    assert m["ftp_used"] == MFTP and m["ftp_source"].startswith("推估：Stryd PD 模型 mFTP")
    assert m["tss"] == pytest.approx(_power_tss(w, MFTP))
    assert m["tss"] == pytest.approx((200 / MFTP) ** 2 * 50, rel=0.03)       # 30 min at 200 W
    assert ds.cp(w) == m["ftp_used"]                                          # TSS FTP == the charts' CP


def test_the_plan_cp_test_applies_from_its_date_on(tmp_path, env):
    ds = _ds(tmp_path)
    days = _by_day(ds)
    after, before = days[dt.date(2026, 9, 25)], days[dt.date(2026, 9, 10)]
    assert after.metrics["ftp_used"] == 250.0 and after.metrics["ftp_source"] == "你的測試 2026-09-20"
    assert after.metrics["tss"] == pytest.approx(_power_tss(after, 250.0))
    assert before.metrics["ftp_used"] == MFTP                                 # never backwards
    assert ds.cp(after) == 250.0


def test_a_db_run_ftp_wins_over_the_estimate(tmp_path, env):
    ds = _ds(tmp_path, settings=[{"effective_date": "2026-09-01", "run_ftp_w": 190.0}])
    days = _by_day(ds)
    w = days[dt.date(2026, 9, 10)]
    assert w.metrics["ftp_used"] == 190.0 and w.metrics["ftp_source"].startswith("athlete_settings")
    assert days[dt.date(2026, 9, 25)].metrics["ftp_used"] == 250.0          # the test still wins


def test_watch_power_scores_no_power_tss_and_is_never_fitted(tmp_path, env):
    ds = _ds(tmp_path)
    watch = _by_day(ds)[dt.date(2026, 9, 12)]
    assert ds.power_source(watch) == PS.WATCH
    m = watch.metrics
    assert m["power_tss_blocked"] and m["tss_source"] == "hrtss" and m["ftp_used"] is None
    assert m["tss"] is not None and m["np"] == pytest.approx(400, rel=0.01)
    assert env and all(ds.power_source(r) == PS.STRYD for runs in env for r in runs)


def test_a_power_run_without_ftp_gets_moving_hrtss_and_the_elevation_bonus(tmp_path, env):
    cfg = EngineConfig(parity=False, hr_tss_moving_only=True, elevation_tss_per_1000ft=10.0)
    ds = _ds(tmp_path, config=cfg)
    w = _by_day(ds)[dt.date(2026, 8, 1)]                    # before the first PD fit: no FTP
    m = w.metrics
    assert m["np"] is not None and m["tss_source"] == "hrtss" and ds._is_hr_sourced(w)
    assert m["hrtss_moving"] < m["hrtss"]                   # the 5-min stop is not charged
    assert m["elevation_tss"] == pytest.approx(cfg.elevation_bonus(m["climbing"], w.sport_type))
    assert m["elevation_tss"] > 0
    assert m["tss"] == pytest.approx(m["hrtss_moving"] + m["elevation_tss"])
    # a power-TSS run gets neither
    p = _by_day(ds)[dt.date(2026, 9, 10)]
    assert not ds._is_hr_sourced(p) and "elevation_tss" not in p.metrics and "hrtss_moving" not in p.metrics


def test_without_estimates_only_the_plan_and_db(tmp_path, env):
    ds = _ds(tmp_path, estimate=False)
    days = _by_day(ds)
    assert not env                                          # no PD fit
    assert days[dt.date(2026, 9, 10)].metrics["tss_source"] == "hrtss"
    assert days[dt.date(2026, 9, 25)].metrics["ftp_used"] == 250.0


def test_parity_mode_keeps_wkos_ftp_setting(tmp_path, env):
    ds = _ds(tmp_path, config=EngineConfig(parity=True),
             settings=[{"effective_date": "2026-01-01", "run_ftp_w": 250.0}])
    for w in ds.workouts:
        m = w.metrics
        assert m["tss_source"] == "power"                   # parity reads watch power too
        assert m["ftp_used"] == 250.0 and m["tss"] == pytest.approx(_power_tss(w, 250.0))


def test_the_wko5_opt_in_keeps_wkos_ftp_setting(tmp_path, env):
    ds = _ds(tmp_path, estimate=False)
    ds.settings_from = "wko5"                               # as with charts.fit_settings_from_wko5
    ds.athlete.settings["runftp"] = [(dt.date(1980, 1, 1), 250.0)]
    w = _by_day(ds)[dt.date(2026, 9, 10)]
    assert ds.tss_ftp(w) == (250.0, "WKO5 athlete 檔（選用）")
    w.metrics = ds._metrics(w)
    assert w.metrics["tss"] == pytest.approx(_power_tss(w, 250.0))
