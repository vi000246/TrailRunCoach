"""SP-336 (SP-320 ⑤): a chart's render-cache key covers only the activities its range (plus
warm-up) reads, `today` only when the chart reads it, and only the code its chart type runs.

Synthetic data only (FakeDataset): four years of daily runs."""
import datetime as dt

import pytest

from backend.engine import codehash as CH
from backend.engine.wko5expr import chartscope as CS
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.evaluator import Evaluator
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)
T = int(date_to_day(TODAY))
E = T - 1                 # a range that ends yesterday: today is not in its key


def _runs(days_back, skip=()):
    return [FakeWorkout(dt.datetime.combine(TODAY - dt.timedelta(days=k), dt.time(7)),
                        metrics={"tss": 40.0 + k % 30, "duration": 3600.0, "movingduration": 3500.0,
                                 "distance": 10.0})
            for k in range(days_back) if k not in skip]


def _ds(workouts, today=TODAY, tmp=None):
    d = FakeDataset(workouts, today)
    d.config = EngineConfig()
    d.dir = tmp
    for w in d.workouts:                    # a file is named after its activity, not its list position
        w.entry.file = f"fake/{w.entry.start:%Y%m%d%H%M}.fit"
    return d


@pytest.fixture
def ds(tmp_path):
    return _ds(_runs(4 * 365, skip=(30,)), tmp=tmp_path)


def chart(*exprs, kind="athlete"):
    return {"kind": kind, "id": "c", "series": [{"expression": x} for x in exprs]}


def scope(ch, d, days=90, end=E):
    return CS.scope_of(ch, d, end - days + 1, end)


# ---------------------------------------------------------------------------
# scope: which days a chart reads
# ---------------------------------------------------------------------------

def test_a_chart_ending_today_has_today_in_its_scope(ds):
    s = CS.scope_of(chart("tss"), ds, T - 89, T)
    assert (s.lo, s.hi, s.today) == (T - 89, T, True)


def test_a_per_activity_chart_reads_its_range_only(ds):
    s = scope(chart("tss", "sum(movingduration, startofweek(date))", "if(sport=\"run\", distance)"), ds)
    assert (s.lo, s.hi, s.today) == (E - 89, E, False) and not s.full


def test_pmc_charts_warm_up_six_time_constants(ds):
    s = scope(chart("ctl", "atl", "if(tsb / shift(ctl, 1) < -0.3, tsb)"), ds)
    assert s.lo == E - 89 - 6 * 42 - 1 and s.hi == E and not s.today
    assert scope(chart("tl(movingduration, 28)*7"), ds).lo == E - 89 - 6 * 28
    assert scope(chart("tl(tss, ctlconstant) - shift(tl(tss, ctlconstant), 7)"), ds).lo == E - 89 - 7 - 6 * 42


def test_tis_load_adds_the_tis_fit_window_to_the_warm_up(ds):
    # tisaerobic: each activity's TIS reads the 90 days before it (BUILTIN_EXPRS @lookback:=90)
    assert scope(chart("tisaerobic"), ds).lo == E - 89 - 89
    assert scope(chart("tl((tisaerobic), ctlconstant)"), ds).lo == E - 89 - 6 * 42 - 89


def test_reading_today_is_seen(ds):
    assert scope(chart("if(date >= today - 7, tss)"), ds).today
    assert scope(chart("goalclimbperkm"), ds).today                  # the season plan's target as of today
    s = scope(chart("@win := 7, @historic := athleterange(min({begindate,today-90}), "
                    "min({enddate,today-@win}), meanmax(runpower))"), ds, days=30)
    assert s.today and s.lo == min(E - 29, T - 90)
    assert scope(chart("ftp(meanmax(runpower), 42)"), ds).lo == E - 89 - 41


def test_drift_avg_and_estimated_settings_reach_back(ds):
    assert scope(chart('drift_avg("pace")'), ds).lo == E - 89 - CS.DRIFT_AVG_DAYS
    assert scope(chart('drift("pace", "all")'), ds).weather
    assert scope(chart("60/(runtpace*1.08)"), ds).lo == E - 89 - CS.SETTING_EST_DAYS


def test_what_cannot_be_bounded_is_the_whole_history(ds):
    assert scope(chart("shift(tss, @k)"), ds).full                       # an unknown shift
    assert scope(chart("athleterange(sqrt(today), today, tss)"), ds).full
    for kind in ("zones", "targets", "z5gate", "periodzones", "climbvam", "polecompare", "review", "activity"):
        s = scope(chart(kind=kind), ds)
        assert s.full and s.today, kind


