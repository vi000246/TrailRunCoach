"""
SP-468 (after SP-449): three things about the CP the 課表 uses.

1. **The CP changes only by ≥ 3 W** (the user 2026-10-13, SP-449 問答 Q2 第 1 題): the Stryd PD
   fit now reaches the last run, so every sync moved it a little and every power session on the
   watch was re-pushed. A new fit within 3 W of the CP in effect keeps the CP in effect
   (fitdataset.hold_cp; the CP in effect remembered in cp_hold.json next to the estimate memo).
2. **A CP that changed re-zones the stored sessions**: production after `35d9fd50` showed CP
   191.0 W while the stored easy runs still said 「功率 114–140 W」 (65–80 % × 175): only a
   threshold edit (PUT) or a sync with a new activity started plan_auto's run, and a CP that
   moved by itself (a deploy that refits, an activity excluded …) never did. Opening the 課表
   now compares the CP in effect with the one the last run saw (plan_auto.check_cp) and starts
   the cp_change run.
3. **A CP test is WKO5's PD mFTP, nothing else** (the user 2026-10-13: 「兩點算法…刪掉，還有待套用
   這個也刪掉，完全比照 WKO5 用自動計算的」): no two-point / single-bout / 0.95 × P20 / Riegel CP,
   no 「已偵測、待套用」, no 「套用這次的 CP」, no apply-cp endpoint. A CP test is an effort on the
   90-day curve; its card shows WKO5's mFTP after the test.

Synthetic data only — never the WKO5 folder, the real app DB or ~/.wko5coach.
"""
import datetime as dt
from datetime import date, datetime, timezone

import pytest
from pytest import approx

from backend.engine import cp_protocols as CPP
from backend.engine import planning as PL
from backend.engine.racepower import cptest as T
from backend.tests.test_cp_protocols import _act, _bouts, _ds, _sess, quick_parts, standard_parts

LTHR = 165.0
PD = {"mftp": 195.5, "frc": 5000.0, "date": "2026-09-30", "runs": 49}
D = dt.date


def _fit(day, cp):
    return {"date": day, "cp": cp, "frc_j": 15000.0, "runs": 49, "n_points": 40}


# ---------------------------------------------------------------------------
# 1. the CP in effect changes only by ≥ 3 W
# ---------------------------------------------------------------------------

def _on(eff, day):
    """The fit in effect on `day` (FitFolderDataset._cp_fit_on: the last one on or before it)."""
    hit = None
    for r in eff:
        if r["date"] <= day:
            hit = r
    return hit


def test_hold_keeps_the_cp_in_effect_within_3_w():
    from backend.engine.wko5expr import fitdataset as FD
    assert FD.CP_HOLD_W == 3.0
    raw = [_fit(D(2026, 9, 13), 175.4), _fit(D(2026, 10, 8), 191.0)]
    eff, held = FD.hold_cp(raw, None)                  # nothing remembered: the fits as they are
    assert [r["cp"] for r in eff] == [175.4, 191.0] and held["cp"] == 191.0 and held["date"] == D(2026, 10, 8)
    # the next sync: the last run is 10/10, its fit 192.5 (1.5 W away) — 191.0 (the 10/08 fit) stays
    raw2 = [_fit(D(2026, 9, 13), 175.4), _fit(D(2026, 10, 10), 192.5)]
    eff, held2 = FD.hold_cp(raw2, held)
    for day in (D(2026, 10, 8), D(2026, 10, 10), D(2026, 10, 20)):
        assert _on(eff, day)["cp"] == 191.0 and _on(eff, day)["date"] == D(2026, 10, 8)
    assert _on(eff, D(2026, 10, 7))["cp"] == 175.4                   # the history before it is not touched
    assert held2["cp"] == 191.0 and held2["date"] == D(2026, 10, 8)
    # a 30-day grid day crossed (10/13 fit 190.2, the last run 10/14 189.9): still 191.0
    raw3 = raw2[:1] + [_fit(D(2026, 10, 13), 190.2), _fit(D(2026, 10, 14), 189.9)]
    eff, held3 = FD.hold_cp(raw3, held2)
    assert [_on(eff, d)["cp"] for d in (D(2026, 10, 8), D(2026, 10, 13), D(2026, 10, 14))] == [191.0] * 3
    assert held3 == held2
    # 3 W or more, up or down: the new fit
    eff, held4 = FD.hold_cp(raw2[:1] + [_fit(D(2026, 10, 12), 194.0)], held)
    assert _on(eff, D(2026, 10, 12))["cp"] == 194.0 and _on(eff, D(2026, 10, 11))["cp"] == 191.0
    assert held4["cp"] == 194.0 and held4["date"] == D(2026, 10, 12)
    eff, _ = FD.hold_cp(raw2[:1] + [_fit(D(2026, 10, 12), 188.0)], held)
    assert _on(eff, D(2026, 10, 12))["cp"] == 188.0
    eff, _ = FD.hold_cp(raw2[:1] + [_fit(D(2026, 10, 12), 188.1)], held)
    assert _on(eff, D(2026, 10, 12))["cp"] == 191.0                  # 2.9 W: held
    # a remembered CP after the last fit (runs deleted since): not used
    eff, held5 = FD.hold_cp(raw[:1], held)
    assert [r["cp"] for r in eff] == [175.4] and held5["cp"] == 175.4


