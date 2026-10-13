"""
SP-471: when the easy runs' own power at the easy-run HR cap is far above the easy power band
(SP-449's note), suggest the test that conflict points at — with 排入, on 總覽 (a block of its own)
and on 課表:

* the HR side → the AeT test (an LTHR test too when the LTHR is the doubt): the cap within
  EP_CAP_LTHR_GAP_BPM of LTHR, or the LTHR a low-confidence automatic estimate;
* the power side → the CP test: the CP's own date older than EP_CP_STALE_DAYS, or no CP test / race in
  EP_ALL_OUT_DAYS; neither side → the CP test; both → both, each with its reason in numbers;
* not ≤ 10 days before an A race, in a 恢復週 / 停訓後恢復期 / 恢復期 etc., in a new runner's first weeks;
  not when a test of that kind is already in the plan (or was done in EP_RETEST_DAYS).

Synthetic data only — never the user's data, the real app DB or ~/.wko5coach.
"""
import datetime as dt
import re
import types
from pathlib import Path

import pytest

from backend.engine import base_check as BC
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import suggestions as SG

TODAY = dt.date(2026, 10, 13)
STATIC = Path(__file__).resolve().parents[1] / "static"


def _note(power=169.0, cap=154.0, cp=191.0):
    return BC.easy_power_note({"power": power, "n": 6}, cp, cap)


# ---------------------------------------------------------------------------
# which test (base_check.easy_power_tests)
# ---------------------------------------------------------------------------

def test_the_owners_case_cap_at_lthr_and_a_low_confidence_lthr_asks_for_the_aet_test():
    # 正式站: CP 191 W, the easy cap 154 (COROS 儲備心率 Z2), LTHR 155 auto-estimated (low), a CP test 9/30
    ep = BC.easy_power_tests(_note(), TODAY, lthr=155.0, lthr_measured=False, lthr_conf="low",
                             cp_date="2026-10-12", last_cp_test="2026-09-30")
    assert [t["kind"] for t in ep["tests"]] == ["aet"]
    why = ep["tests"][0]["why"]
    assert "心率上限 154 跟 LTHR 155 幾乎一樣（差 1 bpm，不到 5）" in why
    assert "LTHR 155 是自動估算、可信度低" in why and "AeT 測試（或 30 分鐘 LTHR 測試）" in why
    assert ep["tests"][0]["lthr_doubt"] is True
    # the summary says the numbers: 169 W at 154 bpm = 88 % of CP 191 W, the band's top 153 W (80 % CP)
    assert ep["summary"].startswith("輕鬆跑上限 154 bpm 時約 169 W ＝ 88% CP（CP 191 W）")
    assert "153 W（80% CP）" in ep["summary"]


def test_a_believable_lthr_far_above_the_cap_and_no_all_out_effort_asks_for_the_cp_test():
    ep = BC.easy_power_tests(_note(), TODAY, lthr=170.0, lthr_measured=True, lthr_conf="high",
                             cp_date="2026-10-12", last_cp_test="2026-07-01")
    assert [t["kind"] for t in ep["tests"]] == ["cp"]
    assert "最近 42 天沒有 CP 測試或比賽" in ep["tests"][0]["why"] and "建議做 CP 測試" in ep["tests"][0]["why"]


def test_an_old_cp_asks_for_the_cp_test_with_its_age():
    ep = BC.easy_power_tests(_note(), TODAY, lthr=170.0, lthr_measured=True, cp_date="2026-08-01",
                             last_race="2026-10-01")
    (t,) = ep["tests"]
    assert t["kind"] == "cp" and "CP 是 73 天前的（超過 42 天）" in t["why"]
    assert "沒有 CP 測試或比賽" not in t["why"]                      # the race 12 days ago was all-out


