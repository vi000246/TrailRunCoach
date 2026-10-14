"""SP-511 (SP-69 #6): the easy-run % CP and the RPE → % CP scale per athlete
(engine/intensity_calib.py, two engine/calibrate.py items) — the default below the minimum data,
the shrinkage and the bounds, what the settings list shows, and that a refit never re-pushes a
workout already on the watch."""
from __future__ import annotations

import datetime as dt
import math
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import calibrate as CAL
from backend.engine import intensity_calib as ICAL
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW

TH = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}


def _store(monkeypatch, **entries):
    """calibrate's stored entries: {name: entry dict} (none = nothing stored)."""
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: entries.get(name))


def _manual(monkeypatch, **vals):
    _store(monkeypatch, **{k: {"value": v, "n": 0, "source": "user"} for k, v in vals.items()})


@pytest.fixture(autouse=True)
def _nothing_stored(monkeypatch):
    _store(monkeypatch)


def _easy(n, iff=0.72, **kw):
    """n easy flat road runs (equivalence's easy speed selection) at intensity `iff`."""
    return [{"km": 8.0, "gain": 40.0, "minutes": 50.0, "hr": 140.0, "aet": 145.0, "if": iff, **kw} for _ in range(n)]


# ---- registered with today's constants as the defaults ---------------------------------------

def test_items_registered_with_todays_constants():
    reg = CAL._registry()
    e, s = reg[ICAL.EASY], reg[ICAL.SCALE]
    assert e.default == WS.EASY_F == 0.78 and e.min_n == 10 and e.bounds == (0.65, 0.85)
    assert s.default == 1.0 and s.min_n == 3 and s.bounds == (0.85, 1.15)
    # nothing stored: the engine reads exactly the old constants
    assert ICAL.easy_if() == WS.EASY_F and ICAL.rpe_scale() == 1.0
    c = WS.Ctx(cp=300.0)
    assert c.easy_frac() == WS.EASY_F and c.rpe_scale() == 1.0
    assert WS.resolve({"kind": "work", "target": {"type": "rpe", "lo": 3, "hi": 5}}, c).frac == \
        pytest.approx((WS.RPE_FRAC[3] + WS.RPE_FRAC[5]) / 2)


# ---- 驗收 1: below 10 easy runs / 3 per RPE value the default stands and says 「預設」 -------------

def test_easy_below_ten_runs_keeps_the_default():
    assert ICAL.fit_easy_rows(_easy(9)).n == 9 and ICAL.fit_easy_rows([]) is None
    item = CAL._registry()[ICAL.EASY]
    assert CAL.shrink(item, ICAL.fit_easy_rows(_easy(9))) is None        # nothing written
    d = CAL.describe(ICAL.EASY, None)
    assert d["source"] == "default" and d["value"] == 0.78 and d["chip"]["text"] == "預設（推估）"
    assert "滿 10 筆才會自己擬合" in d["chip"]["tip"]
    f = ICAL.fit_easy_rows(_easy(10))
    assert f is not None and f.n == 10 and f.value == pytest.approx(0.72)


def test_rpe_needs_three_runs_of_one_value():
    rows = [{"rpe": r, "if": 0.7} for r in (2, 2, 4, 4, 5, 5, 7, 7)]      # every value twice
    assert ICAL.fit_scale_rows(rows).n == 2 and ICAL.fit_scale_rows([]) is None
    assert CAL.shrink(CAL._registry()[ICAL.SCALE], ICAL.fit_scale_rows(rows)) is None
    d = CAL.describe(ICAL.SCALE, None)
    assert d["source"] == "default" and d["value"] == 1.0 and d["chip"]["text"] == "預設（推估）"
    # a third run of RPE 2 makes that value count, alone: the others stay out of n and the fit
    f = ICAL.fit_scale_rows(rows + [{"rpe": 2, "if": 0.7}])
    assert f.n == 3 and f.value == pytest.approx(min(0.7 / WS.RPE_FRAC[2], 1.15))