def _fit_ds(tmp_path, last: dt.date):
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    utc = timezone.utc
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    day, i = D(2026, 8, 1), 0
    while day <= last:
        p = d / f"{day.isoformat()}.fit"
        if not p.exists():
            p.write_bytes(build_run(start=datetime(day.year, day.month, day.day, 0, tzinfo=utc), seconds=1800,
                                    power=200, stryd=True))
        day += dt.timedelta(days=4 if day < D(2026, 10, 8) else 2)
        i += 1
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=D(2026, 10, 13),
                          estimate_thresholds=False, tz=utc)
    ds.plan = PL.Plan()
    return ds


def test_the_dataset_keeps_its_cp_across_syncs(tmp_path, monkeypatch):
    """Production 2026-10-13: CP 191.0 W (PD model 10/08). A run on 10/10 refits to 192.5 W: the
    課表 keeps 191.0 W and says the fit is the 10/08 one; the remembered CP is on disk."""
    from backend.engine.racepower import athlete as A
    fits = {D(2026, 10, 8): 191.0, D(2026, 10, 10): 192.5}
    monkeypatch.setattr(A, "pd_model", lambda ds_, day, runs, ref, any_power=False:
                        {"mftp": fits.get(day, 175.4), "frc": 5000.0, "n_points": 40})
    ds = _fit_ds(tmp_path, D(2026, 10, 8))
    ds._estimate_cp(D(2026, 8, 14), D(2026, 10, 8))
    ds._hold_cp_est()
    assert ds.cp_info(ds.workouts[-1])["cp"] == 191.0
    assert (ds._store.home / "cp_hold.json").exists()
    ds2 = _fit_ds(tmp_path, D(2026, 10, 10))
    assert ds2.workouts[-1].entry.start.date() == D(2026, 10, 10)
    ds2._estimate_cp(D(2026, 8, 14), D(2026, 10, 10))
    assert ds2._cp_est[-1]["cp"] == 192.5                             # the raw fit
    ds2._hold_cp_est()
    i = ds2.cp_info(ds2.workouts[-1])
    assert i["cp"] == 191.0 and i["date"] == "2026-10-08" and "PD 模型 2026-10-08" in i["source"]
    assert ds2.cp(ds2.workouts[-1]) == 191.0