def test_both_sides_list_both_tests_each_with_its_reason():
    ep = BC.easy_power_tests(_note(), TODAY, lthr=156.0, lthr_measured=True, cp_date=None, last_cp_test=None)
    assert [t["kind"] for t in ep["tests"]] == ["aet", "cp"]
    aet, cp = ep["tests"]
    assert "差 2 bpm" in aet["why"] and "LTHR 測試" not in aet["why"] and not aet["lthr_doubt"]
    assert "沒有 CP 測試或比賽" in cp["why"]


def test_a_cap_above_lthr_says_so():
    (t,) = BC.easy_power_tests(_note(cap=158.0, power=175.0), TODAY, lthr=155.0, lthr_measured=True,
                               last_cp_test="2026-10-01")["tests"]
    assert t["kind"] == "aet" and "心率上限 158 不低於 LTHR 155" in t["why"]


def test_the_max_hr_prior_counts_as_a_doubtful_lthr():
    (t,) = BC.easy_power_tests(_note(cap=140.0), TODAY, lthr=165.0, lthr_prior=True,
                               last_cp_test="2026-10-01")["tests"]
    assert t["kind"] == "aet" and t["lthr_doubt"] and "LTHR 165 是自動估算" in t["why"]


def test_neither_side_falls_back_to_the_cp_test_unless_one_was_just_done():
    ep = BC.easy_power_tests(_note(cap=140.0), TODAY, lthr=170.0, lthr_measured=True,
                             cp_date="2026-10-12", last_cp_test="2026-09-01")       # 42 days: still recent enough
    (t,) = ep["tests"]
    assert t["kind"] == "cp" and "心率上限離 LTHR 170 還有 30 bpm、LTHR 可信：比較像是 CP 估低了" in t["why"]
    # a CP test 13 days ago: its result first, nothing to suggest
    assert BC.easy_power_tests(_note(cap=140.0), TODAY, lthr=170.0, lthr_measured=True,
                               last_cp_test="2026-09-30") is None


def test_a_test_of_that_kind_done_recently_is_not_asked_again():
    ep = BC.easy_power_tests(_note(), TODAY, lthr=155.0, lthr_conf="low", last_cp_test="2026-09-30",
                             last_aet_test="2026-10-01")
    assert ep is None                                                 # the AeT test 12 days ago: its result first
    ep = BC.easy_power_tests(_note(), TODAY, lthr=155.0, lthr_conf="low", last_cp_test="2026-09-30",
                             last_aet_test=(TODAY - dt.timedelta(days=BC.EP_RETEST_DAYS)).isoformat())
    assert [t["kind"] for t in ep["tests"]] == ["aet"]


def test_the_rules_are_named_constants(monkeypatch):
    kw = dict(lthr=158.0, lthr_measured=True, last_cp_test="2026-09-10")        # 33 days ago
    assert [t["kind"] for t in BC.easy_power_tests(_note(), TODAY, **kw)["tests"]] == ["aet"]   # 4 bpm < 5
    monkeypatch.setattr(BC, "EP_CAP_LTHR_GAP_BPM", 3.0)
    (t,) = BC.easy_power_tests(_note(), TODAY, **kw)["tests"]        # 4 bpm is no longer "the same":
    assert t["kind"] == "cp" and "比較像是 CP 估低了" in t["why"]       # the CP side by default
    monkeypatch.setattr(BC, "EP_ALL_OUT_DAYS", 7)
    (t,) = BC.easy_power_tests(_note(), TODAY, **kw)["tests"]
    assert t["kind"] == "cp" and "最近 7 天沒有 CP 測試或比賽" in t["why"]


def test_nothing_without_the_note():
    assert BC.easy_power_tests(None, TODAY, lthr=155.0) is None
    assert BC.easy_power_tests(_note(power=150.0), TODAY, lthr=155.0) is None       # no conflict, no note
    assert BC.easy_power_tests({"src": "hr_zones", "text": "x"}, TODAY, lthr=155.0) is None