def test_run_writes_nothing_for_thin_data(monkeypatch):
    monkeypatch.setattr(ICAL, "easy_rows", lambda ds, today: _easy(4))
    monkeypatch.setattr(ICAL, "rated_rows", lambda ds, today: [{"rpe": 3, "if": 0.8}])
    reg = {n: CAL._registry()[n] for n in (ICAL.EASY, ICAL.SCALE)}
    monkeypatch.setattr(CAL, "REGISTRY", reg)
    monkeypatch.setattr(CAL, "_load_items", lambda: None)
    up, skipped = CAL.run(SimpleNamespace(), {}, dt.date(2026, 10, 14))
    assert up == {} and skipped == {ICAL.EASY: "n=4<10", ICAL.SCALE: "n=1<3"}


# ---- 驗收 2: shrunk toward today's value, inside the bounds -------------------------------------

def test_easy_shrinks_toward_0_78():
    item = CAL._registry()[ICAL.EASY]
    e = CAL.shrink(item, ICAL.fit_easy_rows(_easy(10, 0.70)))           # w = 10 / (10 + 10)
    assert e["w"] == 0.5 and e["value"] == pytest.approx(0.5 * 0.70 + 0.5 * 0.78) and e["personal"] == 0.70
    e = CAL.shrink(item, ICAL.fit_easy_rows(_easy(90, 0.70)))           # w = 0.9: closer to the runner
    assert e["value"] == pytest.approx(0.9 * 0.70 + 0.1 * 0.78)
    assert 0.70 < e["value"] < 0.78


def test_easy_never_leaves_its_bounds():
    item = CAL._registry()[ICAL.EASY]
    hi = ICAL.fit_easy_rows(_easy(400, 1.0))                             # an "easy" run at CP: clipped first
    assert hi.value == 0.85
    assert CAL.shrink(item, hi)["value"] <= 0.85
    lo = ICAL.fit_easy_rows(_easy(400, 0.40))
    assert lo.value == 0.65 and CAL.shrink(item, lo)["value"] >= 0.65


def test_scale_shrinks_toward_1_and_stays_in_bounds():
    item = CAL._registry()[ICAL.SCALE]
    rows = [{"rpe": 2, "if": 0.62 * 1.10}] * 5 + [{"rpe": 5, "if": 0.82 * 1.10}] * 5     # 10 % above the table
    f = ICAL.fit_scale_rows(rows)
    assert f.n == 10 and f.value == pytest.approx(1.10)
    e = CAL.shrink(item, f)
    assert e["w"] == 0.5 and e["value"] == pytest.approx(1.05)
    big = ICAL.fit_scale_rows([{"rpe": 3, "if": 1.4}] * 300)
    assert big.value == 1.15 and CAL.shrink(item, big)["value"] <= 1.15
    small = ICAL.fit_scale_rows([{"rpe": 8, "if": 0.4}] * 300)
    assert small.value == 0.85 and CAL.shrink(item, small)["value"] >= 0.85


def test_scale_is_the_run_weighted_mean_of_each_values_median():
    rows = ([{"rpe": 2, "if": x} for x in (0.60, 0.62, 0.70)]            # median 0.62 → ratio 1.0
            + [{"rpe": 7, "if": x} for x in (0.80, 0.85, 0.90, 0.85, 0.85, 0.85)])   # median 0.85 → 0.85 / 0.94
    f = ICAL.fit_scale_rows(rows)
    want = (3 * 1.0 + 6 * 0.85 / 0.94) / 9
    assert f.n == 9 and f.value == pytest.approx(want)