def test_the_estimate_memo_keeps_the_raw_fits_and_holds_on_every_read(tmp_path, monkeypatch):
    import json
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    ds = _fit_ds(tmp_path, D(2026, 10, 8))
    (ds._store.home).mkdir(parents=True, exist_ok=True)
    (ds._store.home / "cp_hold.json").write_text(json.dumps(
        {"date": "2026-10-08", "cp": 191.0, "frc_j": 15000.0, "runs": 49, "n_points": 40}), "utf-8")
    raw = [_fit(D(2026, 9, 13), 175.4), _fit(D(2026, 10, 10), 192.5)]

    def fake(self):
        self._cp_est = [dict(r) for r in raw]
        return True
    monkeypatch.setattr(FitFolderDataset, "_estimate_settings", fake)
    ds._estimate_settings_memo()                                       # computed
    assert ds._cp_est[-1]["cp"] == 191.0
    memo = json.loads((ds._store.home / "estimate.json").read_text("utf-8"))
    assert [r["cp"] for r in next(iter(memo.values()))["cp_est"]] == [175.4, 192.5]   # raw on disk
    monkeypatch.setattr(FitFolderDataset, "_estimate_settings", lambda self: pytest.fail("memo hit expected"))
    ds._cp_est = []
    ds._estimate_settings_memo()                                       # read from the memo
    assert ds._cp_est[-1]["cp"] == 191.0


# ---------------------------------------------------------------------------
# 2. a CP that moved by itself re-zones the stored sessions
# ---------------------------------------------------------------------------

def _inputs(cp, easy_target):
    from backend.tests.test_plan_auto import base_inputs
    inp = base_inputs()
    for s in inp["cur"]["sessions"]:
        if s["id"] == "easy1":
            s["target"] = easy_target
    inp["thresholds"]["cp"] = cp
    inp["cur"]["thresholds"]["cp"] = cp
    return inp


def test_a_cp_that_moved_without_a_run_starts_the_cp_change_run(monkeypatch):
    from backend.engine import plan_auto as PA
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import make_db, run
    from backend.tests.test_plan_auto import Box, Push
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    b = Box(monkeypatch, _inputs(175.4, "功率 114–140 W"), Push())
    seen = []
    monkeypatch.setattr(PA, "after_cp_moved", lambda: seen.append(1))

    async def go():
        db = await make_db()
        assert (await PA.run(db))["status"] == "applied"
        assert (await PA.settings(db))["state"]["cp"] == 175.4
        # same activities, the CP in effect moved 175.4 -> 191.0 (a refit after a deploy)
        b.inp = _inputs(191.0, "功率 124–153 W")
        assert await PA.check_cp(db, b.inp) and seen == [1]
        # a stale view (the previous inputs) never starts it; nor the same CP
        assert not await PA.check_cp(db, {**b.inp, "stale": {"reason": "sync"}})
        assert not await PA.check_cp(db, _inputs(175.4, "功率 114–140 W")) and seen == [1]
        await SettingsRepository(db).set("plan.auto.enabled", False)
        await db.commit()
        assert not await PA.check_cp(db, b.inp) and seen == [1]       # 自動調整 off: nothing
    run(go())


def test_the_cp_change_run_rezones_the_stored_easy_runs(monkeypatch):
    """The run check_cp starts: the stored easy run 「功率 114–140 W」 (65–80 % × 175) becomes the
    generator's 「功率 124–153 W」 (65–80 % × 191) with no new activity, and is re-pushed."""
    from backend.engine import plan_auto as PA
    from backend.engine import plan_store as PS
    from backend.tests.test_coros_workouts import make_db, run
    from backend.tests.test_plan_auto import Box
    from backend.tests.test_plan_auto_cp import ThPush
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    b = Box(monkeypatch, _inputs(175.4, "功率 114–140 W"), ThPush())

    async def go():
        db = await make_db()
        await PA.run(db)
        easy = next(s for s in await PS.load(db) if s.get("gen_key") and "easy1" in s["gen_key"]
                    and s["state"] == "active" and s["week_start"] == "2026-09-28")
        assert easy["target"] == "功率 114–140 W"
        n0 = len(b.push.calls)
        b.inp = _inputs(191.0, "功率 124–153 W")
        r = await PA.run(db, trigger="cp_change")
        assert r["cp_change"]["old"] == 175.4 and r["cp_change"]["new"] == 191.0
        got = next(s for s in await PS.load(db) if s["uid"] == easy["uid"])
        assert got["target"] == "功率 124–153 W"
        assert len(b.push.calls) == n0 + 1 and b.push.calls[-1]["cp"] == 191.0
        assert easy["uid"] in {s["id"] for s in b.push.calls[-1]["sessions"]}
    run(go())


