"""SP-965 (owner 2026-10-28, Q1 (b) / Q2 (a): 「CP 有效的定義要全 app 統一」): the testing card (status.i_testing)
and the week plan's CP test suggestion read the same 「CP 有效」 as the power state — power_state.cp_check over
power_state.state(): a CP test or a race in 42 days, or the Stryd PD fit anchored by recent all-out efforts
(SP-963). Kept valid by the efforts, no CP test is suggested and the card is not red; CP_REMIND_DAYS (7) before
they lapse the card and the week plan remind 「再跑一次強度課或做 CP 測試」. No ceiling on how long the efforts
may stand in for a formal test. A CP valid by a CP test keeps its cadence (test_cp_protocols.test_timing_rules).
Synthetic data only."""
from __future__ import annotations

import datetime as dt

import pytest

from backend.engine import power_state as PST
from backend.engine import status as ST
from backend.engine.planning import Plan, Threshold
from backend.tests.test_cp_efforts_sp963 import CP, TODAY, _ago, _DS, _state, easy, long, short


def _check(ds, **kw):
    return PST.cp_check(_state(ds, **kw), TODAY, kw.get("last_cp_test"))


# ---- 1. power_state.cp_check ------------------------------------------------------------------------

def test_kept_valid_by_the_efforts_no_cp_test_is_due():
    ds = _DS([easy(_ago(30)), long(_ago(20)), short(_ago(10)), easy(_ago(2))])
    cv = _check(ds)
    assert cv["valid"] and cv["basis"] == "efforts" and cv["by"] == "efforts"
    assert cv["date"] == _ago(20).isoformat() and cv["age"] == 20          # the older of the two efforts
    assert cv["left"] == 22 and cv["until"] == (_ago(20) + dt.timedelta(days=42)).isoformat()
    assert not cv["remind"] and not cv["due"]


@pytest.mark.parametrize("long_ago, left, remind", [(34, 8, False), (35, 7, True), (36, 6, True), (42, 0, True)])
def test_a_week_before_the_efforts_lapse_it_reminds(long_ago, left, remind):
    ds = _DS([long(_ago(long_ago)), short(_ago(10))])
    cv = _check(ds)
    assert cv["valid"] and cv["by"] == "efforts" and cv["left"] == left
    assert cv["remind"] is remind and cv["due"] is remind
    assert PST.CP_REMIND_DAYS == 7


def test_lapsed_efforts_are_due_like_no_cp():
    cv = _check(_DS([long(_ago(43)), short(_ago(10))]))
    assert not cv["valid"] and not cv["remind"] and cv["due"] and cv["date"] is None


def test_no_ceiling_on_how_long_the_efforts_stand_in_for_a_test():
    # Q2 (a): a formal CP test 200 days ago and the efforts keep the CP valid — no test is due
    cv = _check(_DS([long(_ago(20)), short(_ago(10))]), last_cp_test=_ago(200).isoformat())
    assert cv["valid"] and cv["by"] == "efforts" and not cv["due"]


def test_a_cp_test_or_a_race_keeps_its_cadence():
    ds = _DS([easy(_ago(3))])
    t41 = _check(ds, last_cp_test=_ago(41).isoformat())
    assert t41["valid"] and t41["by"] == "test" and t41["age"] == 41 and not t41["remind"] and not t41["due"]
    t43 = _check(ds, last_cp_test=_ago(43).isoformat())
    assert not t43["valid"] and t43["by"] == "test" and t43["age"] == 43 and t43["due"]
    race = _check(ds, last_race=_ago(20).isoformat())
    assert race["valid"] and race["by"] == "race" and race["age"] == 20 and not race["due"]


def test_without_a_meter_nothing_is_due():
    ds = _DS([easy(_ago(3))], plan=Plan(profile={"power_source": "none"}))
    assert not _check(ds)["due"]


# ---- 2. the testing card (status.i_testing) ---------------------------------------------------------

@pytest.fixture
def card(monkeypatch):
    from backend.engine import reentry as RE
    from backend.engine import threshold_confidence as TC
    from backend.engine import workout_review as WR
    from backend.engine import zone_events as ZE
    monkeypatch.setattr(WR, "latest_cp_test", lambda ds, today: None)
    monkeypatch.setattr(ZE, "suggestions", lambda *a, **k: {"suggestions": [], "events": [], "checks": {}})
    monkeypatch.setattr(TC, "check", lambda *a, **k: None)
    monkeypatch.setattr(RE, "find", lambda *a, **k: None)

    def make(runs, thresholds=()):
        ds = _DS(runs)
        ds.plan.thresholds = [Threshold(_ago(30).isoformat(), lthr=165.0, aethr=150.0), *thresholds]
        st = ST.Status.__new__(ST.Status)
        st.ds, st.plan, st.today = ds, ds.plan, TODAY
        st.goals, st.indicators, st.cp_protocol = {"days_to_next_a": None}, [], "quick"
        return st, st.i_testing()
    return make