@pytest.mark.parametrize("days_to, phase, mode, cold, why", [
    (10, "base", "base", False, "race"), (3, "specific", "specific", False, "race"),
    (None, "base", "recovery_week", False, "mode"), (None, "base", "reentry", False, "mode"),
    (None, "recovery", "recovery", False, "phase"), (None, "transition", "transition", False, "phase"),
    (None, "base", "rebuild", False, "phase"), (None, "taper", "taper", False, "phase"),
    (None, "base", "base", True, "cold_start"),
    (11, "base", "base", False, None), (None, "specific", "specific", False, None), (40, "base", "base", False, None)])
def test_not_suggested_where_the_tests_never_are(days_to, phase, mode, cold, why):
    assert BC.easy_power_blocked(days_to, phase, mode, cold) == why


# ---------------------------------------------------------------------------
# into the week's test suggestions (overview.easy_power_suggestions / week_plan)
# ---------------------------------------------------------------------------

AET_S = {"kind": "test", "title": "AeT 測試 40 分", "minutes": 50, "target": "", "detail": "d", "source": "UA",
         "tss": 30.0, "protocol": "aet"}
CP_S = {"kind": "test", "title": "CP 測試 20 分全力", "minutes": 37, "protocol": "quick", "tss": 45.0}


def test_new_rows_are_built_like_the_due_tests_and_an_existing_one_gets_the_reason():
    ep = {"summary": "S", "tests": [{"kind": "aet", "why": "W1", "lthr_doubt": True},
                                    {"kind": "cp", "why": "W2", "lthr_doubt": False}]}
    tests = [{"kind": "cp", "title": "CP 測試", "minutes": 37, "reason": "門檻過期", "session": dict(CP_S)}]
    O.easy_power_suggestions(tests, ep, lambda: CP_S, lambda: AET_S, "ua40")
    cp, aet = tests
    assert cp["reason"] == "門檻過期 · W2" and cp["easy_power"] == {"summary": "S", "why": "W2", "lthr_doubt": False}
    assert aet["kind"] == "aet" and aet["src"] == "easy_power" and aet["reason"] == "W1"
    assert aet["session"]["title"] == "AeT 測試 40 分" and aet["replaces_long"] is False and aet["protocol"] == "ua40"
    # the doubtful LTHR: the 30-min LTHR test's 安排課表 link rides along
    assert aet["links"] and aet["links"][0]["test"] == "tt30" and aet["links"][0]["key"] == "lib:friel_lthr30"
    # a CP test with 課表偏好 「用比賽」 has no session: not offered
    tests = []
    O.easy_power_suggestions(tests, {"summary": "S", "tests": [{"kind": "cp", "why": "W", "lthr_doubt": False}]},
                             lambda: None, lambda: AET_S)
    assert tests == []


def _week_ds():
    from backend.tests.test_aet_weekday import _build_week_ds
    from backend.tests.test_quality_gate import TODAY as QT
    ds, plan = _build_week_ds()
    for t in plan.thresholds:                                         # the CP test 9/20 → 7/1: 91 days old
        if t.cp is not None:
            t.date = "2026-07-01"
    return ds, plan, QT


def _week_plan(monkeypatch, power, days_to=None):
    from backend.engine.status import Status
    ds, plan, qt = _week_ds()
    st = Status(ds, plan, qt, prefs=PP.Prefs()).compute()
    if days_to is not None:
        st.goals = {**st.goals, "days_to_next_a": days_to}
    ds.config = types.SimpleNamespace(parity=False)                  # easy_targets runs outside the parity mode
    monkeypatch.setattr(BC, "easy_targets", lambda ds, today, aet: {"power": power, "n": 6, "text": "", "pace_s_km": None})
    return O.week_plan(ds, st, qt, prefs=PP.Prefs())