def test_opening_the_schedule_checks_the_cp(monkeypatch):
    """GET /sessions (and the calendar, which goes through it) with fresh inputs asks check_cp."""
    from backend.api import plan_sessions as API
    from backend.engine import plan_auto as PA
    from backend.tests.test_coros_workouts import make_db, run
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    asked = []

    async def check(db, inp):
        asked.append(inp["thresholds"]["cp"])
        return False
    monkeypatch.setattr(PA, "check_cp", check)

    async def go():
        db = await make_db()
        inp = _inputs(191.0, "功率 124–153 W")
        await API._sessions_body(db, inp, None, None, 0.0)
        assert asked == [191.0]
        await API._sessions_body(db, {**inp, "stale": {"reason": "sync", "at": 0}}, None, None, 0.0)
        assert asked == [191.0]                                       # a stale view: not asked
    run(go())


# ---------------------------------------------------------------------------
# 3. a CP test is WKO5's PD mFTP: no two-point CP, nothing to apply
# ---------------------------------------------------------------------------

RACE = [(900, 150.0, 140), (1500, 245.0, 175), (600, 130.0, 130)]


@pytest.mark.parametrize("proto, parts", [
    ("standard", standard_parts(p12=250.0, p3=300.0)),                 # a valid two-point model: not used
    ("standard", standard_parts(p12=221.0, p3=217.0, hr3=146, hr12=171)),   # the 9/30 test
    ("quick", quick_parts(p20=240.0)),                                 # 0.95 × P20 = 228: not used
    ("race", RACE),                                                    # Riegel: not used
])
def test_every_protocol_takes_wko5s_mftp_of_the_test_day(proto, parts):
    r = CPP.result(_bouts(parts), proto, LTHR, pd=lambda: PD)
    assert r["method"] == "pd_model" and r["cp"] == approx(195.5) and r["wprime"] is None
    assert r["pd"] == PD and r["bouts"]
    assert "quality" not in r


def test_without_a_pd_fit_a_cp_test_has_no_cp():
    for proto, parts in (("standard", standard_parts(p12=250.0, p3=300.0)), ("quick", quick_parts())):
        for pd in (None, lambda: None):
            r = CPP.result(_bouts(parts), proto, LTHR, pd=pd)
            assert r["method"] == "pd_model" and r["cp"] is None
            assert any("算不出" in x for x in r["reasons"])


def test_the_two_point_formula_and_the_apply_payload_are_gone():
    for name in ("two_point", "apply_payload", "reference", "PD_NOTE"):
        assert not hasattr(CPP, name), name


def test_review_card_shows_the_mftp_and_offers_no_apply(monkeypatch):
    from backend.engine import workout_review as R
    monkeypatch.setattr(R, "test_pd_fit", lambda ds, day: PD)
    ds = _ds(_act(standard_parts(p12=250.0, p3=300.0)), sessions=[_sess(protocol="standard")])
    w = ds.workouts[0]
    card = R.review(ds, w, "cp_test")
    assert card["empty"] is None and "action" not in card
    text = " ".join(s["data"]["value"] for s in card["series"] if s["data"]["kind"] == "value")
    assert "模型閾值功率（Modelled threshold power）196 W" in text and "算法參考 WKO5" in text and "套用" not in text
    assert "W′" not in text.split("算法參考 WKO5")[0]                           # no two-point W′
    ev = R.cp_eval(ds, w, R.measure(ds, w), R.classify(ds, w))
    assert ev["cp"] == approx(195.5) and "apply" not in ev and "ref" not in ev