def test_a_workout_chart_reads_its_own_day(ds):
    w = ds.workouts[-10]                       # an activity of the last weeks
    s = CS.scope_of({"kind": "map"}, ds, T - 364, T, workout=w)
    assert (s.lo, s.hi, s.today) == (int(w.day), int(w.day), False)
    s = CS.scope_of(chart("ctl", kind="workout"), ds, T - 364, T, workout=w)
    assert s.lo == T - 364 - 6 * 42 and not s.full
    # review #2: without begin / end the endpoint scopes a workout chart on the activity's own day
    d = int(w.day)
    s = CS.scope_of(chart("ctl", kind="workout"), ds, d, d, workout=w)
    assert (s.lo, s.hi, s.today) == (d - 6 * 42, d, False)
    s = CS.scope_of(chart("avg(heartrate)", kind="workout"), ds, d, d, workout=w)
    assert (s.lo, s.hi) == (d, d)
    assert not CS.reads_range(chart("avg(power)", kind="workout"))
    assert CS.reads_range(chart("athleterange(begindate, enddate, tss)", kind="workout"))
    assert CS.reads_range({"kind": "workout", "variants": [{"series": [{"expression": "enddate"}]}]})


def test_a_var_carries_its_warm_up_where_it_is_used(ds):
    # review #5: `@c := tl(...)` then shift(@c, 7) reads 7 more days of warm-up
    s = scope(chart("@c := tl(tss, ctlconstant), shift(@c, 7)"), ds)
    assert s.lo == E - 89 - 7 - 6 * 42


def test_lookup_of_a_setting_stamps_the_whole_setting_history(ds):
    assert scope(chart("lookup(runthr, date)"), ds).history
    assert not scope(chart("tss"), ds).history


def test_every_evaluator_function_is_classified():
    """A new fn_* must be listed as reading its range only or be handled by the scope analysis
    (else the analysis treats it as unbounded)."""
    fns = {k[3:] for k in vars(Evaluator) if k.startswith("fn_")}
    assert fns == set(CS.RANGE_LOCAL_FNS) | set(CS.HANDLED_FNS), fns ^ (set(CS.RANGE_LOCAL_FNS) | set(CS.HANDLED_FNS))
    assert not set(CS.RANGE_LOCAL_FNS) & set(CS.HANDLED_FNS)


def test_an_unclassified_function_is_the_whole_history(ds):
    assert scope(chart("nosuchfn(tss)"), ds).full


def test_my_training_charts_are_bounded():
    """Every expression chart of 我的訓練 / 周期化訓練 gets a window, not the whole history."""
    from backend.engine.wko5expr.customviews import load_custom_views
    d = _ds(_runs(30))
    views = load_custom_views()
    n = 0
    for name in ("我的訓練", "周期化訓練"):
        for dash in views[name]["dashboards"]:
            for c in dash["charts"]:
                if c.get("kind") != "athlete":
                    continue
                n += 1
                s = scope(c, d)
                assert not s.full and s.lo >= E - 89 - 6 * 42 - 1 - 90, (c.get("id"), s)
    assert n > 20


# ---------------------------------------------------------------------------
# fingerprint: only the activities in scope (+ today when read)
# ---------------------------------------------------------------------------

PLAIN, PMC = chart("tss"), chart("ctl", "atl")


def fp(ch, d, days=90, end=T):
    return CS.fingerprint(d, scope(ch, d, days, end))


def test_adding_an_activity_invalidates_only_charts_whose_range_has_its_day(tmp_path):
    old = _ds(_runs(4 * 365, skip=(30,)), tmp=tmp_path)
    new = _ds(_runs(4 * 365), tmp=tmp_path)                              # a sync brought T − 30
    assert fp(PLAIN, old) != fp(PLAIN, new)                               # 90 days to today
    assert fp(PMC, old) != fp(PMC, new)
    assert fp(PLAIN, old, 90, T - 100) == fp(PLAIN, new, 90, T - 100)     # an older range
    assert fp(PLAIN, old, 20, T - 31) == fp(PLAIN, new, 20, T - 31)


def test_deleting_a_three_year_old_activity_keeps_the_90_and_365_day_charts(tmp_path):
    full = _ds(_runs(4 * 365), tmp=tmp_path)
    gone = _ds(_runs(4 * 365, skip=(3 * 365 + 10,)), tmp=tmp_path)
    for ch in (PLAIN, PMC, chart("tl((tisaerobic), ctlconstant)")):
        for days in (90, 365):
            assert fp(ch, full, days) == fp(ch, gone, days), (ch, days)
    assert CS.fingerprint(full, CS.Scope.whole()) != CS.fingerprint(gone, CS.Scope.whole())