def test_week_plan_suggests_the_test_with_the_numbers(monkeypatch):
    # the synthetic plan: CP 250 W (a test 7/1), LTHR 160, easy cap 144 — 235 W at the cap = 94 % CP
    wp = _week_plan(monkeypatch, 235.0)
    assert any(n.get("src") == "easy_power" for n in wp["notes"])
    ep = [t for t in wp["test_suggestions"] if t.get("easy_power")]
    assert ep, wp["test_suggestions"]
    s = ep[0]["easy_power"]["summary"]
    cap, cp = wp["thresholds"]["aet"], wp["thresholds"]["cp"]
    assert f"輕鬆跑上限 {cap:.0f} bpm 時約 235 W ＝ {235 / cp * 100:.0f}% CP（CP {cp:.0f} W）" in s
    for t in ep:
        assert t["session"]["kind"] == "test" and t["minutes"] and t["reason"]
    # no conflict → no easy-power test
    assert not [t for t in _week_plan(monkeypatch, 190.0)["test_suggestions"] if t.get("easy_power")]


def test_week_plan_quiet_ten_days_before_an_a_race(monkeypatch):
    wp = _week_plan(monkeypatch, 235.0, days_to=9)
    assert any(n.get("src") == "easy_power" for n in wp["notes"])     # the note stays
    assert not [t for t in wp["test_suggestions"] if t.get("easy_power")]


def test_the_plan_s_cp_row_and_the_review_s_test_both_count_as_cp_tests():
    from backend.engine.planning import Plan, Threshold
    p = Plan()
    p.thresholds += [Threshold("2026-09-20", cp=250.0), Threshold("2026-10-01", cp=255.0)]
    p.thresholds[-1].cp_manual = True                                 # typed by hand: not a test
    assert O.last_cp_test_day(p, TODAY, None) == "2026-09-20"
    assert O.last_cp_test_day(p, TODAY, "2026-09-30") == "2026-09-30"
    assert O.last_cp_test_day(Plan(), TODAY, None) is None


def test_the_last_race_is_a_race_not_a_hike():
    ev = lambda kind, date, days=1: types.SimpleNamespace(kind=kind, start=dt.date.fromisoformat(date),  # noqa: E731
                                                           end=dt.date.fromisoformat(date) + dt.timedelta(days=days - 1))
    plan = types.SimpleNamespace(events=[ev("race", "2026-09-06"), ev("baiyue", "2026-10-03", 2),
                                         ev("race", "2026-11-01")])
    assert O.last_race_day(plan, TODAY) == "2026-09-06"


# ---------------------------------------------------------------------------
# the box rows, 排入, not twice (api/plan_sessions.py)
# ---------------------------------------------------------------------------

EP = {"summary": "輕鬆跑上限 154 bpm 時約 169 W ＝ 88% CP", "why": "心率上限 154 跟 LTHR 155 幾乎一樣", "lthr_doubt": True}


@pytest.fixture
def _pin(monkeypatch):
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: dt.date(2026, 9, 30))
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs(aet_test_protocol="ua40"))


def _aet_row():
    return {"kind": "aet", "protocol": "ua40", "title": "AeT 測試 40 分", "minutes": 50, "reason": EP["why"],
            "src": "easy_power", "easy_power": dict(EP), "replaces_long": False,
            "links": [{"href": "/x?template=lib:friel_lthr30", "label": "安排課表：30 分鐘獨跑測試", "test": "tt30"}],
            "session": {"kind": "test", "title": "AeT 測試 40 分", "minutes": 50, "protocol": "aet"}}


def test_box_and_schedule_rows_carry_the_reason_and_scheduling_it_ends_the_suggestion(monkeypatch, _pin):
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        e.inp["cur"]["test_suggestions"] = [_aet_row()]
        e.c.get(f"{API}/sessions")
        (sg,) = e.c.get(f"{API}/test-suggestions").json()["suggestions"]     # the 課表 page's test_suggestions
        assert sg["easy_power"] == EP and sg["days"]
        box = {r["id"]: r for r in e.c.get(f"{API}/suggestions").json()["suggestions"]}
        r = box["test:aet:2026-09-28"]
        assert r["easy_power"] == EP and r["reason"] == EP["why"] and r["links"][0]["test"] == "tt30"
        day = r["options"][0]["day"]
        out = e.c.post(f"{API}/suggestions/accept", json={"id": r["id"], "day": day}).json()
        assert out["sessions"][0]["kind"] == "test" and out["sessions"][0]["day"] == day
        # the same kind is in the plan now: not suggested again (box and 課表)
        assert "test:aet:2026-09-28" not in {x["id"] for x in e.c.get(f"{API}/suggestions").json()["suggestions"]}
        assert e.c.get(f"{API}/test-suggestions").json()["suggestions"] == []


