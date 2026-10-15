"""SP-618: the interval judging (the 達標 line 0.98 × lo, the time in zone), the structure editor's
power 區間 choice and the activity classifier's edges follow 設定 → 課表功率區間; with Palladino every
result is exactly what it was. The classifier's change reaches the runs measured after a switch only
(the per-run measurements are cached; the user decides about re-measuring the old ones).
Synthetic only."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from backend.engine import interval_library as IL
from backend.engine import power_zone_plan as PZP
from backend.engine import workout_steps as WS

CP = 250.0
PARAMS = [15000.0, 20.0, 250.0, 25.0, 2400.0, -30.0]


def pm(system: str) -> dict:
    return {"system": system, "setting": system, "fallback": "", "levels": PZP.static_levels(system)}


def personal_pm(cp: float = CP) -> dict:
    from backend.engine.algorithms.wko5_levels import levels
    lv = levels(PARAMS)
    rows = [[str(i + 1), n, lo / cp, None if hi != hi else hi / cp] for i, ((lo, hi), n) in
            enumerate(zip(lv, PZP.PERSONAL_NAMES))]
    return {"system": "personal", "setting": "personal", "fallback": "", "levels": rows,
            "scale": PARAMS[2] / cp, "pd": {"params": PARAMS, "tte": 2100.0}}


# ---------------------------------------------------------------------------
# the band a planned session is judged in
# ---------------------------------------------------------------------------

def test_palladino_judges_the_planned_band_itself():
    from backend.engine import quality_gate as QG
    v = IL.get("v1a")
    spec = QG.variant_tuple(v)
    for p in (None, pm("palladino")):
        assert PZP.judge_spec(spec, p) is spec and PZP.judge_spec(v, p) is v


def test_another_set_judges_its_own_band():
    from backend.engine import quality_gate as QG
    v = IL.get("v1a")                                         # 106–112 % CP (Palladino 5)
    spec = QG.variant_tuple(v)
    assert PZP.judge_spec(spec, pm("stryd"))[5:7] == (1.00, 1.15)        # Stryd Interval
    assert PZP.judge_spec(spec, pm("classic"))[5:7] == (1.06, 1.21)      # classic VO2max
    j = PZP.judge_spec(v, pm("stryd"))
    assert (j.lo, j.hi, j.n, j.work_s) == (1.00, 1.15, v.n, v.work_s)
    # a 個人化間歇 is already the model's band; a detected set / an open band stay
    opt = IL.adjust(v, {"opt": {"level": 2, "work_s": 300, "reps": 3, "rest_s": 300, "lo": 0.9, "hi": 0.95}})
    assert PZP.judge_spec(opt, pm("stryd")) is opt
    det = ("detected", "x", 3, 2.0, 2, 1.06, 1.12, False, "")
    assert PZP.judge_spec(det, pm("stryd")) is det


def _v1a_run(day, on):
    from backend.tests.wko5_fakes import FakeWorkout
    p = [175.0] * 900
    for k in range(5):
        p += [on] * 120
        if k < 4:
            p += [120.0] * 120
    p += [150.0] * 300
    t = np.arange(len(p), dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": [140.0] * len(t), "speed": [10.0] * len(t), "power": p,
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 5.0})


def _own(ds):
    """The app's own mode (the WKO5 cross-check mode, parity, has no 課表功率區間: Palladino)."""
    from backend.engine.wko5expr.config import EngineConfig
    ds.config = EngineConfig(parity=False)
    return ds


def _planned_ds(on: float):
    from backend.tests.test_quality_gate import TODAY, _ds
    ds = _own(_ds([_v1a_run(TODAY - dt.timedelta(days=3), on)]))
    ds.plan_rows = {w.idx: {"title": IL.title(IL.get("v1a")), "variant_key": "v1a", "rung_key": "z5a",
                            "equiv": True, "state": "done"} for w in ds.workouts}
    return ds