def test_the_card_counts_the_efforts_like_the_power_state(card):
    st, ind = card([easy(_ago(30)), long(_ago(20)), short(_ago(10)), easy(_ago(2))])
    ps = st.power_state()
    assert ps["state"] == PST.POWER and ps["basis"] == "efforts"
    # not 「CP 沒測過」 / 「要測」: the card says until when and by which runs
    assert ind.level == ST.GOOD and ind.text == "OK" and not ind.action
    first = ind.why.split("；")[0]
    assert first.startswith("CP 有效到 11/18（10/7 跑出 ") and "沒測過" not in ind.why
    assert ind.extra["cp_due"] is False


def test_the_card_reminds_a_week_before_the_efforts_lapse(card):
    st, ind = card([long(_ago(36)), short(_ago(10))])
    assert st.power_state()["state"] == PST.POWER                    # still ①
    assert ind.level == ST.WATCH and ind.text == "快到期" and ind.extra["cp_due"] is True
    assert ind.why.split("；")[0].startswith("CP 再 6 天到期（")
    assert ind.verdict == "CP 快到期，再跑一次強度課或做 CP 測試"
    assert ind.action.startswith("再跑一次強度課（3–5 分鐘和 12–30 分鐘各一段全力）就能延長，或排一次 CP 測試（")


def test_the_card_without_any_cp_is_unchanged(card):
    st, ind = card([easy(_ago(20)), easy(_ago(10))])
    assert st.power_state()["state"] == PST.STALE
    assert ind.level == ST.BAD and "CP 沒測過" in ind.why and ind.extra["cp_due"] is True


def test_the_card_with_a_fresh_cp_test_is_unchanged(card):
    _, ind = card([easy(_ago(3))], [Threshold(_ago(13).isoformat(), cp=CP)])
    assert ind.level == ST.GOOD and "CP 13 天前" in ind.why and ind.extra["cp_due"] is False


# ---- 3. the week plan's CP test suggestion -----------------------------------------------------------

def week(monkeypatch, long_day: str):
    """The week plan of test_aet_weekday's athlete with no CP test and the PD fit kept valid by a 20-min
    effort on `long_day` and a 5-min one on 9/25 (power_state.efforts_on stubbed: that Dataset has no
    Stryd PD model). Returns (week plan, status)."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_aet_weekday import TODAY as T2, _build_week_ds
    ds, plan = _build_week_ds()
    plan.thresholds = [t for t in plan.thresholds if t.cp is None]      # no CP test row: the CP is the fit
    plan.profile = {"power_source": "stryd"}
    ds.settings_from, ds.setting_label = None, (lambda key: "設定")     # zones.threshold_info: the fit's CP
    eff = {"anchored": True, "short": {"date": "2026-09-25", "seconds": 300, "watts": 290, "pct": 0.98},
           "long": {"date": long_day, "seconds": 1200, "watts": 255, "pct": 0.97},
           "until": (dt.date.fromisoformat(long_day) + dt.timedelta(days=42)).isoformat(),
           "cp": 250.0, "fit_date": "2026-09-01"}
    monkeypatch.setattr(PST, "efforts_on", lambda ds, d: eff)
    st = Status(ds, plan, T2, prefs=PP.Prefs()).compute()
    return O.week_plan(ds, st, T2, prefs=PP.Prefs()), st


def test_week_plan_kept_valid_by_the_efforts_suggests_no_cp_test(monkeypatch):
    wp, st = week(monkeypatch, "2026-09-20")                            # valid to 11/1, today 9/30
    ps = wp["thresholds"]["power_state"]
    assert ps["state"] == PST.POWER and ps["basis"] == "efforts" and wp["thresholds"]["cp"] == 250.0
    assert "cp" not in {t["kind"] for t in wp["test_suggestions"]}
    ti = next(i for i in st.indicators if i.id == "testing")
    assert ti.extra["cp_due"] is False and "CP 沒測過" not in ti.why and "要測" != ti.text


def test_week_plan_a_week_before_the_efforts_lapse_suggests_the_cp_test(monkeypatch):
    wp, _ = week(monkeypatch, "2026-08-22")                             # valid to 10/3: 3 days left
    assert wp["thresholds"]["power_state"]["state"] == PST.POWER
    (cp,) = [t for t in wp["test_suggestions"] if t["kind"] == "cp"]
    assert cp["reason"].startswith("CP 快到期，再跑一次強度課或做 CP 測試：")
    assert "CP 250 W 有效到 10/3（8/22 跑出 20 分鐘 255 W，9/25 跑出 5 分鐘 290 W）" in cp["reason"]
