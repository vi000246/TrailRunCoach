"""SP-64: is the LTHR / max HR believable, which one is wrong, which test to suggest
(engine/threshold_confidence.py). Synthetic data only."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import threshold_confidence as TC

TODAY = dt.date(2026, 10, 4)


def run(date="2026-09-20", **kw):
    r = {"date": date, "s60": None, "s120": None, "hr20": None, "hr60": None, "avg_hr": None, "dur_s": 3600.0,
         "cp_band": [], "race": False, "temp": "cool"}
    r.update(kw)
    return r


def lt(value=160.0, kind="test", date="2026-09-01", cp=None):
    return {"value": value, "source_kind": kind, "date": date, "cp_at_date": cp, "label": "x"}


# ---------------------------------------------------------------------------
# per-run HR cleaning
# ---------------------------------------------------------------------------

def test_clean_hr_drops_spikes_and_held_peak_needs_the_hold():
    t = np.arange(0, 600.0)
    hr = np.full(600, 170.0)
    hr[300:304] = 220.0                       # a 4-s optical spike: +50 bpm in a second
    g, y = TC.clean_hr(t, hr)
    assert np.isnan(y[300:304]).all() and y[299] == 170.0
    assert TC.held_peak(y, 5) == 170.0       # the spike can't count
    # a slow, real rise is kept
    hr2 = np.concatenate([np.linspace(150, 185, 300), np.full(300, 185.0)])
    assert TC.held_peak(TC.clean_hr(t, hr2)[1], 120) == pytest.approx(185.0)


def test_clean_hr_drops_a_rise_spread_over_three_seconds():
    t = np.arange(0, 200.0)
    hr = np.full(200, 160.0)
    hr[100:103] = [166, 172, 178]             # +18 in 3 s, then stuck high
    hr[103:120] = 200.0
    y = TC.clean_hr(t, hr)[1]
    assert np.isnan(y[102:120]).all()
    # a jump that stays (a dropout before it, a lap start): a level change, kept
    hr2 = np.concatenate([np.full(100, 130.0), np.full(100, 165.0)])
    assert np.isfinite(TC.clean_hr(t, hr2)[1][100:]).all()


def test_cadence_lock_is_removed():
    t = np.arange(0, 400.0)
    cad = 170 + 3 * np.sin(t / 7.0)
    hr = np.full(400, 150.0)
    hr[100:300] = cad[100:300] + 0.5          # HR follows the steps
    y = TC.clean_hr(t, hr, cad)[1]
    assert np.isnan(y[130:270]).all() and y[10] == 150.0


def test_run_summary_peaks_means_and_cp_band():
    n = 70 * 60
    t = np.arange(n, dtype=float)
    hr = np.full(n, 150.0)
    hr[1200:2400] = 162.0
    p = np.full(n, 200.0)
    p[1200:2400] = 250.0                      # 20 min at 100 % of a 250 W CP
    s = TC.run_summary(t, hr, p, None, 250.0)
    assert s["s120"] == 162.0 and s["hr20"] == pytest.approx(162.0)
    assert s["hr60"] == pytest.approx((150 * 2400 + 162 * 1200) / 3600, abs=0.5)
    (b,) = s["cp_band"]
    assert b["dur_s"] >= 1150 and b["hr_med"] == 162.0


# ---------------------------------------------------------------------------
# LTHR signals
# ---------------------------------------------------------------------------

def test_source_not_a_test_is_low_confidence():
    s = TC.source_signals(lt(kind="estimate"))
    assert s[0]["level"] == "weak" and TC.confidence("estimate", s) == "low"
    assert TC.source_signals(lt(kind="default"))[0]["level"] == "strong"
    assert TC.source_signals(lt(kind="test")) == [] and TC.confidence("test", []) == "high"
    assert TC.confidence("manual", []) == "medium"
    assert TC.confidence("test", [TC.signal("x", "lthr", "hint", "")]) == "high"   # a hint never lowers it


def test_premise_fails_when_cp_moved_more_than_5_percent():
    assert TC.premise_signal(lt(kind="estimate", cp=200.0), 220.0)[0]["level"] == "strong"
    assert TC.premise_signal(lt(kind="estimate", cp=200.0), 208.0) == []
    assert TC.premise_signal(lt(kind="test", cp=200.0), 230.0) == []          # a test: 7 (event), not 2


def test_consistency_easy_cap_and_ratios():
    s = TC.consistency_signals(160.0, 202.0, 50.0, {"COROS 儲備心率": 162.0, "COROS 乳酸閾": 144.0})
    ids = {(x["id"], x["level"]) for x in s}
    assert ("easy_cap", "error") in ids                 # 162 ≥ 160
    assert ("pct_hrmax", "strong") in ids               # 79 % < 80 %
    assert ("pct_hrr", "strong") in ids                 # 72 % < 73 %
    w = TC.consistency_signals(160.0, 192.0, None, {})  # 83 %: inside 80–98, outside 85–95
    assert [(x["id"], x["level"], x["direction"]) for x in w] == [("pct_hrmax", "weak", "up")]
    assert TC.consistency_signals(160.0, 178.0, None, {}) == []                   # 90 %


def test_long_effort_above_lthr_strong_when_cool_hint_when_hot():
    s, _ = TC.effort_signals(155.0, [run(hr60=158.0)])
    assert (s[0]["id"], s[0]["level"], s[0]["direction"]) == ("long_effort", "strong", "up")
    s, _ = TC.effort_signals(155.0, [run(hr20=170.0, temp="hot")])                # 0.95 × 170 = 161.5
    assert (s[0]["level"], s[0]["evidence"]["kind"]) == ("hint", "hr20")
    s, _ = TC.effort_signals(155.0, [run(hr60=150.0, hr20=160.0)])                # 152 < 155
    assert s == []


def test_race_too_low_and_race_support():
    s, sup = TC.effort_signals(170.0, [run(race=True, dur_s=50 * 60, avg_hr=155.0)])
    assert s[0]["id"] == "race_low" and s[0]["direction"] == "down" and not sup
    s, sup = TC.effort_signals(160.0, [run(race=True, dur_s=50 * 60, avg_hr=158.0)])
    assert s == [] and sup["race"][0]["avg_hr"] == 158.0
    s, sup = TC.effort_signals(170.0, [run(race=True, dur_s=90 * 60, avg_hr=150.0)])   # not 40–60 min
    assert s == []


def test_cp_band_cross_check_needs_three_cool_stretches():
    band = [{"hr_med": 168.0}]
    rows = [run(cp_band=band), run(cp_band=band), run(cp_band=band, temp="hot")]
    assert TC.effort_signals(160.0, rows)[0] == []                                 # only 2 cool
    rows[2]["temp"] = "cool"
    s, _ = TC.effort_signals(160.0, rows)
    assert (s[0]["id"], s[0]["direction"]) == ("cp_band", "up")
    _s, sup = TC.effort_signals(165.0, rows)                                       # within 5 bpm
    assert _s == [] and sup["cp_band"]["n"] == 3


def test_events_and_age():
    brk = {"days": 35, "return": "2026-09-10"}
    cool = {"start": "2026-09-25"}
    s = TC.event_signals(lt(cp=200.0, date="2026-06-01"), TODAY, 215.0, brk, cool)
    assert {x["id"] for x in s} == {"break", "cool_season", "cp_change", "age"}
    assert next(x for x in s if x["id"] == "age")["level"] == "hint"
    # a break before the LTHR test is not an event
    assert TC.event_signals(lt(date="2026-09-20"), TODAY, None, brk, None) == []


# ---------------------------------------------------------------------------
# HRmax plausibility
# ---------------------------------------------------------------------------

def test_hrmax_far_above_the_sustained_peaks_is_flagged_with_a_candidate():
    runs = [run(s120=189.0, s60=192.0), run(s120=187.0, s60=190.0), run(s120=182.0, s60=185.0)]
    s, cand = TC.hrmax_check({"value": 202.0, "source_kind": "watch"}, runs)
    assert {(x["id"], x["level"]) for x in s} == {("source", "weak"), ("sustained", "strong")}
    assert cand["value"] == 189 and cand["s60"] == 192 and cand["hold_s"] == 120
    s, _ = TC.hrmax_check({"value": 195.0, "source_kind": "manual"}, runs)        # 6 bpm: fine
    assert s == []


def test_hrmax_below_a_60s_hold_is_too_low_and_a_lone_top_run_is_dropped():
    runs = [run(s120=186.0, s60=192.0), run(s120=184.0, s60=190.0), run(s120=180.0, s60=186.0)]
    s, cand = TC.hrmax_check({"value": 185.0, "source_kind": "manual"}, runs)
    assert s[0]["direction"] == "up" and cand["value"] == 192
    lone = [run(s120=205.0, s60=206.0), run(s120=189.0, s60=191.0), run(s120=187.0, s60=189.0)]
    s, cand = TC.hrmax_check({"value": 202.0, "source_kind": "test"}, lone)
    assert cand["value"] == 189                                                    # 205 is 16 above the next
    assert TC.hrmax_check({"value": 202.0}, lone[:2]) == ([], None)                # < 3 runs


def test_rhr_check():
    assert TC.rhr_check({"value": 20.0})[0]["level"] == "strong"
    assert TC.rhr_check({"value": 50.0, "source_kind": "watch"})[0]["level"] == "hint"


# ---------------------------------------------------------------------------
# diagnosis
# ---------------------------------------------------------------------------

def _s(sid, target, level="strong", d=None):
    return TC.signal(sid, target, level, sid, d)


@pytest.mark.parametrize("lsig,pair,hsig,support,src,want,tests", [
    ([], [_s("pct_hrmax", "pair")], [_s("sustained", "hrmax")], {"race": [1]}, "watch", "hrmax", ["hrmax"]),
    ([_s("long_effort", "lthr", d="up")], [], [], {}, "test", "lthr", ["tt30"]),
    ([_s("long_effort", "lthr")], [], [_s("sustained", "hrmax")], {}, "watch", "both", ["hrmax", "tt30"]),
    ([], [_s("pct_hrmax", "pair")], [], {}, "manual", "undetermined", ["hrmax", "tt30"]),
    ([], [_s("pct_hrmax", "pair")], [], {"cp_band": {"n": 3, "median": 160}}, "watch", "hrmax", ["hrmax"]),
    ([], [_s("pct_hrmax", "pair")], [], {}, "test", "lthr", ["tt30"]),
    ([], [], [], {}, "watch", None, []),
    ([_s("source", "lthr", "weak")], [], [], {}, "watch", None, []),               # a source is no evidence
])
def test_diagnosis_branches(lsig, pair, hsig, support, src, want, tests):
    d = TC.diagnose(lsig, pair, hsig, support, src)
    assert d["outlier"] == want and d["tests"] == tests


# ---------------------------------------------------------------------------
# the whole check: the user's case, suggestions, conditions, warnings
# ---------------------------------------------------------------------------

def users_case(**kw):
    runs = [run(s120=189.0, s60=192.0, hr60=156.0), run(s120=187.0, s60=190.0),
            run(s120=182.0, s60=185.0, race=True, dur_s=55 * 60, avg_hr=156.0)]
    args = dict(lthr=lt(kind="estimate", date="2026-09-01", cp=250.0), mhr={"value": 202.0, "source_kind": "watch"},
                rhr={"value": 50.0, "source_kind": "watch"}, today=TODAY, runs=runs,
                easy_caps={"COROS 儲備心率": 162.0}, cp_now=252.0, hr_model="hrr")
    args.update(kw)
    return TC.assess(**args)


def test_the_example_runner_hrmax_is_the_outlier():
    r = users_case()
    assert r["diagnosis"]["outlier"] == "hrmax" and r["diagnosis"]["support"]["race"]
    assert r["hrmax"]["confidence"] == "low" and r["hrmax"]["candidate"]["value"] == 189
    assert r["lthr"]["confidence"] == "low"                       # an estimate: low from the start
    # the consistency errors count against the HRmax, not the LTHR
    assert any(s["id"] == "easy_cap" for s in r["hrmax"]["signals"])
    assert not any(s["id"] == "easy_cap" for s in r["lthr"]["signals"])
    (sg,) = r["suggestions"]
    assert sg["tests"] == ["hrmax"] and sg["id"] == "thr_check:hrmax" and "priority" not in sg
    assert sg["title"] == "建議：做最大心率測試"
    assert sg["links"][0]["href"].endswith("add=lib%3Amaxhr_hill")
    assert r["warn"]["hrmax"]["low"] and r["warn"]["lthr"]["low"]


def test_low_source_alone_is_a_low_priority_lthr_suggestion():
    runs = [run(s120=178.0, s60=180.0), run(s120=176.0, s60=178.0), run(s120=175.0, s60=177.0)]
    r = TC.assess(lt(kind="estimate", cp=250.0), {"value": 182.0, "source_kind": "manual"}, {"value": None},
                  TODAY, runs, {}, 250.0)
    (sg,) = r["suggestions"]
    assert sg["tests"] == ["tt30"] and sg["priority"] == "low"
    assert not r["warn"]["hrmax"]["low"]                         # medium, and the LTHR model anyway


def test_tested_since_removes_the_suggestion_and_a_clean_test_is_quiet():
    r = users_case(tested={"hrmax"})
    assert r["suggestions"] == []
    runs = [run(s120=185.0, s60=188.0), run(s120=184.0, s60=186.0), run(s120=180.0, s60=183.0)]
    r = TC.assess(lt(168.0, kind="test", date="2026-09-20"), {"value": 190.0, "source_kind": "test"},
                  {"value": 50.0, "source_kind": "manual"}, TODAY, runs, {"COROS 乳酸閾": 151.0}, None)
    assert r["suggestions"] == [] and r["lthr"]["confidence"] == "high" and r["hrmax"]["confidence"] == "high"
    assert TC.warn_of(r) is None


def test_lthr_test_conditions_heat_taper_and_48h():
    c = TC.test_conditions("tt30", True, "base", None, TODAY)
    assert c["wait_cool"] and any("等天氣轉涼" in n for n in c["notes"]) and any("48" in n for n in c["notes"])
    c = TC.test_conditions("tt30", False, "taper", 6, TODAY)
    assert c["earliest"] == "2026-10-11" and any("減量期" in n for n in c["notes"]) and not c["wait_cool"]
    assert not TC.test_conditions("hrmax", True, "base", None, TODAY)["wait_cool"]   # heat: LTHR test only
    # the suggestion carries them: hot → still schedulable (a link), with the note
    runs = [run(hr60=165.0, s120=180.0, s60=182.0), run(s120=178.0, s60=180.0), run(s120=176.0, s60=178.0)]
    r = TC.assess(lt(kind="test", date="2026-09-20"), {"value": 186.0, "source_kind": "test"}, {"value": None},
                  TODAY, runs, {}, None, hot=True, kind="taper", days_to_a=6)
    (sg,) = r["suggestions"]
    assert sg["tests"] == ["tt30"] and sg["wait_cool"] and sg["earliest"] == "2026-10-11"
    assert sg["title"] == "建議：做 LTHR 測試（30 分鐘獨跑）"
    assert sg["links"][0]["href"].endswith("&day=2026-10-11") and "proto=race" in sg["links"][0]["href"]


def test_recent_hot():
    rows = [run(date="2026-09-30", temp="hot"), run(date="2026-10-01", temp="hot"), run(date="2026-10-02")]
    assert TC.recent_hot(rows, TODAY) is True
    assert TC.recent_hot([run(date="2026-08-01", temp="hot")], TODAY) is None


# ---------------------------------------------------------------------------
# test results: candidates, never applied by themselves
# ---------------------------------------------------------------------------

def test_tt30_result_is_the_last_20_minutes_and_power_vs_cp():
    warm, tt, cool = 900, 1800, 600
    n = warm + tt + cool
    t = np.arange(n, dtype=float)
    hr = np.concatenate([np.full(warm, 130.0), np.full(600, 158.0), np.full(1200, 166.0), np.full(cool, 140.0)])
    p = np.concatenate([np.full(warm, 180.0), np.full(tt, 262.0), np.full(cool, 160.0)])
    r = TC.tt30_result(t, hr, p, None, 250.0)
    assert r["lthr"] == 166 and r["power"] == 262 and r["vs_cp"] == pytest.approx(0.048)
    assert TC.tt30_result(t[:1500], hr[:1500]) is None                            # < 30 min
    body = TC.result_apply("tt30", r, "2026-10-01")
    assert body["lthr"] == 166 and body["lthr_method"] == "friel30" and body["date"] == "2026-10-01"


def test_hrmax_result_filters_spikes():
    t = np.arange(0, 900.0)
    hr = np.concatenate([np.linspace(120, 186, 840), np.full(60, 186.0)])
    hr[500:503] = 230.0
    r = TC.hrmax_result(t, hr)
    assert r["value"] == 186 and r["s10"] == 186
    body = TC.result_apply("hrmax", r, "2026-10-01")
    assert body == {**body, "mhr": 186, "mhr_method": "test"}


def test_test_kind_from_titles():
    assert TC.test_kind(["Friel 30′ 閾值心率測試"]) == "tt30"
    assert TC.test_kind(["", "最大心率測試：3 趟上坡、最後一趟全力"]) == "hrmax"
    assert TC.test_kind(["LTHR test"]) == "tt30" and TC.test_kind(["輕鬆跑"]) is None


# ---------------------------------------------------------------------------
# wiring: the box rows, the template, the stored method
# ---------------------------------------------------------------------------

def test_zone_rows_carry_the_schedule_links():
    from backend.engine import suggestions as SG
    sg = users_case()["suggestions"][0]
    (row,) = SG.zone_rows({"suggestions": [sg]}, set(), lambda k, s: False, lambda k, e: [])
    assert row["id"] == "zone:thr_check:hrmax" and row["pick"] is None
    assert row["links"][0]["label"] == "安排課表：最大心率測試"
    # another detector's 30-min TT gets a link too (hr_shift / cool_season)
    other = {"id": "cool_season", "tests": ["tt30", "aet"], "title": "x", "conditions": [], "detected": "2026-10-01"}
    (row,) = SG.zone_rows({"suggestions": [other]}, set(), lambda k, s: False, lambda k, e: [])
    assert [x["test"] for x in row["links"]] == ["tt30"]


def test_maxhr_template_and_lthr_conditions():
    from backend.engine import workout_templates as WT
    by = {t.key: t for t in WT.TEMPLATES}
    m = WT.row(by["maxhr_hill"])
    assert m["key"] == "lib:maxhr_hill" and "胸帶" in m["note"] and "心血管" in m["note"] and "5 K" in m["note"]
    assert "< 25 °C" in WT.row(by["friel_lthr30"])["note"] and "48 小時" in WT.row(by["friel_lthr30"])["note"]


def test_max_hr_test_session_is_not_read_as_a_cp_test():
    from types import SimpleNamespace as NS

    from backend.engine import workout_review as WR
    w = NS(idx=7, day=46000.0, entry=NS(title=""))
    s = {"title": "最大心率測試：3 趟上坡、最後一趟全力", "state": "done", "kind": "test",
         "done_by": {"index": 7, "date": WR._wdate(w).isoformat()}}
    ds = NS(plan_test_sessions=[s])
    assert WR.scheduled_test(ds, w, {}) is None


def test_hr_profile_says_a_tested_max_hr():
    from types import SimpleNamespace as NS

    from backend.engine import hr_profile as HP
    from backend.engine import planning as PL
    ds = NS(plan=PL.Plan(thresholds=[PL.Threshold("2026-09-01", mhr=188.0, mhr_method="test")]))
    m = HP.max_hr(ds, TODAY, acc={}, use_account=False)
    assert m["kind"] == "manual" and m["method"] == "test" and m["source"] == "最大心率測試 2026-09-01"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import plan as API
    from backend.engine import planning as PL
    path = tmp_path / "plan.json"
    PL.Plan(thresholds=[PL.Threshold("2026-01-01", lthr=160.0, cp=220.0)]).save(path)
    load, save = PL.Plan.load.__func__, PL.Plan.save
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, p=path: load(cls, p)))
    monkeypatch.setattr(PL.Plan, "save", lambda self, p=path: save(self, p))
    monkeypatch.setattr(API, "_notify", lambda thresholds: None)
    app = FastAPI()
    app.include_router(API.router)
    return TestClient(app)


def test_apply_a_max_hr_test_records_the_method(client):
    body = TC.result_apply("hrmax", {"value": 188}, "2026-02-01")
    r = client.post("/api/v1/plan/thresholds/apply-estimate", json=body)
    assert r.status_code == 200
    th = r.json()["threshold"]
    assert (th["date"], th["mhr"], th["mhr_method"]) == ("2026-02-01", 188, "test")
    # the settings table PUTs the rows back: the method survives
    rows = client.get("/api/v1/plan/thresholds").json()["thresholds"]
    assert client.put("/api/v1/plan/thresholds", json=rows).status_code == 200
    assert [x.get("mhr_method") for x in client.get("/api/v1/plan/thresholds").json()["thresholds"]] == [None, "test"]
    assert client.post("/api/v1/plan/thresholds/apply-estimate", json={"mhr": 300}).status_code == 400
    assert client.post("/api/v1/plan/thresholds/apply-estimate",
                       json={"mhr": 190, "mhr_method": "guess"}).status_code == 400
    # an LTHR 30-min result: friel30 on the test day
    th = client.post("/api/v1/plan/thresholds/apply-estimate",
                     json=TC.result_apply("tt30", {"lthr": 164}, "2026-02-02")).json()["threshold"]
    assert (th["lthr"], th["lthr_method"]) == (164, "friel30")


def test_threshold_check_endpoint_reports_without_saving(client, monkeypatch):
    from backend.api import plan as API
    seen = {}
    monkeypatch.setattr(API, "_estimate_dataset", lambda: "DS")
    monkeypatch.setattr(TC, "check", lambda ds, plan, today, **kw: seen.update(ds=ds, **kw) or {"ok": True})
    monkeypatch.setattr("backend.engine.reentry.find", lambda ds, today: None)
    monkeypatch.setattr("backend.engine.zone_events.suggestions", lambda *a, **k: {"checks": {}})
    assert client.get("/api/v1/plan/threshold-check").json() == {"ok": True}
    assert seen["ds"] == "DS" and "days_to_a" in seen


def test_testing_indicator_carries_the_threshold_check(monkeypatch):
    from types import SimpleNamespace as NS

    from backend.engine import status as S
    from backend.engine import zone_events as ZE
    from backend.engine.planning import Plan, Threshold
    monkeypatch.setattr(ZE, "suggestions", lambda *a, **k: {"suggestions": [], "events": [], "checks": {}})
    chk = users_case()
    monkeypatch.setattr(TC, "check", lambda *a, **k: chk)
    st = S.Status.__new__(S.Status)
    st.ds = NS(workouts=[], plan=None)
    st.plan = Plan(thresholds=[Threshold("2026-09-01", cp=250, lthr=160, lthr_method="estimate")])
    st.today, st.goals, st.indicators, st.cp_protocol = TODAY, {"days_to_next_a": None}, [], "quick"
    ind = st.i_testing()
    assert ind.extra["test_suggestions"] == chk["suggestions"] and st.thr_check is chk
    assert ind.extra["thr_check"]["diagnosis"]["outlier"] == "hrmax"
    assert ind.level == S.WATCH and "最大心率測試" in ind.action and "最可能錯的是最大心率" in ind.why
    assert "最大心率 可信度低" in ind.why