@pytest.mark.needs_core
def test_the_interval_card_judges_in_the_set(monkeypatch):
    """5×2′ run at 102 % CP against V1 (Palladino 106–112 %): Palladino says 「第 1 趟就沒到」; with Stryd
    in effect the plan said 100–115 %, so every rep is 達標 and the time in zone counts from 100 %."""
    from backend.engine import interval_eval as IE
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "palladino")
    ds = _planned_ds(255.0)
    pal = IE.evaluate(ds, ds.workouts[0], with_peers=False)
    assert pal["lo"] == pytest.approx(1.06) and pal["hit"] == 0 and pal["outcome"] == "too_high"
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "stryd")
    ds = _planned_ds(255.0)
    st = IE.evaluate(ds, ds.workouts[0], with_peers=False)
    assert st["lo"] == pytest.approx(1.00) and st["hi"] == pytest.approx(1.15)
    assert st["hit"] == 5 and st["outcome"] == "met" and st["tiz_ratio"] == pytest.approx(1.0, abs=0.05)
    assert st["floor"] == pytest.approx(st["in_band_tol"] * 1.00 * CP)


@pytest.mark.needs_core
def test_with_palladino_the_card_is_exactly_as_before(monkeypatch):
    from backend.engine import interval_eval as IE
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "palladino")
    ds = _planned_ds(272.0)
    got = IE.evaluate(ds, ds.workouts[0], with_peers=False)
    monkeypatch.setattr(PZP, "judge_spec", lambda spec, p: spec)        # the SP-618 path off
    ds = _planned_ds(272.0)
    want = IE.evaluate(ds, ds.workouts[0], with_peers=False)
    assert got == want


def test_the_ladder_judges_in_the_same_band():
    """quality_gate's state machine reads dose_history's band (`judge`) for the same planned band."""
    from backend.engine import quality_gate as QG
    spec = QG.variant_tuple(IL.get("v1a"))
    judge = {"from": [1.06, 1.12], "to": [1.00, 1.15]}
    assert QG.judged_spec(spec, judge)[5:7] == (1.00, 1.15)
    assert QG.judged_spec(spec, None) is spec
    assert QG.judged_spec(QG.variant_tuple(IL.get("v2a")), judge)[5:7] == (1.05, 1.10)   # another band: as is
    h = {"bouts": [{"power": 255.0}] * 5, "cp": CP, "variant_key": "v1a", "rung_key": "z5a", "equiv": True,
         "tiz_ratio": 1.0}
    a = dict(h)
    QG.dose_step([a], track="z5")
    b = {**h, "judge": judge}
    QG.dose_step([b], track="z5")
    assert a["outcome"] == "too_high" and b["outcome"] == "met"


# ---------------------------------------------------------------------------
# the structure editor
# ---------------------------------------------------------------------------

def test_the_editor_offers_the_sets_zones():
    c = WS.Ctx(cp=CP, lthr=165.0, basis="power", pzm=pm("stryd"))
    rows = WS.zones_table(c)["power"]
    cur = [r for r in rows if not r.get("legacy")]
    assert [r["id"] for r in cur] == ["S1", "S2", "S3", "S4", "S5"]
    assert cur[2]["label"] == "Stryd Z3 Threshold" and cur[2]["text"] == "225–250 W"
    assert [r["id"] for r in rows if r.get("legacy")] == [z for z, _lo, _hi in WS.POWER_ZONES]
    cl = [r for r in WS.zones_table(WS.Ctx(cp=CP, pzm=pm("classic")))["power"] if not r.get("legacy")]
    assert [r["id"] for r in cl] == [f"C{i}" for i in range(1, 8)] and cl[3]["label"] == "L4 閾值"
    pe = [r for r in WS.zones_table(WS.Ctx(cp=CP, pzm=personal_pm()))["power"] if not r.get("legacy")]
    # SP-661: the running levels, R1–R10
    assert [r["id"] for r in pe] == [f"R{i}" for i in range(1, 11)] and pe[4]["label"] == "L5 準閾值"


