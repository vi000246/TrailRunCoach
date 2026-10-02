"""
CP-test protocols (engine/cp_protocols.py; docs/research/cp-test-protocols.md §5):
the 課表偏好 preference, the session per protocol, detection (the plan's
done_by first, then the power pattern), the per-protocol analysis with its
quality checks (incl. the athlete's real 2026-09-30 test), the same-method
comparison, 「套用這次的 CP」, and the timing rules. Temp plans only — the
user's plan thresholds are never written.
"""
import datetime as dt
import gzip
import json
from pathlib import Path

import numpy as np
import pytest
from pytest import approx

from backend.engine import cp_protocols as CPP
from backend.engine import plan_prefs as PP
from backend.engine import plan_store as PS
from backend.engine import planning as PL
from backend.engine import workout_review as R
from backend.settings import repository as SR
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)
LTHR, CP_NOW = 165.0, 220.0
SETTINGS = {"runthr": LTHR, "runftp": CP_NOW}


# ---------------------------------------------------------------------------
# the preference and the session
# ---------------------------------------------------------------------------

def test_default_is_quick_and_the_protocol_does_not_activate_shaping():
    assert SR.DEFAULTS["plan.prefs.cp_test_protocol"] == "quick" == CPP.DEFAULT == PP.Prefs().cp_test_protocol
    assert SR.PREF_ENUMS["plan.prefs.cp_test_protocol"] == CPP.PROTOCOLS
    for p in CPP.PROTOCOLS:
        SR.validate("plan.prefs.cp_test_protocol", p)
        assert not PP.Prefs(cp_test_protocol=p).active          # only the test session changes
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.cp_test_protocol", "3min")
    p = PP.Prefs(cp_test_protocol="standard", runs=4)
    assert p.active and PP.from_settings(p.settings()) == p


@pytest.mark.parametrize("proto, title, minutes, tss", [
    ("quick", "CP 測試 20 分全力", 37, 45.0),
    ("standard", "CP 測試 12 分 + 3 分", 70, 65.0),
])
def test_session_per_protocol(proto, title, minutes, tss):
    s = CPP.session_for(proto)
    assert (s["kind"], s["title"], s["minutes"], s["tss"], s["protocol"]) == ("test", title, minutes, tss, proto)
    t = CPP.TABLE[proto]
    # minutes = warm-up + bouts + rest + cool-down, and the detail says so (COROS reads it)
    assert t["warm"] + sum(t["bouts"]) + t["rest"] + t["cool"] == minutes
    assert f"暖身 {t['warm']} 分" in s["detail"] and f"緩和 {t['cool']} 分" in s["detail"]
    assert CPP.protocol_of(s) == proto and CPP.protocol_of({"title": s["title"]}) == proto


def test_race_schedules_nothing_and_says_so():
    assert CPP.session_for("race") is None
    assert CPP.NOTE_RACE == "用 5–10 K 比賽或計時跑代替 CP 測試"
    assert CPP.session_for("nonsense")["protocol"] == "quick"                  # unknown -> the default


def test_legacy_title_is_read_as_standard_and_the_cap_note_by_protocol():
    assert CPP.protocol_of({"title": "CP 測試 3 分 + 12 分"}) == "standard"
    assert CPP.protocol_of({"title": "輕鬆跑"}) is None
    assert "20 分全力" in PP.note_test("quick") and "12 分全力" in PP.note_test("standard")
    ctx = PP.Ctx(kind="base", mode="base", allow_quality=True, rates={"road": 50.0}, aet=150)
    s = {**CPP.session_for("standard"), "day": None, "done": False, "done_by": None}
    out = PP.shape([s], 200, PP.Prefs(cap_weekday=50, cap_mode="hard"), ctx)
    assert next(x for x in out if x["kind"] == "test")["minutes"] == 70           # exempt from the cap
    assert any(n["text"] == PP.note_test("standard") for n in ctx.notes)


