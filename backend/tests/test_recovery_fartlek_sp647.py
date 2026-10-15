"""SP-647: the recovery week's fartlek (quality_gate.RECOVERY, 4×1′ at 98–101 % CP) done as planned.

① The 課表 judged it with the session classifier's bars (Zone 3 ÷ 10 min, T@VO2max ÷ 4 min): a run at
  threshold is neither, so it read 「換了課：排強度課，實際跑輕鬆」. It is judged by the generator's own
  done rule now (quality_gate.hard_need: 60 % of the reps at ≥ 95 % CP), whatever the classifier says.
② The 間歇判讀 counted the time in the zone from the band's lower edge (98 % CP) while a rep is 達標 from
  0.98 × that: every rep 達標 and still 「未達到」. Its time in the zone counts from Palladino Zone 3B's
  lower edge (95 % CP), the line the generator counts it done by.
③ After 取消配對 the session was matched again at once to another run of the same day; a session the
  user unlinked is now only linked by hand. Synthetic data only."""
from __future__ import annotations

import datetime as dt
from datetime import date

import numpy as np
import pytest

from backend.engine import interval_eval as IE
from backend.engine import plan_match as PM
from backend.engine import quality_gate as QG
from backend.engine import workout_review as WR
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_match import API, _week, act, by_uid, sess
from backend.tests.test_plan_store import Env, g
from backend.tests.test_quality_gate import TODAY, _ds
from backend.tests.wko5_fakes import FakeWorkout

FARTLEK = QG.RECOVERY[1]
CP = 250.0                                          # test_quality_gate._ds


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 2))


def _ses(typ="easy", z3=0.0, eq=0.0):
    """The session classifier's row as the demo run has it (type easy, 「中強度跑」)."""
    return {"type": typ, "type_label": "中強度跑", "z3_s": z3, "t_vo2_eq_s": eq}


def _fartlek(hard_s, minutes=30, session=None):
    a = {**act(42, "2026-10-01", minutes, hard_s=hard_s, tss=25), "session": session or _ses()}
    return sess("f", "2026-10-01", "quality", 37, FARTLEK, state="done", done_by={**a, "match": "plan"})


# ---- ① the 課表 ------------------------------------------------------------------------------------

def test_the_recovery_fartlek_needs_60_percent_of_its_reps():
    assert QG.is_recovery_fartlek(FARTLEK) and QG.is_recovery_fartlek(FARTLEK, None)
    assert not QG.is_recovery_fartlek("有氧間歇（巡航）3×6 分（只排閾值）")
    assert not QG.is_recovery_fartlek(FARTLEK, "v1a")                # a library variant is never it
    assert PM.hard_need({"title": FARTLEK}) == pytest.approx(144)


def test_done_as_planned_is_done_whatever_the_classifier_says():
    # the demo run: 208 s at ≥ 95 % CP against 144 s; the classifier: easy, no Zone 3, no T@VO2max
    vs = PM.compare(_fartlek(208))
    assert not vs["off_plan"] and not vs["short"] and vs["actual"] == "hard" and vs["text"] == ""
    assert vs["intensity_pct"] == 144 and vs["need_min"] == 2.4
    p, grade, d = PM.graded(_fartlek(208), _fartlek(208)["done_by"])
    assert (p, grade, d["by"], d["done_s"], d["need_s"]) == ("hard", "hard", "hard", 208, 144)


def test_the_same_bands_as_any_planned_hard_session():
    # 50–80 % of 144 s = 強度不足, < 50 % = ran easy (INTENSITY_DONE / INTENSITY_OFF, unchanged)
    assert PM.graded(_fartlek(100), _fartlek(100)["done_by"])[1] == "short"
    assert PM.graded(_fartlek(60), _fartlek(60)["done_by"])[1] == "easy"
    assert PM.compare(_fartlek(60, minutes=50))["text"] == "換了課：排強度課，實際跑輕鬆"


def test_other_planned_hard_sessions_keep_the_classifier():
    # a threshold session the classifier read easy (Zone 3 6 of 10 min) stays judged by it, not by hard_s
    q = sess("q", "2026-10-01", "quality", 60, "有氧間歇（巡航）3×6 分（只排閾值）", state="done",
             done_by={**act(9, "2026-10-01", 60, hard_s=1200, tss=48), "session": _ses(z3=360.0)})
    p, grade, d = PM.graded(q, q["done_by"])
    assert d["by"] == "z3" and grade == "short"


# ---- ② the 間歇判讀 ---------------------------------------------------------------------------------

def _fartlek_run(day, rep):
    """15′ easy, 4 × (60 s of `rep` W per second, 2′ jog), 5′ easy."""
    p = [175.0] * 900
    for k in range(4):
        p += list(rep)
        if k < 3:
            p += [150.0] * 120
    p += [175.0] * 300
    t = np.arange(len(p), dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": [150.0] * len(t), "speed": [10.0] * len(t), "power": p,
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(18)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 5.0})