def test_with_palladino_the_editor_is_as_before():
    want = [{"id": z, "label": f"Z{z}", "lo": lo, "hi": hi, "text": f"{lo * CP:.0f}–{hi * CP:.0f} W"}
            for z, lo, hi in WS.POWER_ZONES]
    for p in (None, pm("palladino")):
        assert WS.zones_table(WS.Ctx(cp=CP, pzm=p))["power"] == want


def test_a_set_zone_resolves_saves_and_ranges():
    c = WS.Ctx(cp=CP, lthr=165.0, basis="power", pzm=pm("stryd"))
    st = {"kind": "work", "dur": {"type": "time", "value": 600},
          "target": {"type": "power", "mode": "zone", "zone": "S3"}}
    r = WS.resolve(st, c)
    assert (r.lo, r.hi) == (225, 250)
    errs: list = []
    t = WS._norm_target({"type": "power", "mode": "zone", "zone": "S4", "zone_to": "S2"}, errs)
    assert not errs and (t["zone"], t["zone_to"]) == ("S2", "S4")
    assert WS.zone_span("power", "S2", "S4") == (0.80, 1.15)
    errs = []
    WS._norm_target({"type": "power", "mode": "zone", "zone": "S2", "zone_to": "3A"}, errs)
    assert errs                                                  # two sets: not one run
    assert WS.zone_range_text({"zone": "S2", "zone_to": "S4"}) == "S2–S4"
    # a step saved with a Palladino id keeps working under another set (legacy)
    old = {**st, "target": {"type": "power", "mode": "zone", "zone": "3B"}}
    assert (WS.resolve(old, c).lo, WS.resolve(old, c).hi) == (238, 252)
    # a 個人化區間 id needs that set in effect
    p3 = {**st, "target": {"type": "power", "mode": "zone", "zone": "R6"}}
    assert WS.resolve(p3, c).err
    rp = WS.resolve(p3, WS.Ctx(cp=CP, basis="power", pzm=personal_pm()))
    assert (rp.lo, rp.hi) == (round(0.95 * 250), round(1.02 * 250))
    # SP-661: a step saved with the 9 cycling levels' id (P5 閾值 95–105 %) reads as the running 閾值
    old5 = {**st, "target": {"type": "power", "mode": "zone", "zone": "P5"}}
    ro = WS.resolve(old5, WS.Ctx(cp=CP, basis="power", pzm=personal_pm()))
    assert (ro.lo, ro.hi) == (rp.lo, rp.hi)
    errs = []
    assert WS._norm_target({"type": "power", "mode": "zone", "zone": "P2", "zone_to": "P3"}, errs) == \
        {"type": "power", "mode": "zone", "zone": "R2", "zone_to": "R4"} and not errs


def test_the_editor_words_name_no_wko5():
    for p in (pm("stryd"), pm("classic"), personal_pm()):
        for r in WS.zones_table(WS.Ctx(cp=CP, pzm=p))["power"]:
            assert "wko" not in r["label"].lower() and "ilevel" not in r["label"].lower()


# ---------------------------------------------------------------------------
# the activity classifier
# ---------------------------------------------------------------------------

def test_the_classifier_edges_follow_the_set():
    assert PZP.class_edges(None) == PZP.class_edges(pm("palladino")) == {"z3": 0.88, "p_lo": 1.03, "p_hi": 1.06}
    assert PZP.class_edges(pm("stryd")) == {"z3": 0.90, "p_lo": 1.00, "p_hi": 1.00}
    assert PZP.class_edges(pm("classic")) == {"z3": 0.91, "p_lo": 1.03, "p_hi": 1.06}
    e = PZP.class_edges(personal_pm(260.0))
    k = 250.0 / 260.0
    # SP-661: Zone 5's floor is the running levels' FRC/FTP, 102 % of the model's FTP (was 105 %)
    assert e["z3"] == pytest.approx(0.88 * k, abs=1e-4) and e["p_hi"] == pytest.approx(1.02 * k, abs=1e-4)
    assert e["p_lo"] == pytest.approx(min(1.03 * k, 1.02 * k), abs=1e-4)