def test_the_selections():
    base = _easy(1)[0]
    assert ICAL.is_easy(base)
    assert not ICAL.is_easy({**base, "gain": 200.0})                       # 25 m/km: not flat
    assert not ICAL.is_easy({**base, "minutes": 15.0})                     # too short
    assert not ICAL.is_easy({**base, "hr": 149.0})                         # AeT 145 + 3 < 149
    assert ICAL.is_easy({**base, "hr": 148.0})
    assert not ICAL.is_easy({**base, "aet": None})                          # no AeT: can't tell easy
    assert not ICAL.is_easy({**base, "if": None})                           # no threshold: no IF
    assert not ICAL.is_easy({**base, "if": 3.0})                            # a mis-recorded IF
    assert ICAL.rpe_value(4.5) == 5 and ICAL.rpe_value(0) is None and ICAL.rpe_value(11) is None


def test_rated_rows_reads_runs_of_the_last_year(monkeypatch):
    from backend.engine import activity_tags as AT
    from backend.engine.wko5expr.dataset import date_to_day
    today = dt.date(2026, 10, 14)

    def w(day, sport="run", secs=3000.0, iff=0.8):
        return SimpleNamespace(day=date_to_day(day), sport=sport, entry=SimpleNamespace(start=day, file=str(day)),
                               metrics={"movingduration": secs, "if": iff})
    ws = [w(dt.date(2026, 10, 1)), w(dt.date(2025, 9, 1)), w(dt.date(2026, 9, 1), "hike"),
          w(dt.date(2026, 8, 1), secs=300.0), w(dt.date(2026, 7, 1))]
    rated = {str(x.entry.file): {"rpe": 4.0} for x in ws if x.entry.start != dt.date(2026, 7, 1)}
    monkeypatch.setattr(AT, "recorded_of", lambda rows, start, file=None: rated.get(file))
    rows = ICAL.rated_rows(SimpleNamespace(workouts=ws), today, recorded=[{"x": 1}])
    # the one rated run of the year, ≥ 10 min (a year-old run, a hike, a 5-min run, an unrated run: out)
    assert rows == [{"rpe": 4.0, "if": 0.8}]


# ---- what the values change ----------------------------------------------------------------------

def test_values_in_effect_reach_the_steps(monkeypatch):
    _store(monkeypatch, easy_run_if={"value": 0.70, "n": 30, "w": 0.75, "personal": 0.67, "source": "fitted"},
           rpe_if_scale={"value": 1.10, "n": 12, "w": 0.55, "personal": 1.18, "source": "fitted"})
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, v_easy=10.0, tpace=270.0)
    assert c.easy_frac() == 0.70 and c.rpe_scale() == 1.10
    v, how = WS.speed_kmh(0.70, c)                       # the easy speed sits at the runner's own % CP
    assert how == "easy+tpace" and v == pytest.approx(10.0)
    assert WS.speed_kmh(None, c)[0] == pytest.approx(10.0)
    assert WS.speed_kmh(1.0, c)[0] == pytest.approx(3600 / 270)
    r = WS.resolve({"kind": "work", "target": {"type": "rpe", "lo": 3, "hi": 3}}, c)
    assert r.frac == pytest.approx(WS.RPE_FRAC[3] * 1.10)
    # Ctx values given win over the stored ones (the API / tests pin them)
    c2 = WS.Ctx(cp=300.0, easy_f=0.78, rpe_k=1.0)
    assert c2.easy_frac() == 0.78 and c2.rpe_scale() == 1.0


def test_the_plan_tss_follows_the_rpe_scale(monkeypatch):
    doc = WS.normalize({"v": 1, "origin": "user", "items": [
        {"id": "a", "kind": "work", "dur": {"type": "time", "value": 3600},
         "target": {"type": "rpe", "lo": 5, "hi": 6}}]})
    base = WS.totals(doc, WS.Ctx(cp=300.0, lthr=170.0, aet=150.0))["tss"]
    f = (WS.RPE_FRAC[5] + WS.RPE_FRAC[6]) / 2
    assert base == pytest.approx(f * f * 100, abs=0.1)
    _manual(monkeypatch, rpe_if_scale=0.9)
    assert WS.totals(doc, WS.Ctx(cp=300.0, lthr=170.0, aet=150.0))["tss"] == pytest.approx((0.9 * f) ** 2 * 100, abs=0.1)


