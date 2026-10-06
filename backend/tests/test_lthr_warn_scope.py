"""The LTHR warning's scope and wording (SP-277; lthr-low-confidence-testing.md §1.1, §6.1 第
4–5 點): only on steps whose HR target comes from LTHR, never on a test session, why + how it
goes away, a manual LTHR's 「手動輸入，沒有驗證」. Synthetic data only."""
import asyncio

import pytest

from backend.api import plan_sessions as PSAPI
from backend.engine import aet_test as AT
from backend.engine import plan_prefs as PP
from backend.engine import target_policy as TP
from backend.engine import threshold_confidence as TC
from backend.engine import workout_steps as WS
from backend.i18n import use_locale
from backend.tests.test_threshold_confidence import TODAY, lt, run

RUNS = [run(s120=178.0, s60=180.0), run(s120=176.0, s60=178.0), run(s120=175.0, s60=177.0)]


def _assess(kind, runs=RUNS, **k):
    return TC.assess(lt(kind=kind, cp=250.0), {"value": 182.0, "source_kind": "manual"}, {"value": None},
                     TODAY, runs, {}, 250.0, **k)


# ---- the text: why + how it goes away -------------------------------------------------------

def test_estimate_alone_gets_the_actionable_text():
    w = TC.warn_of(_assess("estimate"))
    assert w["lthr"]["low"]
    assert w["lthr"]["text"] == "LTHR 是估算的；輕鬆跑可以改用講話測試的配速，做一次 30 分鐘測試後這個提醒會消失"
    with use_locale("en"):
        assert TC.warn_of(_assess("estimate"))["lthr"]["text"].startswith("Your LTHR is an estimate;")


def test_watch_alone_names_the_watch():
    t = TC.warn_of(_assess("watch"))["lthr"]["text"]
    assert t.startswith("LTHR 來自手錶帳號，沒有測過；") and t.endswith("做一次 30 分鐘測試後這個提醒會消失")


def test_other_reasons_keep_the_why_and_say_how():
    # a 60-min effort well above LTHR (effort signal) on top of the estimate source
    runs = RUNS + [run(hr60=172.0, dur_s=3700.0)]
    r = TC.assess(lt(160.0, kind="estimate", cp=250.0), {"value": 190.0, "source_kind": "manual"}, {"value": None},
                  TODAY, runs, {}, 250.0)
    reasons = [s for s in r["lthr"]["signals"] if s["level"] != "hint" and s["id"] != "source"]
    if not reasons:
        pytest.skip("the synthetic run didn't raise an effort signal")
    t = r["warn"]["lthr"]["text"]
    assert t.startswith("LTHR 可信度低：心率目標可能不準（") and t.endswith("做一次 30 分鐘 LTHR 測試並套用後，這個提醒會消失")


def test_manual_lthr_stays_medium_with_the_small_note():
    r = _assess("manual")
    assert r["lthr"]["confidence"] == "medium"
    w = TC.warn_of(r)
    assert not w["lthr"]["low"] and w["lthr"]["note"] == "手動輸入，沒有驗證"
    with use_locale("en"):
        assert TC.warn_of(_assess("manual"))["lthr"]["note"] == "Entered by hand, not verified"


def test_a_tested_lthr_has_no_note():
    r = TC.assess(lt(168.0, kind="test", date="2026-09-20"), {"value": 190.0, "source_kind": "test"},
                  {"value": 50.0, "source_kind": "manual"}, TODAY, RUNS, {}, None)
    assert TC.warn_of(r) is None


# ---- the scope (pure) -------------------------------------------------------------------------

WARN = {"lthr": {"low": True, "text": "x", "note": ""}}
EASY = [{"id": "a", "kind": "work", "target": {"type": "auto", "intent": "easy"}}]
BAND = [{"id": "a", "kind": "work", "target": {"type": "auto", "intent": "band", "lo": 0.95, "hi": 1.0}}]
TYPED = [{"id": "a", "kind": "work", "target": {"type": "hr", "mode": "abs", "lo": 150, "hi": 160}}]
HR_RES = {"a": {"type": "hr"}}