def test_vo2_bouts_and_zone3_time_use_the_edges():
    """3×3′ at 102 % CP: no VO2 bout on Palladino (< 106 % for 2′, < 5′ at ≥ 103 %), three on Stryd's
    Interval zone (≥ 100 %); Palladino's result is the one without edges."""
    from backend.engine import session_stimulus as SS
    p = [150.0] * 600
    for _ in range(3):
        p += [255.0] * 180 + [150.0] * 180
    t = np.arange(len(p), dtype=float)
    pal = SS.measure(t, None, np.array(p), np.full(len(p), 3.0), None, None, CP, None, "road")
    assert pal["t_vo2_power_s"] == 0 and pal["vo2_bouts"] == [] and "edges" not in pal
    assert SS.measure(t, None, np.array(p), np.full(len(p), 3.0), None, None, CP, None, "road",
                      PZP.class_edges(pm("palladino")))["t_vo2_power_s"] == 0
    st = SS.measure(t, None, np.array(p), np.full(len(p), 3.0), None, None, CP, None, "road",
                    PZP.class_edges(pm("stryd")))
    assert len(st["vo2_bouts"]) == 3 and st["t_vo2_power_s"] > 0 and st["edges"]["p_hi"] == 1.00


def test_runs_measured_before_a_switch_keep_their_class(monkeypatch):
    """The per-run measurements are cached on the run (workout_review.measure's cache key does not
    carry the set): a switch changes the runs measured after it, the old ones keep theirs."""
    from backend.engine import workout_review as WR
    from backend.tests.test_quality_gate import TODAY, _ds
    store: dict = {}

    def cached(key, w, fn):
        k = (key, w.idx)
        if k not in store:
            store[k] = fn()
        return store[k]
    run = _v1a_run(TODAY - dt.timedelta(days=3), 255.0)
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "palladino")
    ds = _own(_ds([run]))
    ds.cached_series = cached
    before = WR.measure(ds, ds.workouts[0])["stim"]
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "stryd")
    ds2 = _own(_ds([run]))
    ds2.cached_series = cached
    assert WR.measure(ds2, ds2.workouts[0])["stim"] == before            # measured before: kept
    store.clear()
    after = WR.measure(ds2, ds2.workouts[0])["stim"]                      # a new measurement: the set's edges
    assert after.get("edges") == PZP.class_edges(pm("stryd"))
    assert after["t_vo2_power_s"] > before["t_vo2_power_s"]


@pytest.mark.needs_core
def test_a_personal_interval_is_judged_by_its_own_reps_and_power(monkeypatch):
    """SP-590 + SP-618: a 個人化間歇 (variant_adj["opt"]) is judged by the model's rep power and its reps,
    whatever the set reads on the day (it is not mapped again)."""
    from backend.engine import interval_eval as IE
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "personal")
    ds = _planned_ds(255.0)
    opt = {"level": 2, "work_s": 120, "reps": 5, "rest_s": 120, "lo": 1.00, "hi": 1.04, "w": 255}
    for r in ds.plan_rows.values():
        r["variant_adj"] = {"opt": opt}
    e = IE.evaluate(ds, ds.workouts[0], with_peers=False)
    assert e["lo"] == pytest.approx(1.00) and e["hi"] == pytest.approx(1.04)
    assert e["n_plan"] == 5 and e["works"] == [120] * 5 and e["hit"] == 5 and e["outcome"] == "met"
    monkeypatch.setattr(PZP, "setting", lambda user_id=1: "stryd")
    ds2 = _planned_ds(255.0)
    for r in ds2.plan_rows.values():
        r["variant_adj"] = {"opt": opt}
    assert IE.evaluate(ds2, ds2.workouts[0], with_peers=False)["lo"] == pytest.approx(1.00)