def test_protocol_rides_through_reconcile_and_the_store():
    from backend.engine import reconcile as R_
    assert "protocol" in R_.FIELDS
    g = {**CPP.session_for("quick"), "day": "2026-10-01", "done": False, "done_by": None}
    s = R_.session_from_gen(g, "2026-09-28", False)
    assert s["protocol"] == "quick"
    assert PS.push_dict({**s, "state": "active"})["protocol"] == "quick"


def test_api_prefs_store_the_protocol(monkeypatch):
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        got = e.c.get(f"{API}/prefs").json()
        assert got["prefs"]["cp_test_protocol"] == "quick" and got["defaults"]["cp_test_protocol"] == "quick"
        r = e.c.put(f"{API}/prefs", json={"cp_test_protocol": "standard"})
        assert r.status_code == 200 and r.json()["active"] is False
        assert e.c.get(f"{API}/prefs").json()["prefs"]["cp_test_protocol"] == "standard"
        assert e.c.put(f"{API}/prefs", json={"cp_test_protocol": "nope"}).status_code == 400


# ---------------------------------------------------------------------------
# synthetic activities
# ---------------------------------------------------------------------------

def _act(parts, day=TODAY, title=""):
    """parts: [(seconds, power, hr)] pieces; HR ramps to the piece's value."""
    p, h = [], []
    for sec, pw, hr in parts:
        p += list(pw) if hasattr(pw, "__len__") else [float(pw)] * sec
        h += list(np.linspace(h[-1] if h else 120.0, hr, sec))
    t = np.arange(len(p), dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": h, "speed": [11.0] * len(t), "power": p,
          "elapseddistance": list(t * 11.0 / 3600.0)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch, title=title,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) * 11.0 / 3600.0, "climbing": 10.0})


def standard_parts(p12=250.0, p3=300.0, rest_s=1800, hr3=172, hr12=174, last_min=None):
    twelve = [p12] * 660 + [last_min if last_min is not None else p12] * 60
    return [(900, 150.0, 135), (720, twelve, hr12), (rest_s, 110.0, 120), (180, p3, hr3), (600, 130.0, 125)]


def quick_parts(p20=240.0, hr=172, last_min=None):
    twenty = [p20] * 1140 + [last_min if last_min is not None else p20] * 60
    return [(720, 150.0, 135), (1200, twenty, hr), (300, 130.0, 125)]


def _ds(*acts, sessions=None, thresholds=None, events=None):
    ds = FakeDataset(list(acts), TODAY, settings=SETTINGS)
    ds.plan = PL.Plan(thresholds=list(thresholds or []), events=list(events or []))
    ds.plan_test_sessions = list(sessions or [])
    return ds


def _sess(day="2026-09-30", state="done", idx=0, protocol="quick", title=None):
    title = title or (CPP.session_for(protocol) or {}).get("title", "CP 測試")
    return {"uid": "t1", "day": day, "state": state, "title": title, "protocol": protocol,
            "done_by": {"index": idx, "date": day} if state == "done" else None}


# ---------------------------------------------------------------------------
# detection: the plan (done_by) first, then the power pattern
# ---------------------------------------------------------------------------

def test_scheduled_test_is_matched_through_done_by():
    # a quick test too soft for the pattern (≈ CP, not ≥ 1.03 CP) — only the plan knows it was the test
    ds = _ds(_act(quick_parts(p20=221.0)), sessions=[_sess(protocol="quick")])
    w = ds.workouts[0]
    c = R.classify(ds, w)
    assert c["type"] == "test_cp" and c["protocol"] == "quick" and c["test_match"] == "done_by"
    # without the stored session it is a hard run, not a test
    assert R.classify(_ds(_act(quick_parts(p20=221.0))), w)["type"] != "test_cp"


