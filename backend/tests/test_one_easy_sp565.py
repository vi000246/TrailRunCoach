"""SP-565 (owner 2026-10-14, SP-493 Q1-4 ③): one 「輕鬆」 for the 課表, the activity review and adapt.

Before, three: the 課表's cap = the Z2 top of the 課表心率區間 (儲備心率: HRR Z2); the review's easy =
avg HR ≤ AeT + 3 (AeT = 0.89 × LTHR without a plan AeT); adapt's 「輕鬆跑偏強」 without power =
avg HR > 94 % LTHR. Now all three read hr_profile.easy_cap (the Z2 top of the zones in effect, a
measured AeT when there is one) and a run is easy when its average HR ≤ the cap + 3 (is_easy).

The review reads the cap on read (measure() → easy_cap / over_cap_s from the cached hr_hist), so an
old activity is judged by today's cap (the one-time recompute, docs/research/lthr-low-confidence-
testing.md §9.10). Synthetic data only — no WKO5 folder, no real DB."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from backend.engine import adapt as A
from backend.engine import hr_profile as HP
from backend.engine import workout_review as WR
from backend.engine.planning import Plan, Threshold
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 14)
MHR, RHR, LTHR = 189.0, 53.0, 155.0           # the ticket's numbers (SP-493): hrr Z2 141–154
CAP = {"lthr": round(0.90 * LTHR), "hrr": round(RHR + 0.74 * (MHR - RHR)), "hrmax": round(0.60 * MHR)}
AET_TEST = 150.0


def _run(hr: float, day: dt.date = TODAY, minutes: int = 50) -> FakeWorkout:
    n = minutes * 60
    t = np.arange(n, dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": [float(hr)] * n, "speed": [10.0] * n,
          "elapseddistance": list(t * 10.0 / 3600.0)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": float(n), "movingduration": float(n), "distance": minutes / 6.0,
                                "climbing": 20.0, "tss": 40.0})


def _ds(runs, aet_test: bool = False) -> FakeDataset:
    rows = [Threshold("2026-09-01", mhr=MHR, rhr=RHR)]
    if aet_test:
        rows.append(Threshold("2026-09-20", aethr=AET_TEST, aethr_method="test"))
    ds = FakeDataset(runs, TODAY, settings={"runthr": LTHR})
    ds.plan = Plan(thresholds=rows)
    ds.aethr = lambda w: AET_TEST if aet_test else 0.89 * LTHR      # dataset.aethr: the plan's, else 0.89 × LTHR
    ds.cp = lambda w: None
    ds.settings_from = "app"
    ds.setting_label = lambda name, default="": "設定"
    return ds


@pytest.fixture
def model(monkeypatch):
    """Lock the 課表心率區間 (SP-493's setting) and keep the watch account out."""
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)

    def lock(m):
        monkeypatch.setattr(HP, "plan_model", lambda user_id=1: m)
    return lock


def _plan_cap(ds) -> float:
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.zones import training_targets
    tt = training_targets(ds, int(date_to_day(TODAY)))
    z2 = next(r for r in tt["rows"] if r["id"] == "z2")
    assert z2["hr"] == [None, tt["easy_cap"]["value"]]          # the 課表's easy row is that cap
    return tt["easy_cap"]["value"]


def _row(m: dict) -> dict:
    """api/plan_sessions._adapt_ctx's review row of a run without power."""
    return {k: m.get(k) for k in ("avg_hr", "aet", "lthr", "avg_power", "cp", "easy_cap")}


# ---------------------------------------------------------------------------
# 1. one cap: the 課表, the review, adapt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("zone_model", ["lthr", "hrr", "hrmax"])
@pytest.mark.parametrize("aet_test", [False, True])
def test_the_plan_the_review_and_adapt_read_one_cap(model, zone_model, aet_test):
    model(zone_model)
    ds = _ds([_run(140.0)], aet_test)
    want = AET_TEST if aet_test else CAP[zone_model]          # a measured AeT wins in every model
    assert _plan_cap(ds) == want                               # 課表 (zones.training_targets)
    m = WR.measure(ds, ds.workouts[0])
    assert m["easy_cap"]["value"] == want                      # 活動判讀 (workout_review.measure)
    assert HP.easy_cap_for(ds, TODAY, LTHR)["value"] == want   # adapt's row carries the same (_adapt_ctx)
    assert m["easy_cap"]["measured"] is aet_test


@pytest.mark.parametrize("zone_model", ["lthr", "hrr", "hrmax"])
def test_the_easy_line_is_the_cap_plus_3_in_the_review_and_adapt(model, zone_model):
    model(zone_model)
    cap = CAP[zone_model]
    ds = _ds([_run(cap + 3.0, TODAY - dt.timedelta(days=1)), _run(cap + 4.0)])
    at, over = ds.workouts
    for w, easy in ((at, True), (over, False)):
        m = WR.measure(ds, w)
        c = WR.classify(ds, w, m)
        assert c["stim"]["easy_hr"] is easy                     # the review's 「輕鬆」
        if easy:
            assert c["type"] == "easy" and not c["moderate"]
        else:                                                    # 中強度跑, or the Zone 3 time makes it 閾值
            assert c["moderate"] or c["type"] == "quality"
        why = A.overhard(None, _row(m))                         # adapt's 「輕鬆跑偏強」 (no power)
        assert (why is None) is easy
        if not easy:
            assert why == f"平均心率 {cap + 4:.0f} > 輕鬆跑上限 {cap}+3（沒有功率才看心率）"
        # the time-above share follows the same line (hr_hist on read)
        assert WR._over_aet(m) == (0.0 if easy else 1.0)