def test_a_baseline_row_standing_in_keeps_the_reason(monkeypatch, _pin):
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        e.inp["tests"] = {"cp": None, "aet": None}
        e.inp["cur"]["test_suggestions"] = [_aet_row()]
        e.c.get(f"{API}/sessions")
        box = {r["id"]: r for r in e.c.get(f"{API}/suggestions").json()["suggestions"]}
        assert "test:aet:2026-09-28" not in box
        b = box["baseline:aet:2026-09-28"]
        assert b["easy_power"] == EP and EP["why"] in b["reason"] and b["links"][0]["test"] == "tt30"


def test_carry_easy_power_only_onto_the_stand_in_of_the_same_kind():
    rows = [{"type": "test", "kind": "aet", "easy_power": EP, "reason": "a"},
            {"type": "baseline", "kind": "aet", "reason": "b"}, {"type": "baseline", "kind": "cp", "reason": "c"}]
    SG.carry_easy_power(rows)
    assert rows[1]["easy_power"] == EP and rows[1]["reason"] == f"b · {EP['why']}" and "easy_power" not in rows[2]


# ---------------------------------------------------------------------------
# the pages: a block of its own on 總覽 (top, not in the 本週 card), and on 課表
# ---------------------------------------------------------------------------

def test_the_overview_has_its_own_block_at_the_top():
    html = (STATIC / "overview.html").read_text(encoding="utf-8")
    i_blk, i_kpi, i_week = html.index('<div id="eptest"'), html.index('id="s-status"'), html.index('id="s-week"')
    assert i_blk < i_kpi < i_week                                      # above the KPI row, outside 本週
    assert '<script src="/api/v1/static/eptest.js" defer></script>' in html


def test_the_schedule_page_shows_it_and_reloads_after_scheduling():
    html = (STATIC / "schedule.html").read_text(encoding="utf-8")
    assert '<div id="eptest" hidden></div>' in html and "/api/v1/static/eptest.js" in html
    assert html.index('id="pnotes"') < html.index('id="eptest"') < html.index('id="calwrap"')
    assert re.search(r'from === "eptest"\) load\(\)', html)


def test_the_block_script_uses_the_box_rows_and_its_accept():
    js = (STATIC / "eptest.js").read_text(encoding="utf-8")
    assert "suggestions:loaded" in js and "r.easy_power" in js
    assert "${API}/accept" in js and "${API}/dismiss" in js and 'class="ep-row sg-item"' in js
    box = (STATIC / "suggestions.js").read_text(encoding="utf-8")
    assert 'new CustomEvent("suggestions:loaded"' in box
    assert '"/api/v1/static/eptest.js"' in (STATIC / "offline_core.js").read_text(encoding="utf-8")


def test_the_block_texts_are_in_both_languages():
    import json
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "common.json").read_text(encoding="utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "common.json").read_text(encoding="utf-8"))
    js = (STATIC / "eptest.js").read_text(encoding="utf-8")
    keys = {f"eptest.{k}" for k in re.findall(r'T\("([a-z_]+)"', js)}
    assert keys and all(k in zh and k in en and en[k] != zh[k] for k in keys), keys
    from backend.i18n import _, use_locale
    with use_locale("en"):
        ep = BC.easy_power_tests(_note(), TODAY, lthr=155.0, lthr_conf="low", last_cp_test="2026-09-30")
    assert ep["summary"].startswith("At the easy-run cap of 154 bpm you run about 169 W")
    assert "almost LTHR 155" in ep["tests"][0]["why"] and "30-min LTHR test" in ep["tests"][0]["why"]
    assert not re.search(r"[一-鿿]", ep["summary"] + ep["tests"][0]["why"])