def test_done_by_of_another_activity_or_day_does_not_match():
    ds = _ds(_act(quick_parts(p20=221.0)), sessions=[_sess(idx=5)])
    assert R.classify(ds, ds.workouts[0])["type"] != "test_cp"
    other_day = {**_sess(), "done_by": {"index": 0, "date": "2026-09-29"}}
    ds = _ds(_act(quick_parts(p20=221.0)), sessions=[other_day])
    assert R.classify(ds, ds.workouts[0])["type"] != "test_cp"


def test_same_day_unfinished_session_needs_a_hard_bout():
    s = _sess(state="active", protocol="standard")
    ds = _ds(_act(standard_parts(p12=240.0, p3=260.0)), sessions=[s])        # 3′ 260 W ≥ 1.05 × 220
    c = R.classify(ds, ds.workouts[0])
    assert c["type"] == "test_cp" and c["protocol"] == "standard" and c["test_match"] == "same_day"
    easy = _act([(2400, 150.0, 140)])
    ds = _ds(easy, sessions=[s])
    assert R.classify(ds, ds.workouts[0])["type"] != "test_cp"


def test_fallback_pattern_standard_with_bouts_that_never_overlap():
    # the power pattern alone is only a hint (it labelled ~35 hard 5 km runs as tests)
    ds = _ds(_act(standard_parts()))
    w = ds.workouts[0]
    m = R.measure(ds, w)
    c = R.classify(ds, w, m)
    assert c["type"] != "test_cp" and c["cp_hint"] and c["protocol"] is None
    assert R.latest_cp_test(ds, TODAY) is None
    rows = {s["name"]: s["data"]["value"] for s in R.review(ds, w, "summary")["series"]}
    assert rows["CP 測試？"] == R.CP_HINT and "不當測試" in rows["CP 測試？"]
    # marked by the title: a test, and the pattern picks the protocol
    ds = _ds(_act(standard_parts(), title="週三 測試"))
    w = ds.workouts[0]
    m = R.measure(ds, w)
    c = R.classify(ds, w, m)
    assert c["type"] == "test_cp" and c["protocol"] == "standard" and c["test_match"] == "pattern"
    assert not c["cp_hint"]
    st = m["cp_bouts"]["standard"]
    L, S = st["long"], st["short"]
    assert L["start_s"] == approx(900, abs=2) and S["start_s"] == approx(900 + 720 + 1800, abs=2)
    assert S["start_s"] >= L["start_s"] + 720 + CPP.GAP_S                      # ≥ 10 min apart, no overlap
    assert st["gap_s"] == approx(1800, abs=2) and st["long_first"]


def test_fallback_pattern_quick_needs_power_and_hr():
    ds = _ds(_act(quick_parts(p20=240.0, hr=172)))                             # 240 ≥ 1.03 × 220, HR > LTHR
    c = R.classify(ds, ds.workouts[0])
    assert c["type"] != "test_cp" and c["cp_hint"]                              # unmarked: a hint only
    ds = _ds(_act(quick_parts(p20=240.0, hr=172), title="測試"))
    c = R.classify(ds, ds.workouts[0])
    assert c["type"] == "test_cp" and c["protocol"] == "quick" and c["test_match"] == "pattern"
    ds = _ds(_act(quick_parts(p20=240.0, hr=150)))                             # a tempo run vs a stale CP
    c = R.classify(ds, ds.workouts[0])
    assert c["type"] != "test_cp" and not c["cp_hint"]


def test_race_by_title_or_plan_event():
    race = _act([(900, 150.0, 140), (1500, 245.0, 175), (600, 130.0, 130)], title="10K 計時")
    ds = _ds(race)
    c = R.classify(ds, ds.workouts[0])
    assert c["type"] == "test_cp" and c["protocol"] == "race"
    ev = PL.Event(id="e1", name="城市 10K", date="2026-09-30", kind="race", priority="C", distance_km=10.0)
    untitled = _act([(900, 150.0, 140), (1500, 245.0, 175), (600, 130.0, 130)])
    assert R.classify(_ds(untitled, events=[ev]), _ds(untitled).workouts[0])["protocol"] == "race"