# ---- 驗收 3: the settings list shows the value, where it comes from and on how many ----------------

def test_settings_list_shows_value_source_and_n(monkeypatch):
    from backend.i18n import use_locale
    fitted = {"value": 0.74, "se": 0.01, "n": 24, "personal": 0.71, "w": 0.7059, "source": "fitted",
              "fitted_at": "2026-10-14"}
    d = CAL.describe(ICAL.EASY, fitted)
    assert d["value"] == 0.74 and d["source"] == "fitted" and d["n"] == 24 and d["chip"]["text"] == "本人 n=24"
    assert "本人 24 筆資料擬合 0.71" in d["chip"]["tip"] and "預設 0.78" in d["chip"]["tip"]
    d = CAL.describe(ICAL.SCALE, {"value": 1.05, "n": 0, "source": "user"})
    assert d["chip"]["text"] == "手動" and d["value"] == 1.05
    with use_locale("en"):
        d = CAL.describe(ICAL.EASY, None)
        assert d["label"] == "Easy-run intensity (× CP)" and d["chip"]["text"] == "Default (estimate)"
        assert "never a workout already sent to the watch" in d["chip"]["tip"]
        s = CAL.describe(ICAL.SCALE, None)
        assert s["label"] == "RPE step intensity factor" and "estimate" in s["default_src"].lower()


def test_calib_api_lists_both_items():
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.api import calib as API
    from backend.db.models import Base

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            items = {x["name"]: x for x in (await API.list_calibration(db=s))["items"]}
            r = await API.set_manual(ICAL.SCALE, API.Manual(value=1.1), db=s)
            from fastapi import HTTPException
            with pytest.raises(HTTPException):                       # outside 0.85–1.15
                await API.set_manual(ICAL.SCALE, API.Manual(value=1.3), db=s)
        await eng.dispose()
        return items, r
    items, r = asyncio.new_event_loop().run_until_complete(go())
    e, s = items[ICAL.EASY], items[ICAL.SCALE]
    assert (e["value"], e["source"], e["n"], e["chip"]["text"]) == (0.78, "default", 0, "預設（推估）")
    assert (s["value"], s["source"], s["n"], s["chip"]["text"]) == (1.0, "default", 0, "預設（推估）")
    assert r["source"] == "user" and r["value"] == 1.1


# ---- 驗收 4: a workout already pushed is not pushed again -----------------------------------------

def _session():
    return {"id": "s1", "key": "k1", "day": "2026-11-02", "kind": "mountain", "title": "技術地形", "minutes": 90,
            "steps": {"v": 1, "origin": "user", "items": [
                {"id": "w", "kind": "warm", "dur": {"type": "time", "value": 600},
                 "target": {"type": "auto", "intent": "easy"}},
                {"id": "d", "kind": "work", "dur": {"type": "distance", "value": 3000},
                 "target": {"type": "auto", "intent": "easy"}},
                {"id": "r", "kind": "work", "dur": {"type": "time", "value": 1800},
                 "target": {"type": "rpe", "lo": 3, "hi": 5, "up": 300}},
                {"id": "L", "kind": "work", "dur": {"type": "load", "value": 40},
                 "target": {"type": "rpe", "lo": 5, "hi": 6, "down": 300}},
                {"id": "c", "kind": "cool", "dur": {"type": "time", "value": 300},
                 "target": {"type": "none"}}]}}