def test_easy_with_a_measured_aet_has_no_warning():
    assert TC.session_warn(WARN, {"kind": "easy"}, EASY, HR_RES, aet_measured=True) is None
    assert TC.session_warn(WARN, {"kind": "easy"}, EASY, HR_RES, aet_measured=False) == WARN   # 0.89 × LTHR cap


def test_intervals_keep_the_warning():
    assert TC.session_warn(WARN, {"kind": "quality", "title": "閾值 3×8 分"}, BAND, HR_RES, aet_measured=True) == WARN
    # on power, not HR: nothing from LTHR
    assert TC.session_warn(WARN, {"kind": "quality"}, BAND, {"a": {"type": "power"}}, aet_measured=False) is None


def test_typed_bpm_is_not_from_lthr():
    assert TC.session_warn(WARN, {"kind": "quality"}, TYPED, HR_RES) is None


@pytest.mark.parametrize("s", [
    AT.session({"cp": 250.0, "lthr": 165.0}, 140.0, 190.0, None, p) for p in ("xu90", "ua60", "ua40", "evoke60", "friel")
] + [{"kind": "test", "title": "Friel 30′ 閾值心率測試"}, {"kind": "test", "title": "最大心率測試：3 趟上坡、最後一趟全力"}])
def test_test_sessions_never(s):
    assert TC.session_warn(WARN, s, BAND + EASY, HR_RES) is None


# ---- the editor endpoint (POST /steps/check) ----------------------------------------------------

def _check(monkeypatch, s, aet_measured, warn):
    th = {"cp": 260.0, "lthr": 168.0, "aet": 150.0, "aet_measured": aet_measured, "thr_warn": warn}

    async def inputs(*a, **k):
        return {"thresholds": dict(th)}
    monkeypatch.setattr(PSAPI, "_inputs", inputs)
    monkeypatch.setattr(PSAPI, "_tpace", lambda: None)
    monkeypatch.setattr(PSAPI, "_speeds", lambda: {})
    monkeypatch.setattr(PP, "load", lambda *a, **k: PP.Prefs(target_basis="hr"))
    monkeypatch.setattr(TP, "_auto_power_ok", lambda: True)
    steps = WS.derive(s, th)
    return asyncio.run(PSAPI.steps_check({**s, "steps": steps}, db=None))


def test_editor_easy_run(monkeypatch):
    s = {"kind": "easy", "title": "輕鬆跑", "minutes": 45, "target": "", "detail": "", "day": "2026-10-06"}
    assert _check(monkeypatch, s, True, WARN)["thr_warn"] is None
    assert _check(monkeypatch, s, False, WARN)["thr_warn"] == WARN


def test_editor_intervals_and_tests(monkeypatch):
    q = {"kind": "quality", "title": "閾值 3×8 分", "minutes": 55, "target": "", "detail": "暖身 15 分，休 2 分",
         "day": "2026-10-07"}
    assert _check(monkeypatch, q, True, WARN)["thr_warn"] == WARN
    for p in ("xu90", "ua60", "evoke60"):
        t = {**AT.session({"cp": 260.0, "lthr": 168.0}, 140.0, 190.0, None, p), "day": "2026-10-08"}
        assert _check(monkeypatch, t, False, WARN)["thr_warn"] is None


def test_editor_manual_note_passes_through(monkeypatch):
    w = {"lthr": {"low": False, "text": "", "note": "手動輸入，沒有驗證"}}
    s = {"kind": "quality", "title": "閾值 3×8 分", "minutes": 55, "target": "", "detail": "", "day": "2026-10-07"}
    assert _check(monkeypatch, s, True, w)["thr_warn"]["lthr"]["note"] == "手動輸入，沒有驗證"