# ---------------------------------------------------------------------------
# analysis per protocol
# ---------------------------------------------------------------------------

def _bouts(parts):
    a = _act(parts)
    ch = a.channels
    return CPP.measure_bouts(np.asarray(ch["elapsedtime"]), np.asarray(ch["power"]), np.asarray(ch["heartrate"]))


def test_standard_two_point_when_both_bouts_are_maximal():
    r = CPP.result(_bouts(standard_parts(p12=250.0, p3=300.0)), "standard", LTHR)
    assert r["method"] == "2pt" and r["quality"] == "可信"
    assert r["cp"] == approx((250 * 720 - 300 * 180) / 540, abs=0.2)
    assert r["wprime"] == approx((300 - r["cp"]) * 180, abs=50)


def test_standard_short_recovery_is_a_reference_only():
    r = CPP.result(_bouts(standard_parts(rest_s=1000)), "standard", LTHR)
    assert r["method"] == "2pt" and r["quality"] == "參考"
    assert any("< 25 分" in x for x in r["reasons"])


def test_standard_falls_back_to_one_bout_with_the_prior():
    r = CPP.result(_bouts(standard_parts(p12=250.0, p3=245.0, hr3=150, hr12=174)), "standard", LTHR)
    assert r["method"] == "1pt_prior" and r["quality"] == "參考" and r["wprime"] is None
    assert r["cp"] == approx(250 - 13100 / 720, abs=0.3)
    assert r["cp_range"] == [approx(250 - 17100 / 720, abs=0.3), approx(250 - 9100 / 720, abs=0.3)]
    assert any("自組門檻" in x and "低 24 bpm" in x for x in r["reasons"])
    assert any("Ruiz-Alias 2025" in x for x in r["reasons"])
    women = CPP.result(_bouts(standard_parts(p12=250.0, p3=245.0)), "standard", LTHR, sex="female")
    assert women["cp"] == approx(250 - 6400 / 720, abs=0.3)


def test_quick_is_095_p20_and_its_checks():
    r = CPP.result(_bouts(quick_parts(p20=240.0)), "quick", LTHR)
    assert r["method"] == "tt20" and r["cp"] == approx(0.95 * 240) and r["quality"] == "可信"
    assert r["wprime"] is None
    kick = CPP.result(_bouts(quick_parts(p20=240.0, last_min=280.0)), "quick", LTHR)   # held back, then kicked
    assert kick["quality"] == "參考" and any("自組門檻" in x and "最後 1 分" in x for x in kick["reasons"])
    soft = CPP.result(_bouts(quick_parts(p20=240.0, hr=150)), "quick", LTHR)          # HR far below LTHR
    assert soft["quality"] == "不採用" and CPP.apply_payload(soft, "2026-09-30") is None


def test_race_is_riegel_anchored_at_30_min():
    r = CPP.result(_bouts([(900, 150.0, 140), (1500, 245.0, 175), (600, 130.0, 130)]), "race", LTHR)
    assert r["method"] == "race" and r["race_s"] == 1500
    assert r["cp"] == approx(r["race_power"] * (1500 / 1800) ** 0.07)
    assert r["race_power"] == approx(245.0, abs=1.0)
    assert (1200 / 1800) ** 0.07 == approx(0.972, abs=1e-3)               # the doc's 20-min check


CP_TEST_FIXTURE = Path(__file__).parent / "fixtures" / "cp_test_2026-09-30.json.gz"


def _frozen_cp_test():
    """The 2026-09-30 COROS test frozen to its 1-s power and HR (no GPS, no ids,
    ~4 KB): the FIT's time / power / heart-rate channels as parse_fit gives them."""
    d = json.load(gzip.open(CP_TEST_FIXTURE, "rt", encoding="utf-8"))
    arr = lambda k: np.array([np.nan if v is None else v for v in d[k]], float)
    return np.arange(d["n"], dtype=float), arr("power_w"), arr("heart_rate_bpm")