def test_a_day_change_invalidates_only_charts_that_read_today(tmp_path):
    a = _ds(_runs(400), tmp=tmp_path)
    b = _ds(_runs(400), today=TODAY + dt.timedelta(days=1), tmp=tmp_path)
    assert fp(PLAIN, a, 90, T - 10) == fp(PLAIN, b, 90, T - 10)
    assert fp(PMC, a, 90, T - 10) == fp(PMC, b, 90, T - 10)
    reads_today = chart("if(date >= today - 7, tss)")
    assert fp(reads_today, a, 90, T - 10) != fp(reads_today, b, 90, T - 10)
    # a chart whose range ends today names the day in its range already; its scope says today too
    assert scope(PLAIN, a).today is False and CS.scope_of(PLAIN, a, T - 89, T + 1).today


def test_an_edited_activity_in_range_changes_the_fingerprint(tmp_path):
    a = _ds(_runs(200), tmp=tmp_path)
    b = _ds(_runs(200), tmp=tmp_path)
    b.workouts[-5].metrics["tss"] = 999.0
    assert fp(PLAIN, a) != fp(PLAIN, b)
    assert fp(PLAIN, a, 30, T - 150) == fp(PLAIN, b, 30, T - 150)


def test_full_data_fingerprint_still_follows_every_activity_and_today(tmp_path):
    from backend.engine.wko5expr.render_cache import data_fingerprint
    a = _ds(_runs(50), tmp=tmp_path)
    assert data_fingerprint(a) != data_fingerprint(_ds(_runs(50, skip=(49,)), tmp=tmp_path))
    assert data_fingerprint(a) != data_fingerprint(_ds(_runs(50), today=TODAY + dt.timedelta(days=1), tmp=tmp_path))


# ---------------------------------------------------------------------------
# code signature: only the code each chart type runs
# ---------------------------------------------------------------------------

def _fresh_code_memo():
    CH._MEMO.clear()
    CS._CODE.clear()


def test_code_signature_follows_the_functions_a_chart_calls(ds, monkeypatch):
    _fresh_code_memo()
    tss, drift, zones = chart("tss"), chart('drift("pace", "all")'), {"kind": "zones", "system": "x"}
    a = {k: CS.code_signature(c, ds) for k, c in (("tss", tss), ("drift", drift), ("zones", zones))}
    assert len(set(a.values())) == 3 and CS.code_signature(tss, ds) == a["tss"]

    def fn_drift(self, n, ctx):                       # a deploy that changes drift() only
        return 0.0
    monkeypatch.setattr(Evaluator, "fn_drift", fn_drift)
    _fresh_code_memo()
    assert CS.code_signature(drift, ds) != a["drift"]
    assert CS.code_signature(tss, ds) == a["tss"]
    assert CS.code_signature(zones, ds) == a["zones"]


def test_code_signature_follows_a_module_a_chart_reaches(ds, monkeypatch):
    from backend.engine import workout_review as WR
    _fresh_code_memo()
    tss, drift = chart("tss"), chart('drift("pace", "all")')
    a, b = CS.code_signature(tss, ds), CS.code_signature(drift, ds)

    def measure(ds, w):
        return None
    monkeypatch.setattr(WR, "measure", measure)        # workout_review: only drift() reaches it
    _fresh_code_memo()
    assert CS.code_signature(tss, ds) == a and CS.code_signature(drift, ds) != b


def test_code_signature_keeps_the_manual_version(ds, monkeypatch):
    from backend.engine.wko5expr import render_cache as RC
    _fresh_code_memo()
    a = CS.code_signature(PLAIN, ds)
    monkeypatch.setattr(RC, "CACHE_VERSION", RC.CACHE_VERSION + 1)
    _fresh_code_memo()
    assert CS.code_signature(PLAIN, ds) != a


# ---------------------------------------------------------------------------
# review fixes: calibrations, marks, setting history, dynamic code, deploy safety
# ---------------------------------------------------------------------------

def test_a_calibration_change_invalidates_a_past_range_drift_chart(tmp_path, monkeypatch):
    from backend.engine import calibrate as CAL
    d = _ds(_runs(200), tmp=tmp_path)
    drift = chart('drift("pace", "all")')
    a = fp(drift, d, 60, T - 100)
    names = list(CAL._registry())
    name = next((n for n in names if n.startswith("drift")), names[0])
    monkeypatch.setattr(CAL, "stored_entry",
                        lambda n, user_id=1: {"value": 123.0, "source": "user"} if n == name else None)
    assert fp(drift, d, 60, T - 100) != a


