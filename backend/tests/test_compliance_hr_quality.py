"""光學心率品質差時完成度不往下判 (SP-402; docs/research/positive-compliance.md §4 原則 5, §8 ④): a
done session whose activity's HR is unreliable (`done_by.hr_quality == "low"`, the whole-run SP-266
check — spikes, steps, flat line over 3 %) is graded on time only: its TSS (from that HR) never makes
it 部分, and an intensity shortfall read from it neither; `uncertain` True, and the cards / dialog /
總覽 hover say 「心率品質不穩，依時間判定」. Synthetic data only."""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

from backend.engine import compliance as C
from backend.engine import overview as O
from backend.engine import workout_review as R
from backend.tests import test_overview_cards_sp397 as OV
from backend.tests import test_schedule_cards_sp396 as SCHED
from backend.tests.test_drift_hr_quality import _spiky
from backend.tests.test_quality_gate import TODAY, _ds, _plan
from backend.tests.test_workout_review import _run

STATIC = Path(__file__).resolve().parents[1] / "static"


def _done(hq=None, tss=0.60, dur=0.95):
    a = {"moving_s": 60 * 60 * dur, "tss": 70 * tss, "category": "road"}
    if hq is not None:
        a["hr_quality"] = hq
    return {"kind": "quality", "state": "done", "minutes": 60, "tss": 70, "done_by": a}


def test_low_hr_quality_grades_on_time_only():
    low = C.session_compliance(_done("low"))
    assert low["level"] == "green" and low["uncertain"] is True
    assert low["pct"] == 95 and low["duration_pct"] == 95 and low["tss_pct"] == 60     # TSS shown, not used
    assert C.status_of({**_done("low"), "day": "2026-10-01"}, low, "2026-10-10") == "done"
    ok = C.session_compliance(_done("ok"))
    assert ok["level"] == "yellow" and not ok.get("uncertain")
    assert C.status_of({**_done("ok"), "day": "2026-10-01"}, ok, "2026-10-10") == "partial"
    assert C.session_compliance(_done(None))["level"] == "yellow"                       # not checked: as before


def test_low_hr_quality_still_grades_down_on_time_and_sport():
    late = C.session_compliance(_done("low", tss=1.0, dur=0.6))
    assert late["level"] == "yellow" and late["uncertain"] is True                      # time is measured
    bike = _done("low", tss=1.0, dur=1.0)
    bike["done_by"]["category"] = "bike"
    assert C.session_compliance(bike)["level"] == "red"                                 # the sport is no HR reading


def test_an_intensity_shortfall_read_from_bad_hr_does_not_grade_down():
    vs = {"short": True, "grade": "short", "intensity_pct": 60, "short_text": "強度不足"}
    low = C.with_plan_check(C.session_compliance(_done("low", tss=0.6, dur=1.0)), vs)
    assert low["level"] == "green" and low["uncertain"] and not low.get("short")
    ok = C.with_plan_check(C.session_compliance(_done("ok", tss=1.0, dur=1.0)), vs)
    assert ok["level"] == "yellow" and ok["short"]
    off = C.with_plan_check(C.session_compliance(_done("low", tss=1.0, dur=1.0)), {"off_plan": True, "text": "沒照課表：騎車"})
    assert off["off_plan"]                                                              # 換了課 stays


def test_the_whole_run_check_feeds_done_by():
    plan = _plan(aethr=140, lthr=165)
    spiky = _spiky(TODAY - dt.timedelta(days=5))
    clean = _run(TODAY - dt.timedelta(days=4), minutes=60, hr=140)
    clean.channels["heartrate"] = list(np.asarray(clean.channels["heartrate"], float) + 1.5 * np.sin(np.arange(len(clean.channels["heartrate"])) / 11.0))
    ds = _ds([spiky, clean], plan)
    rows = {r["date"]: r for r in (O.activity_row(w, ds) for w in ds.workouts)}
    assert rows[(TODAY - dt.timedelta(days=5)).isoformat()]["hr_quality"] == "low"
    assert rows[(TODAY - dt.timedelta(days=4)).isoformat()]["hr_quality"] == "ok"
    assert R.run_hr_quality(ds, ds.workouts[0]) in ("low", "ok")
    assert "hr_quality" not in rows[(TODAY - dt.timedelta(days=5)).isoformat()]["session"]


def _uncertain_session():
    s = SCHED._done("green", uncertain=True)
    s["compliance"]["pct"] = 95
    return s


def test_cards_dialog_and_overview_say_judged_by_time():
    words = json.loads((STATIC / "i18n" / "zh-TW" / "common.json").read_text("utf-8"))["legend.hr_uncertain"]
    assert words == "心率品質不穩，依時間判定"
    assert json.loads((STATIC / "i18n" / "en" / "common.json").read_text("utf-8"))["legend.hr_uncertain"]
    s = _uncertain_session()
    chip = SCHED._run("chipHtml(DATA.s, false)", s=s)
    assert "cp-ok" in chip and "sc-r-done" in chip
    # SP-470: the card's hover is sesspop.js's card from popModel (no plain title text) — same words
    assert words in SCHED._run("popModel(DATA.s)", s=s)["hr"]
    assert words in SCHED._run("vsHtml(DATA.s)", s=s)
    assert words in OV._tip(OV._card(s))
    plain = SCHED._run("chipHtml(DATA.s, false)", s=SCHED._done("green"))
    assert words not in plain