def _card(rep, title=FARTLEK):
    """The 間歇判讀 of the run planned as `title`."""
    ds = _ds([_fartlek_run(TODAY - dt.timedelta(days=1), rep)])
    w = ds.workouts[0]
    ds.plan_rows = {w.idx: {"kind": "quality", "title": title, "minutes": 37, "state": "done"}}
    return ds, w, IE.evaluate(ds, w, with_peers=False)


# each rep: 20 s at 96.5 %, 20 s at 99.5 %, 20 s at 96.5 % CP — mean 97.5 % ≥ the 達標 line 0.98 × 98 %
WOBBLY = [0.965 * CP] * 20 + [0.995 * CP] * 20 + [0.965 * CP] * 20


def test_every_rep_on_target_meets_the_goal():
    ds, w, e = _card(WOBBLY)
    assert e["hit"] == 4 and e["outcome"] == "met"
    assert e["tiz_lo"] == pytest.approx(QG.RECOVERY_JUDGE_LO)       # Palladino 3B's lower edge
    assert e["tiz_ratio"] >= IE.TIZ_GOAL and e["verdict"] == "met" and e["verdict_why"] is None
    v = WR.review(ds, w, "interval_verdict")
    tiz = next(c for c in v["chip_rows"] if c["label"] == "目標區時間")
    assert "238–265 W" in tiz["tip"]                                 # 95 % CP – 101 % × 1.05
    assert "恢復週 fartlek" in tiz["tip"]


def test_reps_under_the_zone_still_miss():
    # 94 % CP: under the rep's 達標 line and under Zone 3B — 未達到 as before
    ds, w, e = _card([0.94 * CP] * 60)
    assert e["hit"] == 0 and e["tiz_ratio"] < IE.TIZ_PART and e["verdict"] == "missed"


def test_other_interval_sessions_keep_their_band():
    # every other spec keeps its own lower edge
    assert IE.tiz_lower(QG.SUB, 0.90) == 0.90 and IE.tiz_lower(None, 0.98) == 0.98
    assert IE.tiz_lower(QG.RECOVERY, 0.98) == pytest.approx(0.95)
    # a personal power-zone model moved the band: the 3B edge moves with it
    assert IE.tiz_lower(QG.RECOVERY, 0.98 * 1.02) == pytest.approx(0.95 * 1.02)


# ---- ③ 取消配對 -------------------------------------------------------------------------------------

def test_an_unlinked_session_is_not_matched_again_on_its_day():
    easy41, iv42 = act(41, "2026-10-01", 10, hour=7), act(42, "2026-10-01", 30, hard_s=208)
    run43 = act(43, "2026-09-30", 45)
    out = [sess("f", "2026-10-01", "quality", 37, FARTLEK), sess("e", "2026-09-30", "easy", 45)]
    # before: the session (unlinked from 42) took the morning run of its day
    PM.assign(out, [run43, easy41, iv42], "2026-10-02", {}, unlinked={42, "f"}, covered="2026-10-01")
    s = by_uid(out)
    assert s["f"]["state"] == "missed" and s["f"]["done_by"] is None
    assert s["e"]["state"] == "done" and s["e"]["done_by"]["index"] == 43     # the others match as before


def test_same_day_matching_is_unchanged_without_an_unlink():
    easy41, iv42 = act(41, "2026-10-01", 10, hour=7), act(42, "2026-10-01", 30, hard_s=208)
    out = [sess("f", "2026-10-01", "quality", 37, FARTLEK)]
    PM.assign(out, [easy41, iv42], "2026-10-02", {}, unlinked={42}, covered="2026-10-01")
    assert out[0]["state"] == "done" and out[0]["done_by"]["index"] == 41   # only the activity was unlinked


def test_api_unlink_keeps_the_session_open_until_linked_by_hand(monkeypatch):
    with Env(monkeypatch) as e:
        _week(e, [g("quality", "quality", FARTLEK, 37, "2026-10-01"), g("easy1", "easy", "輕鬆跑", 45, "2026-10-03")],
              monkeypatch)
        iv, morning = act(42, "2026-10-01", 30, hard_s=208), act(41, "2026-10-01", 10, hour=7)
        e.inp["activities"] = [morning, iv]
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        f = next(s for s in ss if s.get("gen_key") == "quality")
        assert f["state"] == "done" and f["done_by"]["index"] == 42
        assert e.c.delete(f"{API}/sessions/{f['uid']}/link").status_code == 200
        ss = by_uid(e.c.get(f"{API}/sessions").json()["sessions"])
        assert ss[f["uid"]]["state"] == "missed" and ss[f["uid"]]["done_by"] is None
        e.c.post(f"{API}/reconcile")
        assert by_uid(e.c.get(f"{API}/sessions").json()["sessions"])[f["uid"]]["state"] == "missed"
        # linked by hand: done (match manual)
        r = e.c.post(f"{API}/sessions/{f['uid']}/link", json={"index": 41})
        assert r.status_code == 200 and r.json()["done_by"]["match"] == "manual"
        assert by_uid(e.c.get(f"{API}/sessions").json()["sessions"])[f["uid"]]["done_by"]["index"] == 41
