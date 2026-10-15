"""SP-646: marathon pace (MP) is its own 中強度 class, not hard time (SP-644, the owner's option B).

  * the structure editor's totals split into 中強度 (80–95 % CP, Palladino's three-zone summary =
    Seiler's zone 2) / 高強度 (≥ 95 %) / 5 區 (≥ 106 %); an MP step is always 中強度;
  * a planned MP long run run as planned is not a 高強度長跑 and its 課表 card has no ◐;
  * the card's MP load is the structure's (IF² × 100 per hour), not a fixed 70 / h;
  * the planner is untouched: the weekly interval budget, the 48 h spacing and the Zone 3 cap.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import calibrate as CAL
from backend.engine import overview as O
from backend.engine import plan_match as PM
from backend.engine import plan_prefs as PP
from backend.engine import quality_gate as QG
from backend.engine import session_stimulus as SS
from backend.engine import session_structure as SST
from backend.engine import workout_review as WR
from backend.engine import workout_steps as WS
from backend.engine import zones as Z

CP, LTHR = 250.0, 168.0
TH = {"cp": CP, "lthr": LTHR, "aet": 150.0, "tpace": 270.0}     # 4:30 / km threshold pace
TH_NO_TP = {"cp": CP, "lthr": LTHR, "aet": 150.0}


@pytest.fixture(autouse=True)
def _nothing_stored(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)


def _totals(s: dict, th: dict) -> dict:
    return WS.totals(WS.normalize(WS.derive(s, th)), WS.Ctx.of(th))


def _one(target: dict, sec: int = 1200, kind: str = "work", note: str = "") -> dict:
    return WS.normalize({"items": [{"kind": kind, "dur": {"type": "time", "value": sec}, "target": target,
                                    "note": note}]})


# ---- 1 · the editor's totals: 中強度 / 高強度 / 5 區 ---------------------------------------------

def test_the_bands_are_palladinos_three_zone_summary():
    assert WS.MOD_FRAC == Z.PALLADINO_3ZONE["low"] == 0.80
    assert WS.HARD_FRAC == Z.PALLADINO_3ZONE["high"] == 0.95
    assert WS.Z5_FRAC == pytest.approx(Z.Z5_LO)


@pytest.mark.parametrize("m,share", [(120, 0.20), (120, 0.40), (150, 0.40), (180, 0.40)])
def test_an_mp_long_run_is_moderate_time_not_hard(m, share):
    s = O.road_long_session(m, "specific", 150.0, 70.0, mp={"share": share})
    mp = WS.mp_minutes(s)
    t = _totals(s, TH)
    assert t["mod_s"] == mp * 60
    assert t["hard_s"] == 0 and t["z5_s"] == 0


@pytest.mark.parametrize("th,goal", [(TH, None), (TH, 286.0), (TH, 300.0), (TH_NO_TP, None),
                                     (TH, 262.0)])          # 262 s/km: a goal faster than 1.0 × tpace
def test_mp_is_moderate_on_every_target_path(th, goal):
    # with / without a threshold pace (the HR fallback), a goal pace slower or even faster than the
    # threshold pace: the same 60 min of 中強度 (before: 60 min ≥ 88 % with a threshold pace, 0 without)
    s = O.road_long_session(150, "specific", 150.0, 70.0, goal_pace=goal, mp={"share": 0.40})
    t = _totals(s, th)
    assert t["mod_s"] == 60 * 60 and t["hard_s"] == 0


def test_the_bands_by_target():
    c = WS.Ctx.of(TH, "power")
    pw = lambda lo, hi: {"type": "power", "mode": "pct", "lo": lo, "hi": hi}
    cruise = WS.totals(_one(pw(0.88, 0.95)), c)                  # Palladino 3A: the Zone 3 cruise rung
    assert cruise["mod_s"] == 1200 and cruise["hard_s"] == 0
    near = WS.totals(_one(pw(0.97, 1.01)), c)                    # 3B
    assert near["mod_s"] == 0 and near["hard_s"] == 1200 and near["z5_s"] == 0
    vo2 = WS.totals(_one(pw(1.06, 1.12)), c)
    assert vo2["hard_s"] == 1200 and vo2["z5_s"] == 1200 and vo2["mod_s"] == 0
    endurance = WS.totals(_one(pw(0.70, 0.78)), c)
    assert endurance["mod_s"] == endurance["hard_s"] == 0


def test_an_hr_tempo_at_90_95_lthr_is_moderate():
    # SP-642 Q1-3: a 90–95 % LTHR step (hr_to_p ≈ 0.91 CP) is 中強度 by the same bands — no rule of its own
    c = WS.Ctx.of(TH, "hr")
    t = WS.totals(_one({"type": "hr", "mode": "pct", "lo": 0.90, "hi": 0.95}), c)
    assert t["mod_s"] == 1200 and t["hard_s"] == 0


def test_easy_steps_are_never_moderate():
    s = O.road_long_session(150, "base", 150.0, 70.0)
    t = _totals(s, TH)
    assert t["mod_s"] == 0 and t["hard_s"] == 0


def test_the_editor_shows_three_cells():
    src = open(WS.__file__.replace("engine/workout_steps.py", "static/workout_editor.js"), encoding="utf-8").read()
    assert 'tr("workout.mod_time")' in src and "t.mod_s" in src
    assert 'tr("workout.hard_time")' in src and 'tr("workout.z5_time")' in src
    import json
    import os
    base = os.path.join(os.path.dirname(WS.__file__), "..", "static", "i18n")
    zh = json.load(open(os.path.join(base, "zh-TW", "common.json"), encoding="utf-8"))
    en = json.load(open(os.path.join(base, "en", "common.json"), encoding="utf-8"))
    assert zh["workout.mod_time"] == "中強度（80–95）" and zh["workout.hard_time"] == "高強度（≥ 95）"
    assert zh["workout.z5_time"] == "5 區（≥ 106）"
    assert en["workout.mod_time"] == "Moderate (80–95)" and en["workout.hard_time"] == "High (≥ 95)"
    assert "workout.intensity_tip" in zh and "workout.intensity_tip" in en


# ---- 2 · the done side: a planned MP long run run as planned --------------------------------

def _stream(total_min, mp_min, mp_pct, easy_hr, mp_hr, drift, extra_z3_min=0):
    n = total_min * 60
    t = np.arange(n, dtype=float)
    p = np.full(n, 0.72 * CP)
    h = np.full(n, easy_hr, dtype=float)
    a = (total_min - mp_min - 10) * 60
    b = a + mp_min * 60
    p[a:b] = mp_pct * CP
    h[a:b] = mp_hr + drift * (np.arange(b - a) / (b - a))
    if extra_z3_min:                       # a hard block early in the easy part, not in the plan
        p[600:600 + extra_z3_min * 60] = 0.97 * CP
    rng = np.random.default_rng(1)
    p = p + rng.normal(0, 6, n)
    return t, h, p, np.full(n, 3.3)


# SP-644's six scenarios (sp644_done.py): total, MP min, % CP, easy HR, MP HR start, drift
SIX = [(120, 25, 0.94, 138, 152, 4), (150, 60, 0.94, 140, 154, 6), (150, 60, 0.94, 142, 158, 8),
       (180, 70, 0.94, 140, 155, 8), (180, 75, 0.94, 145, 160, 8), (150, 60, 0.90, 140, 152, 5)]


def _judge(tot, mp, pct, eh, mh, dr, cap, planned_mp_s, extra=0):
    from backend.engine import hr_profile as HP
    t, h, p, sp = _stream(tot, mp, pct, eh, mh, dr, extra)
    st = SS.measure(t, hr=h, power=p, speed=sp, cp=CP, lthr=LTHR, category="road")
    v = SS.verdict(st, "road", 190.0, HP.is_easy(float(np.mean(h)), cap))
    v = SS.planned_mp(v, planned_mp_s)
    typ, _stim = WR.session_class("road", tot * 60.0, "", stimulus=v["stimulus"])
    s = {"kind": "long", "id": "long", "minutes": tot, "title": f"長跑＋馬拉松配速 {mp} 分", "state": "done",
         "tss": 175.0, "terrain": "road"}
    a = {"session": {"type": typ, "z3_s": v["z3_s"], "t_vo2_eq_s": v["t_vo2_eq_s"]}, "moving_s": tot * 60.0,
         "category": "road", "tss": 175.0}
    return v, typ, PM.graded(s, a)[1], PM.compare({**s, "done_by": a}, 175.0) or {}


@pytest.mark.parametrize("cap", [151.0, 145.0, 140.0])
@pytest.mark.parametrize("row", SIX)
def test_the_six_sp644_scenarios_read_as_planned(row, cap):
    v, typ, grade, cmp = _judge(*row, cap=cap, planned_mp_s=row[1] * 60)
    assert typ == "long" and grade == "easy"
    assert not cmp.get("short") and not cmp.get("off_plan") and not cmp.get("short_text")


def test_without_the_plan_the_low_cap_still_reads_hard_long():
    # the exemption comes from what was planned: the same run unplanned keeps SP-644's reading
    _v, typ, grade, cmp = _judge(*SIX[1], cap=140.0, planned_mp_s=0)
    assert typ == "hard_long" and grade == "hard" and cmp.get("short")


def test_zone3_well_past_the_planned_mp_is_still_a_hard_long_run():
    # 60 min planned at MP, 15 more min hard in the easy part → Zone 3 beyond the plan ≥ 10 min
    v, typ, _g, _c = _judge(*SIX[1], cap=140.0, planned_mp_s=3600, extra=15)
    assert v["z3_s"] - 3600 >= SS.Z3_NEED_S
    assert v["stimulus"] == "z3" and typ == "hard_long"


def test_planned_mp_keeps_zone5_and_easy_verdicts():
    z5 = {"stimulus": "z5", "z3_s": 3600.0, "why": "x"}
    assert SS.planned_mp(z5, 3600) == z5
    none = {"stimulus": None, "z3_s": 0.0, "why": "x"}
    assert SS.planned_mp(none, 3600) == none
    z3 = {"stimulus": "z3", "z3_s": 3700.0, "why": "x"}
    got = SS.planned_mp(z3, 3600)
    assert got["stimulus"] is None and got["planned_mp"] is True and "馬拉松配速" in got["why"]


def test_mp_seconds_of_a_planned_row():
    assert WS.mp_seconds({"kind": "long", "title": "長跑＋馬拉松配速 60 分", "minutes": 150}) == 3600
    assert WS.mp_seconds({"kind": "long", "title": "LSD（路跑）", "minutes": 150}) == 0
    assert WS.mp_seconds({"kind": "quality", "title": "長跑＋馬拉松配速 60 分"}) == 0
    # an edited structure: its MP steps
    s = O.road_long_session(150, "specific", 150.0, 70.0, mp={"share": 0.40})
    st = WS.derive(s, TH)
    for row in WS.flat(st["items"]):
        if row["st"].get("note") == "馬拉松配速":
            row["st"]["dur"]["value"] = 45 * 60
    assert WS.mp_seconds({**s, "steps": st}) == 45 * 60


def _ds_with(row):
    w = SimpleNamespace(idx=7, day=20000.0, sport="run")
    return SimpleNamespace(plan_rows={}, plan_mp_rows={7: row}, workouts=[], memo={}), w


def test_stimulus_reads_the_planned_mp_long_run(monkeypatch):
    t, h, p, sp = _stream(150, 60, 0.94, 140, 154, 6)
    st = SS.measure(t, hr=h, power=p, speed=sp, cp=CP, lthr=LTHR, category="road")
    monkeypatch.setattr(WR, "hr_peak", lambda ds, w: (190.0, "plan"))
    m = {"stim": st, "avg_hr": float(np.mean(h)), "easy_cap": {"value": 140.0}}
    ds, w = _ds_with({"kind": "long", "title": "長跑＋馬拉松配速 60 分", "minutes": 150})
    got = WR._stimulus(ds, w, m, "road")
    assert got["stimulus"] is None and got.get("planned_mp")
    ds2, w2 = _ds_with({"kind": "long", "title": "LSD（路跑）", "minutes": 150})
    assert WR._stimulus(ds2, w2, m, "road")["stimulus"] == "z3"           # an all-easy long run: as before


# ---- 3 · the card's MP load = the structure's ---------------------------------------------

def test_the_card_mp_rate_is_the_structures():
    assert WS.mp_tss_per_hour() == pytest.approx(100.0 / 1.06 ** 2)
    assert WS.mp_tss_per_hour(goal_pace=300.0, tpace=270.0) == pytest.approx(100.0 * (270 / 300) ** 2)
    assert WS.mp_tss_per_hour(goal_pace=300.0) == pytest.approx(100.0 / 1.06 ** 2)   # no tpace: the default


@pytest.mark.parametrize("goal", [None, 286.0, 300.0])
def test_the_card_tss_equals_the_structure_tss(goal):
    rates = {"road": 70.0, "easy": 70.0}
    s = O.road_long_session(150, "specific", 150.0, 70.0, goal_pace=goal, mp={"share": 0.40}, tpace=TH["tpace"])
    f = SST.figures(s, TH, rates)
    assert f["minutes"] == s["minutes"]
    assert f["tss"] == pytest.approx(s["tss"], abs=0.5)


# ---- 4 · the planner is untouched -----------------------------------------------------------

def test_an_mp_long_run_has_no_interval_minutes():
    # the weekly interval budget (QUALITY_SHARE_MAX) and the Zone 3 cap read the interval sessions only
    s = O.road_long_session(150, "specific", 150.0, 70.0, mp={"share": 0.40})
    assert O.session_tiz_min(s) == 0.0
    assert QG.z3_budget_min(6.0) == pytest.approx(36.0)


MON = dt.date(2026, 10, 5)
WEEK = [MON + dt.timedelta(days=i) for i in range(7)]


def _todo():
    return [{"id": "quality", "kind": "quality", "title": "閾值 3×8 分", "minutes": 45, "day": None},
            {"id": "easy1", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": None},
            {"id": "easy2", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": None}]


def test_48h_after_a_done_mp_long_run_is_the_same_as_hard_long_or_long():
    # a done planned MP long run on Monday: read as 長跑 now (not in hard_done), before as 高強度長跑 (in it);
    # the long day itself keeps the interval 48 h away either way (long_done)
    p = PP.Prefs(long_day="mon")
    a, b = _todo(), _todo()
    PP.place(a, WEEK[1:], 0, p, notes=[], long_done=MON, hard_done=[MON])
    PP.place(b, WEEK[1:], 0, p, notes=[], long_done=MON, hard_done=[])
    assert [x["day"] for x in a] == [x["day"] for x in b]
    q = dt.date.fromisoformat(next(x for x in b if x["id"] == "quality")["day"])
    assert (q - MON).days >= 2


def test_done_mp_long_rows_are_apart_from_done_plan(tmp_path):
    # the done MP long runs (by title) — not in done_plan, which quality_gate reads as planned intervals
    import sqlite3
    from sqlalchemy import create_engine
    from backend.db.models import Base
    from backend.engine import plan_store as PS
    db = tmp_path / "w.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(db)
    for uid, title, state, done in (("mp", "長跑＋馬拉松配速 60 分", "done", '{"index": 5}'),
                                    ("lsd", "LSD（路跑）", "done", '{"index": 6}'),
                                    ("open", "長跑＋馬拉松配速 50 分", "active", None)):
        con.execute("INSERT INTO plan_sessions (athlete_id, uid, week_start, day, kind, title, minutes, origin, edited,"
                    " provisional, state, done_by, updated_at) VALUES (1, ?, '2026-10-12', '2026-10-15', 'long', ?,"
                    " 150, 'auto', 0, 0, ?, ?, '2026-10-12')", (uid, title, state, done))
    con.commit()
    con.close()
    got = PS.done_mp_long(db)
    assert set(got) == {5} and WS.mp_seconds(got[5]) == 3600
    assert PS.done_plan(db) == {}