def test_under_hrr_a_run_inside_the_z2_is_easy_everywhere(model):
    # the ticket's case: 儲備心率 Z2 tops at 154, LTHR 155 (AeT 0.89 × 155 = 138). Before, a 150-bpm run
    # was 「輕鬆」 by the 課表, 中強度 by the review (> 138 + 3) and 偏強 by adapt (> 94 % × 155 = 146)
    model("hrr")
    ds = _ds([_run(150.0)])
    m = WR.measure(ds, ds.workouts[0])
    assert m["aet"] == pytest.approx(0.89 * LTHR) and m["avg_hr"] > m["aet"] + 3 and m["avg_hr"] > 0.94 * LTHR
    assert _plan_cap(ds) == 154 and HP.is_easy(m["avg_hr"], m["easy_cap"])
    c = WR.classify(ds, ds.workouts[0], m)
    assert c["stim"]["easy_hr"] and not c["moderate"] and c["type_label"] == "輕鬆跑"
    assert A.overhard(None, _row(m)) is None


def test_with_power_adapt_still_reads_power_only(model):
    model("hrr")
    ds = _ds([_run(170.0)])
    m = WR.measure(ds, ds.workouts[0])
    assert A.overhard(None, {**_row(m), "avg_power": 200.0, "cp": 300.0}) is None   # 67 % CP, HR not read


# ---------------------------------------------------------------------------
# 2. the helpers
# ---------------------------------------------------------------------------

def test_is_easy_and_over_easy():
    assert HP.is_easy(157, 154) and not HP.is_easy(157.1, 154)
    assert HP.is_easy(157, {"value": 154.0}) and HP.over_easy(158, {"value": 154.0})
    assert not HP.is_easy(None, 154) and not HP.is_easy(150, None)
    assert not HP.over_easy(None, 154) and not HP.over_easy(170, None)
    assert HP.EASY_TOL_BPM == WR.AET_MARGIN


def test_easy_cap_reads_plan_hr_zones():
    hrz = HP.plan_hr_zones(LTHR, None, False, MHR, RHR, "hrr")
    c = HP.easy_cap(hrz)
    assert c["value"] == hrz["easy"][1] == 154 and c["model"] == "hrr" and not c["measured"]
    assert "儲備心率" in c["source"]
    hrz = HP.plan_hr_zones(LTHR, AET_TEST, True, MHR, RHR, "hrr")
    assert HP.easy_cap(hrz)["value"] == AET_TEST and HP.easy_cap(hrz)["measured"]
    assert HP.easy_cap(None) is None


def test_without_zones_the_cap_is_the_reviews_aet(model):
    model("lthr")
    ds = FakeDataset([_run(140.0)], TODAY, settings={})       # no LTHR, no max HR: no zones
    ds.plan = Plan()
    assert HP.easy_cap_for(ds, TODAY, None, 142.0) == {"value": 142.0, "source": "AeT", "measured": False,
                                                       "model": None}
    assert HP.easy_cap_for(ds, TODAY, None, None) is None


def test_the_wko5_cross_check_keeps_friel(model):
    model("hrr")
    ds = _ds([_run(140.0)])
    ds.config = type("C", (), {"parity": True})()
    assert HP.easy_cap_for(ds, TODAY, LTHR, 0.89 * LTHR)["value"] == pytest.approx(0.89 * LTHR)


# ---------------------------------------------------------------------------
# 3. old activities: judged on read with today's cap (the one-time recompute)
# ---------------------------------------------------------------------------

def test_the_cap_is_read_on_read_not_frozen_with_the_activity(model):
    # measure() keeps the per-file numbers (hr_hist, avg_hr) and adds the cap on every read: a changed
    # 課表心率區間 re-judges every activity at once, with no stale mix of old and new
    model("lthr")
    ds = _ds([_run(150.0, TODAY - dt.timedelta(days=40))])
    w = ds.workouts[0]
    m = WR.measure(ds, w)
    assert m["easy_cap"]["value"] == CAP["lthr"] and not WR.classify(ds, w, m)["stim"]["easy_hr"]
    assert sum(m["hr_hist"].values()) == pytest.approx(m["hr_s"])
    model("hrr")
    m = WR.measure(ds, w)
    assert m["easy_cap"]["value"] == CAP["hrr"] and WR.classify(ds, w, m)["stim"]["easy_hr"]


def test_the_review_card_cache_keys_on_the_hr_settings():
    import inspect
    from backend.api import wko5views
    src = inspect.getsource(wko5views)
    assert '("zones", "targets", "activity", "periodzones", "review")' in src


def test_a_hand_made_row_without_the_cap_reads_its_aet():
    # callers that pass their own measure-like dict (no `easy_cap`): the AeT and over_aet_s, as before
    m = {"aet": 140.0, "over_aet_s": 600.0, "hr_s": 3000.0}
    assert WR._easy_view(m) == (140.0, 600.0) and WR._over_aet(m) == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# 4. the words, zh and en
# ---------------------------------------------------------------------------

def test_the_new_words_are_translated():
    from backend.i18n import translate
    for msgid in ("輕鬆跑上限+3 以下", "輕鬆跑上限+3 以上 {p}%",
                  "平均心率 {hr:.0f} > 輕鬆跑上限 {cap:.0f}+3（沒有功率才看心率）",
                  "心率超過輕鬆跑上限 {cap}+3 的時間佔 {pct:.0f}%（> 10%）：下次放慢",
                  "{name} {plain}，但平均心率 {hr:.0f} > 輕鬆跑上限 {cap}+3，不是輕鬆跑"):
        en = translate(msgid, "en")
        assert en != msgid and not any("一" <= ch <= "鿿" for ch in en), msgid
    assert "94" not in translate(A.SRC_OVER, "en") and "easy" in translate(A.SRC_OVER, "en").lower()