def test_real_2026_09_30_test_falls_back_to_one_bout():
    """The athlete's test: 3′ 218 W < 12′ 222 W, 3′ HR peak 149 (146 in the lap,
    +15 s of HR lag) vs 171, 16.5 min of recovery → single bout, CP ≈ 204 W,
    參考. (The 12′ bout's last minute is 220 W on the 1-s records: no kick.)"""
    b = CPP.measure_bouts(*_frozen_cp_test())
    res = CPP.result(b, "standard", lthr=None)
    assert res["method"] == "1pt_prior" and res["quality"] == "參考"
    assert 203.0 <= res["cp"] <= 204.5
    assert res["p3"] < res["p12"]
    text = " ".join(res["reasons"])
    import re
    assert re.search(r"比 12 分段 171 低 2\d bpm：不是全力（自組門檻）", text) and "< 25 分" in text
    assert "不高於 12 分" in text and not res["checks"][0]["own"]            # the model check is not ours
    pay = CPP.apply_payload(res, "2026-09-30", 7)
    assert pay["cp"] == 204 and pay["wprime"] is None and pay["cp_method"] == "1pt_prior" and "參考" in pay["label"]


# ---------------------------------------------------------------------------
# the same-method comparison
# ---------------------------------------------------------------------------

T = PL.Threshold


def test_reference_is_the_previous_result_of_the_same_method():
    ths = [T("2026-08-01", cp=205.0, cp_method="tt20"), T("2026-08-20", cp=216.0, cp_method="2pt")]
    r = CPP.reference(ths, "2026-09-30", "tt20")
    assert r["same_method"] and r["cp"] == 205.0 and r["date"] == "2026-08-01"
    r = CPP.reference(ths, "2026-09-30", "2pt")
    assert r["same_method"] and r["cp"] == 216.0
    # only a different method on file: converted to the same definition (外插 × 1.05)
    r = CPP.reference(ths[:1], "2026-09-30", "2pt")
    assert not r["same_method"] and r["converted"] and r["cp"] == approx(205.0 * 1.05)
    # a legacy row (no cp_method) is a 3′/12′ two-point test
    assert CPP.reference([T("2026-08-01", cp=210.0)], "2026-09-30", "2pt")["same_method"]
    # rows on / after the test day are not "previous"
    assert CPP.reference([T("2026-09-30", cp=204.0, cp_method="tt20")], "2026-09-30", "tt20", cp_now=190.0)["cp"] == 190.0


def test_rotating_protocols_does_not_keep_flagging_an_update():
    from backend.engine import status as ST
    from backend.engine import workout_review as WR
    # applied: a quick test in August (tt20 205), then a standard test (2pt 214) today
    plan = PL.Plan(thresholds=[T("2026-08-10", cp=205.0, cp_method="tt20", lthr=165, aethr=150)])
    ev = {"cp": 214.0, "method": "2pt"}
    ref = CPP.reference(plan.thresholds, "2026-09-30", ev["method"], 205.0)
    delta = ev["cp"] / ref["cp"] - 1
    assert abs(delta) <= WR.CP_DELTA                        # 214 vs 205 × 1.05 = 215: not "要更新"
    raw = ev["cp"] / 205.0 - 1
    assert abs(raw) > WR.CP_DELTA                           # the old comparison would have flagged it
    st = ST.Status.__new__(ST.Status)
    st.plan, st.today, st.goals, st.ds, st.cp_protocol = plan, TODAY, {"days_to_next_a": None}, None, "quick"
    ct = {"idx": 3, "date": "2026-09-30", "cp": 214.0, "wprime": 16000.0, "cp_range": [214, 214], "cp_now": 205.0,
          "delta": delta, "ref": ref, "method": "2pt", "method_label": "兩點", "protocol": "standard",
          "protocol_label": "標準", "quality": "可信", "reasons": [],
          "apply": CPP.apply_payload({"cp": 214.0, "wprime": 16000.0, "method": "2pt", "protocol": "standard",
                                      "quality": "可信"}, "2026-09-30", 3), "applied": False}
    orig = WR.latest_cp_test
    try:
        WR.latest_cp_test = lambda ds, today: ct
        ind = st.i_testing()
        assert ind.text != "要更新" and ind.extra["cp_test"]["apply"]                 # still offered, not nagged
        WR.latest_cp_test = lambda ds, today: {**ct, "cp": 230.0, "delta": 230.0 / ref["cp"] - 1}
        assert st.i_testing().text == "要更新"                                        # a real change still flags
    finally:
        WR.latest_cp_test = orig