def test_apply_cp_endpoint_is_gone():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as API
    app = FastAPI()
    app.include_router(API.router)
    r = TestClient(app).post("/api/v1/plan/thresholds/apply-cp", json={"date": "2026-09-30", "cp": 196,
                                                                        "cp_method": "pd_model"})
    assert r.status_code in (404, 405)


def _laps(p3, hr3, p12, hr12):
    return [{"t": 900.0, "p": 150.0, "hr_max": 140}, {"t": 720.0, "p": p12, "hr_max": hr12},
            {"t": 1800.0, "p": 110.0, "hr_max": 125}, {"t": 180.0, "p": p3, "hr_max": hr3},
            {"t": 600.0, "p": 120.0, "hr_max": 125}]


def test_race_power_page_test_is_wko5s_mftp_and_not_a_suggestion_to_apply():
    t = T.detect(_laps(300.0, 170, 260.0, 172))                       # a valid two-point model
    e = T.estimate(t, 68.0, "male", pd=lambda: PD)
    assert e["method"] == "pd_model" and e["cp"] == approx(195.5) and e["w_prime"] == 5000.0
    assert "WKO5" in e["label"] and "套用" not in e["label"] + e["note"]
    e0 = T.estimate(t, 68.0, "male")
    assert e0["method"] == "pd_model" and e0["cp"] is None and "算不出" in e0["note"]


def test_testing_indicator_counts_the_test_and_offers_no_apply():
    from backend.engine import status as ST
    from backend.engine import workout_review as WR
    st = ST.Status.__new__(ST.Status)
    st.plan = PL.Plan(thresholds=[PL.Threshold("2026-09-01", lthr=165.0, aethr=150.0)])     # no CP row
    st.today, st.goals, st.ds, st.cp_protocol = D(2026, 10, 13), {"days_to_next_a": None}, None, "standard"
    ct = {"idx": 3, "date": "2026-09-30", "cp": 195.5, "cp_range": [195.5, 195.5], "cp_now": 191.0,
          "method": "pd_model", "method_label": CPP.METHOD_LABEL["pd_model"], "protocol": "standard",
          "protocol_label": "標準", "reasons": [], "pd": PD}
    orig = WR.latest_cp_test
    try:
        WR.latest_cp_test = lambda ds, today: ct
        ind = st.i_testing()
    finally:
        WR.latest_cp_test = orig
    assert "CP 沒測過" not in ind.why and "CP 13 天前" in ind.why
    assert ind.text != "要更新" and "套用" not in (ind.action or "") and "apply" not in ind.extra["cp_test"]
    assert "模型閾值功率（Modelled threshold power）196 W" in ind.why


def test_cp_tested_counts_a_cp_test_in_the_fit_window():
    from backend.engine import aet_test as AT
    today = D(2026, 10, 13)
    assert AT.cp_tested(PL.Plan(), today, cp_test_date="2026-09-30")
    assert not AT.cp_tested(PL.Plan(), today)
    assert not AT.cp_tested(PL.Plan(), today, cp_test_date="2026-07-01")       # out of the 90-day fit
    # a CP typed by hand in 設定 is the CP in effect: not a test, whatever ran before
    manual = PL.Plan(thresholds=[PL.Threshold("2026-10-01", cp=200.0, cp_manual=True)])
    assert not AT.cp_tested(manual, today, cp_test_date="2026-09-30")


def test_baseline_counts_a_test_that_has_an_mftp():
    from types import SimpleNamespace
    from backend.api import plan_sessions as API
    st = SimpleNamespace(indicators=[SimpleNamespace(id="testing", extra={"cp_test": {
        "date": "2026-09-30", "method": "pd_model", "protocol": "standard", "cp": 195.5}})])
    assert API._latest_tests(st)["cp"]["ok"]
    st.indicators[0].extra["cp_test"]["cp"] = None
    assert not API._latest_tests(st)["cp"]["ok"]