@pytest.mark.parametrize("native_load", [6, None])
def test_a_refit_never_repushes(monkeypatch, native_load):
    """The plan's TSS moves with the calibration; the payload (targets, durations, the 「負荷」 step's
    TL and estimated time) does not — the stored fingerprint still says 「已推送」."""
    from backend.db.models import CorosPlanPush
    monkeypatch.setattr(CW, "COROS_TARGET_TYPE_LOAD", native_load)
    s = _session()
    before = CW.session_workout(s, TH, "2026-10-14")
    c0 = WS.session_ctx(s, TH)
    tss0 = WS.totals(WS.normalize(s["steps"]), c0)["tss"]
    row = CorosPlanPush(athlete_id=1, session_key="k1", fingerprint=before.fingerprint, status="pushed",
                        program_id="p1", id_in_plan="1", day="2026-11-02")

    _store(monkeypatch, easy_run_if={"value": 0.70, "n": 40, "w": 0.8, "personal": 0.68, "source": "fitted"},
           rpe_if_scale={"value": 1.15, "n": 30, "w": 0.75, "personal": 1.2, "source": "fitted"})
    after = CW.session_workout(s, TH, "2026-10-14")
    tot1 = WS.totals(WS.normalize(s["steps"]), WS.session_ctx(s, TH))
    assert tot1["tss"] != pytest.approx(tss0, abs=0.5)                       # the plan's TSS did move
    assert after.payload == before.payload and after.fingerprint == before.fingerprint
    assert after.load_steps == before.load_steps                             # the TL record too
    assert CW.status_of({**s}, TH, row, "2026-10-14")["status"] == "pushed"


def test_load_step_on_rpe_is_timed_at_the_table(monkeypatch):
    st = {"id": "L", "kind": "work", "dur": {"type": "load", "value": 40}, "target": {"type": "rpe", "lo": 5, "hi": 6}}
    f = (WS.RPE_FRAC[5] + WS.RPE_FRAC[6]) / 2
    _manual(monkeypatch, rpe_if_scale=1.15)
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0)
    r = WS.resolve(st, c)
    assert r.frac == pytest.approx(f * 1.15)                 # the chart shows the runner's own intensity
    assert WS.load_if(st, r) == pytest.approx(f)             # the watch's time / TL: the table as is
    secs, est = WS._secs(st, r, c)
    assert est and secs == pytest.approx(40 * 3600 / (f * f * 100))
    assert math.isclose(WS.totals({"items": [st]}, c)["tss"], 40.0, abs_tol=0.1)   # a typed load stays typed


def test_static_demo_exports_the_values_in_effect(monkeypatch):
    from backend.demo import static_steps as SS
    _manual(monkeypatch, easy_run_if=0.72, rpe_if_scale=1.1)
    ws = SS.constants()["ws"]
    assert ws["easy_f"] == 0.72
    assert ws["rpe"]["frac"]["4"] == pytest.approx(WS.RPE_FRAC[4] * 1.1)
    assert ws["rpe"]["frac_default"]["4"] == WS.RPE_FRAC[4]
    assert np.isclose(ws["rpe"]["frac"]["10"] / ws["rpe"]["frac_default"]["10"], 1.1)


def test_easy_rows_on_a_dataset():
    """The Dataset side: road runs of the last 26 weeks with their avg HR, the day's AeT and IF."""
    from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

    class DS(FakeDataset):
        def aethr(self, w):
            return 145.0

    def run(day, hr, iff, km=8.0, climb=40.0, tags=()):
        n = 3000
        return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=list(tags),
                           metrics={"distance": km, "climbing": climb, "duration": n, "movingduration": n, "if": iff},
                           channels={"elapsedtime": list(range(n)), "heartrate": [hr] * n})
    today = dt.date(2026, 10, 14)
    ws = [run(today - dt.timedelta(days=3 * i + 1), 140, 0.70 + 0.002 * i) for i in range(11)]
    ws += [run(today - dt.timedelta(days=2), 160, 0.90),                     # too hard for easy
           run(today - dt.timedelta(days=400), 140, 0.60)]                  # older than 26 weeks
    rows = ICAL.easy_rows(DS(ws, today), today)
    assert len(rows) == 12 and all(r["aet"] == 145.0 for r in rows)
    f = ICAL.fit_easy_rows(rows)
    assert f.n == 11 and f.value == pytest.approx(0.71)