# ---------------------------------------------------------------------------
# the review card and 「套用這次的 CP」
# ---------------------------------------------------------------------------

def test_review_card_offers_the_apply_button():
    ds = _ds(_act(standard_parts()), sessions=[_sess(protocol="standard")])
    w = ds.workouts[0]
    card = R.review(ds, w, "cp_test")
    assert card["empty"] is None and card["classification"]["protocol"] == "standard"
    a = card["action"]
    assert a["url"] == "/api/v1/plan/thresholds/apply-cp" and a["body"]["date"] == "2026-09-30"
    assert a["body"]["cp_method"] == "2pt" and a["body"]["wprime"] is not None
    text = " ".join(s["data"]["value"] for s in card["series"] if s["data"]["kind"] == "value")
    assert "標準" in text and "依課表對應" in text
    # once a row of that day exists, no button
    ds.plan.thresholds.append(T("2026-09-30", cp=a["body"]["cp"], cp_method="2pt"))
    assert "action" not in R.review(ds, w, "cp_test")


@pytest.fixture
def temp_plan(tmp_path, monkeypatch):
    """A temp plan.json for the API (never the user's)."""
    from backend.api import plan as API
    path = tmp_path / "plan.json"
    PL.Plan(thresholds=[T("2026-09-30", lthr=165.0, note="LTHR")]).save(path)
    load, save = PL.Plan.load.__func__, PL.Plan.save
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, p=path: load(cls, p)))
    monkeypatch.setattr(PL.Plan, "save", lambda self, p=path: save(self, p))
    monkeypatch.setattr(API, "_notify", lambda thresholds: None)
    return path


def test_apply_cp_writes_the_test_day_row(temp_plan):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as API
    app = FastAPI()
    app.include_router(API.router)
    c = TestClient(app)
    url = "/api/v1/plan/thresholds/apply-cp"
    r = c.post(url, json={"date": "2026-09-30", "cp": 203.7, "cp_method": "1pt_prior", "activity_index": 7,
                          "note": "標準；單段＋W′ 先驗；品質 參考"})
    assert r.status_code == 200
    got = PL.Plan.load(temp_plan).thresholds
    assert len(got) == 1                                                  # merged into that day's row
    t = got[0]
    assert (t.date, t.cp, t.cp_method, t.wprime, t.lthr) == ("2026-09-30", 204, "1pt_prior", None, 165.0)
    assert "品質 參考" in t.note and "#7" in t.note
    assert c.post(url, json={"date": "2026-09-30", "cp": 210, "cp_method": "tt20", "wprime": 12000}).status_code == 400
    assert c.post(url, json={"date": "2999-01-01", "cp": 210, "cp_method": "tt20"}).status_code == 400
    assert c.post(url, json={"date": "2026-09-30", "cp": 210, "cp_method": "magic"}).status_code == 400
    assert c.post(url, json={"date": "2026-09-01", "cp": 250, "cp_method": "2pt", "wprime": 15000}).status_code == 200
    rows = {t.date: t for t in PL.Plan.load(temp_plan).thresholds}
    assert rows["2026-09-01"].wprime == 15000 and rows["2026-09-01"].cp_method == "2pt"
    # the plan page's PUT round-trips the new fields
    body = [t.__dict__ for t in PL.Plan.load(temp_plan).thresholds]
    assert c.put("/api/v1/plan/thresholds", json=body).status_code == 200
    assert {t.date: t.cp_method for t in PL.Plan.load(temp_plan).thresholds}["2026-09-01"] == "2pt"