def test_the_review_card_follows_the_users_activity_marks(tmp_path, monkeypatch):
    from backend.engine import activity_tags as AT
    d = _ds(_runs(50), tmp=tmp_path)
    w = d.workouts[-3]
    review = {"kind": "review", "section": "summary"}
    a = CS.fingerprint(d, CS.scope_of(review, d, T - 364, T, workout=w))
    p = fp(PLAIN, d)
    monkeypatch.setattr(AT, "load", lambda *a, **k: [{"start_local": "2026-09-27T07:00", "tag": "x"}])
    assert CS.fingerprint(d, CS.scope_of(review, d, T - 364, T, workout=w)) != a
    assert fp(PLAIN, d) == p                                 # an expression chart has no marks part


def test_the_setting_history_is_in_the_key_of_a_lookup_chart(tmp_path):
    d = _ds(_runs(50), tmp=tmp_path)
    look = chart("lookup(runthr, date - 400)")
    a, p = fp(look, d), fp(PLAIN, d)
    d.athlete.settings["runthr"] = [(dt.date(2020, 1, 1), 170.0)]       # a dated setting years ago
    d.memo.clear()
    assert fp(look, d) != a and fp(PLAIN, d) == p


def test_fit_settings_from_wko5_are_in_every_key(tmp_path):
    d = _ds(_runs(50), tmp=tmp_path)
    d.settings_from = "wko5"
    a = fp(PLAIN, d)
    d.athlete.settings["runftp"] = [(dt.date(2026, 1, 1), 300.0)]
    d.memo.clear()
    assert fp(PLAIN, d) != a


def test_code_signature_covers_the_pd_calls_render_makes_itself(ds, monkeypatch):
    # render.mftp_plateau / pd_notice evaluate "ftp(...)" / "pdcurve(meanmax(runpower))" strings
    _fresh_code_memo()
    pd, tss = chart("meanmax(runpower)"), chart("tss")
    a, b = CS.code_signature(pd, ds), CS.code_signature(tss, ds)

    def fn_ftp(self, n, ctx):
        return 1.0
    monkeypatch.setattr(Evaluator, "fn_ftp", fn_ftp)
    _fresh_code_memo()
    assert CS.code_signature(pd, ds) != a and CS.code_signature(tss, ds) == b


def test_code_signature_follows_channel_expressions(ds, monkeypatch):
    _fresh_code_memo()
    ec, tss = chart("max(ecpower)"), chart("tss")
    a, b = CS.code_signature(ec, ds), CS.code_signature(tss, ds)

    def fn_ewma(self, n, ctx):
        return 0.0
    monkeypatch.setattr(Evaluator, "fn_ewma", fn_ewma)          # ecpower = an ewma(power, 25) expression
    _fresh_code_memo()
    assert CS.code_signature(ec, ds) != a and CS.code_signature(tss, ds) == b


def test_code_signature_sees_a_period_chart_without_a_period_key(ds, monkeypatch):
    from backend.engine.wko5expr import periods as PD
    _fresh_code_memo()
    weekly, tss = chart("sum(tss, startofweek(date))"), chart("tss")
    assert PD.chart_period(weekly) == "week" and "period" not in weekly
    a, b = CS.code_signature(weekly, ds), CS.code_signature(tss, ds)

    def bucket_start(b, period):
        return b
    monkeypatch.setattr(PD, "bucket_start", bucket_start)
    _fresh_code_memo()
    assert CS.code_signature(weekly, ds) != a and CS.code_signature(tss, ds) == b


def test_code_changed_on_disk_after_start_never_shares_a_key(ds, monkeypatch):
    """A `git pull` without a restart: the running code is the old one, the files the new one —
    such a process gets keys of its own (a per-process nonce), never the new code's."""
    _fresh_code_memo()
    a = CS.code_signature(PLAIN, ds)
    monkeypatch.setattr(CS, "_PROCESS_START", 0.0)             # every module file is newer than the start
    CS._DISK.clear()
    _fresh_code_memo()
    b = CS.code_signature(PLAIN, ds)
    assert b != a
    monkeypatch.setattr(CS, "_NONCE", "another process")
    _fresh_code_memo()
    assert CS.code_signature(PLAIN, ds) != b
    CS._DISK.clear()
    _fresh_code_memo()