def test_old_plan_json_without_the_new_fields_loads(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text('{"thresholds": [{"date": "2026-01-01", "cp": 200, "note": ""}]}', "utf-8")
    t = PL.Plan.load(p).thresholds[0]
    assert t.cp == 200 and t.wprime is None and t.cp_method is None


# ---------------------------------------------------------------------------
# plan_store: the synchronous read classify uses
# ---------------------------------------------------------------------------

def test_test_sessions_reads_the_store_read_only(tmp_path):
    import json
    import sqlite3
    from sqlalchemy import create_engine
    from backend.db.models import Base
    db = tmp_path / "w.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO plan_sessions (athlete_id, uid, week_start, day, kind, title, minutes, origin, edited,"
                " provisional, state, done_by, protocol, updated_at) VALUES (1, 'a', '2026-09-28', '2026-09-30',"
                " 'test', 'CP 測試 20 分全力', 37, 'auto', 0, 0, 'done', ?, 'quick', '2026-09-30')",
                (json.dumps({"index": 4, "date": "2026-09-30"}),))
    con.execute("INSERT INTO plan_sessions (athlete_id, uid, week_start, day, kind, title, minutes, origin, edited,"
                " provisional, state, updated_at) VALUES (1, 'b', '2026-09-28', '2026-10-01', 'easy', '輕鬆跑', 40,"
                " 'auto', 0, 0, 'active', '2026-09-30')")
    con.commit()
    con.close()
    got = PS.test_sessions(db)
    assert got == [{"uid": "a", "day": "2026-09-30", "state": "done", "title": "CP 測試 20 分全力",
                    "protocol": "quick", "done_by": {"index": 4, "date": "2026-09-30"}, "gen_key": None}]
    assert PS.test_sessions(tmp_path / "missing.db") == []
    assert PS.test_sessions() == []                                   # tests: no user DB (conftest)


# ---------------------------------------------------------------------------
# timing rules (status.i_testing, unchanged): every 42–90 days, not within
# 10 days of an A race, 10–21 days before is ideal
# ---------------------------------------------------------------------------

def _testing(age_days, days_to=None, protocol="quick"):
    from backend.engine import status as ST
    from backend.engine import workout_review as WR
    d = (TODAY - dt.timedelta(days=age_days)).isoformat()
    st = ST.Status.__new__(ST.Status)
    st.plan = PL.Plan(thresholds=[T(d, cp=210.0, lthr=165.0, aethr=150.0)])
    st.today, st.goals, st.ds, st.cp_protocol = TODAY, {"days_to_next_a": days_to}, None, protocol
    orig = WR.latest_cp_test
    WR.latest_cp_test = lambda ds, today: None
    try:
        return st.i_testing()
    finally:
        WR.latest_cp_test = orig


def test_timing_rules():
    from backend.engine import status as ST
    assert (ST.TEST_DAYS_WATCH, ST.TEST_DAYS_BAD) == (42, 90)
    assert _testing(41).level == ST.GOOD and not _testing(41).action
    assert _testing(43).level == ST.WATCH and "20 分全力" in _testing(43).action
    assert _testing(91).level == ST.BAD
    assert "賽前 15 天正好是測試的時機" in _testing(60, days_to=15).action          # 10–21 days before
    close = _testing(60, days_to=5)
    assert close.action == "賽前 10 天內不要測，賽後再測" and close.level == ST.WATCH
    assert "12 分" in _testing(60, protocol="standard").action
    race = _testing(60, protocol="race")
    assert race.action.startswith(CPP.NOTE_RACE)                                # 還缺什麼: no session, a note
