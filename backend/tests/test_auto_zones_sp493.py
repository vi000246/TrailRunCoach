"""SP-493 (owner 2026-10-14): the 自動 課表心率區間 and the zones in effect everywhere the 課表 reads HR.

* 自動 picks the model by which numbers are believable (threshold_confidence.auto_pick): a reliable
  LTHR → lthr; else a believable max / resting HR → hrr; else hrmax. Never chosen = 自動; a model
  chosen by hand stays.
* the interval texts, the projected weeks, a template's % LTHR, the push and the race calculator's
  「LTHR」 cap follow the zones in effect; the trail HR model's race HR and the load stay × LTHR.
* target_policy: a stale CP runs the auto road intervals by HR; the trail rules stay.
* the LTHR reminder is the box's one row (zone note); SP-471 looks at the max HR under 儲備心率.
* a switch 自動 makes by itself is recorded once, told once in the box and re-sends what is on the
  watch.
Synthetic data only (the three models × the switches)."""
import asyncio
import copy
import datetime as dt
import re

import pytest

from backend.engine import base_check as BCK
from backend.engine import hr_profile as HP
from backend.engine import interval_library as IL
from backend.engine import quality_gate as QG
from backend.engine import suggestions as SG
from backend.engine import target_policy as TP
from backend.engine import threshold_confidence as TC
from backend.tests.test_threshold_confidence import TODAY, lt, run

MHR, RHR, LTHR = 189.0, 53.0, 155.0                   # the owner's numbers in the ticket (hrr: Z4 167–173)


def zones(model: str, lthr: float = LTHR, auto: bool = False) -> dict:
    eff = {"model": model, "setting": "auto", "auto": True, "reason": "r", "why": "x"} if auto else None
    return HP.plan_hr_zones(lthr, 0.89 * lthr, False, MHR, RHR, model, None, "m", "r", eff)


HRZ = {m: zones(m) for m in HP.PLAN_MODELS}
BPM = re.compile(r"心率 (\d+)–(\d+) bpm")


# ---------------------------------------------------------------------------
# 1. 自動: which model, and the setting it reads
# ---------------------------------------------------------------------------

RUNS = [run(s120=178.0, s60=180.0), run(s120=176.0, s60=178.0), run(s120=175.0, s60=177.0)]


def _assess(lthr, mhr, rhr, model="auto", runs=RUNS):
    return TC.assess(lthr, mhr, rhr, TODAY, runs, {}, 250.0, hr_model=model)


MANUAL_MHR = {"value": 182.0, "source_kind": "manual", "label": "你的設定"}
# SP-493 R1: a watch max HR the runs of 90 days came within 5 bpm of is believable — 186 stays unseen by RUNS
WATCH_MHR = {"value": 186.0, "source_kind": "watch", "label": "來自手錶"}
REST = {"value": 50.0, "source_kind": "manual"}


@pytest.mark.parametrize("lthr, mhr, rhr, model, why", [
    (lt(162.0, kind="test", date="2026-08-01"), MANUAL_MHR, REST, "lthr", "lthr_test"),          # tested, 2 months
    (lt(162.0, kind="manual", date="2026-07-01"), MANUAL_MHR, REST, "lthr", "lthr_manual"),      # entered, 3 months
    (lt(162.0, kind="estimate", date="2026-09-01"), MANUAL_MHR, REST, "lthr", "lthr_estimate"),  # nothing against it
    (lt(162.0, kind="test", date="2026-03-01"), MANUAL_MHR, REST, "hrr", "hrr"),                 # > half a year
    (lt(162.0, kind="watch", date=None), MANUAL_MHR, REST, "hrr", "hrr"),                        # the watch's LTHR
    (lt(162.0, kind="prior", date=None), MANUAL_MHR, REST, "hrr", "hrr"),                        # 0.90 × max HR
    (lt(162.0, kind="watch", date=None), WATCH_MHR, REST, "hrmax", "hrmax"),                     # nothing believable
    (lt(162.0, kind="watch", date=None), MANUAL_MHR, {"value": None}, "hrmax", "hrmax"),         # no resting HR
    (lt(162.0, kind="watch", date=None), {"value": None}, REST, "lthr", "lthr_only"),            # no max HR at all
])
def test_auto_pick(lthr, mhr, rhr, model, why):
    a = _assess(lthr, mhr, rhr)["auto"]
    assert (a["model"], a["why"]) == (model, why) and a["reason"]


def test_auto_pick_an_estimate_with_evidence_against_it_is_not_reliable():
    # a cool 60-min effort well above the LTHR: an effort signal on top of the estimate's source
    runs = RUNS + [run(hr60=176.0, dur_s=3700.0)]
    r = _assess(lt(160.0, kind="estimate", date="2026-09-01"), MANUAL_MHR, REST, runs=runs)
    if not [s for s in r["lthr"]["signals"] if s["id"] != "source" and s["level"] != "hint"]:
        pytest.skip("the synthetic run didn't raise an effort signal")
    assert r["auto"]["model"] == "hrr" and "矛盾" in r["auto"]["reason"]


def test_never_chosen_is_auto_and_a_chosen_model_stays(monkeypatch):
    from backend.engine.wko5expr import datasource
    monkeypatch.setattr(HP, "DEFAULT_SETTING", HP.AUTO)              # production's default (conftest pins lthr)
    stored = {}
    monkeypatch.setattr(datasource, "read_setting", lambda k, d=None, u=1: stored.get(k, d))
    assert HP.plan_model() == "auto"                                 # no row: never chosen
    for m in ("lthr", "hrr", "hrmax", "auto"):
        stored[HP.MODEL_KEY] = m
        assert HP.plan_model() == m                                  # a stored choice is kept
    stored[HP.MODEL_KEY] = "bogus"
    assert HP.plan_model() == "auto"


def test_the_setting_accepts_auto():
    from backend.settings import repository as R
    assert R.DEFAULTS["plan.hr_zone_model"] == "auto"
    R.validate("plan.hr_zone_model", "auto")
    with pytest.raises(ValueError):
        R.validate("plan.hr_zone_model", "friel")
    R.validate("plan.hr_zone_auto", {"model": "hrr", "since": "2026-10-14"})


def test_effective_model_locked_or_picked(monkeypatch):
    monkeypatch.setattr(HP, "plan_model", lambda user_id=1: "hrmax")
    assert HP.effective_model(object())["model"] == "hrmax"          # locked: no check at all
    monkeypatch.setattr(HP, "plan_model", lambda user_id=1: "auto")
    monkeypatch.setattr(TC, "auto_choice", lambda ds, plan, today: {"model": "hrr", "why": "hrr", "reason": "R"})

    class DS:
        today, plan = 20000.0, None
    e = HP.effective_model(DS())
    assert e == {"model": "hrr", "setting": "auto", "auto": True, "reason": "R", "why": "hrr"}
    monkeypatch.setattr(TC, "auto_choice", lambda ds, plan, today: None)
    assert HP.effective_model(DS())["model"] == "lthr"                # the check failed: the zones' default


def test_plan_hr_zones_for_reads_the_zones_in_effect(monkeypatch):
    monkeypatch.setattr(HP, "effective_model", lambda ds, day=None, setting=None:
                        {"model": "hrr", "setting": "auto", "auto": True, "reason": "R", "why": "hrr"})
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    monkeypatch.setattr(HP, "max_hr", lambda ds, day, acc=None, use_account=True: {"value": MHR, "source": "m"})
    monkeypatch.setattr(HP, "rest_hr", lambda ds, day, acc=None, use_account=True: {"value": RHR, "source": "r"})
    z = HP.plan_hr_zones_for(None, TODAY, LTHR, None, False)
    assert z["model"] == "hrr" and z["setting"] == "auto" and z["auto"]["reason"] == "R"
    assert z["targets"]["threshold"] == [167.0, 173.0]               # the owner's HRR Z4


# ---------------------------------------------------------------------------
# 2. × LTHR in the zones in effect
# ---------------------------------------------------------------------------

def test_frac_hr_is_the_same_place_in_the_same_zone():
    h = HRZ["hrr"]
    assert HP.frac_hr(1.0, HRZ["lthr"], LTHR) == pytest.approx(LTHR)            # the LTHR model: × LTHR
    assert HP.frac_hr(1.0, None, LTHR) == pytest.approx(LTHR)
    assert HP.frac_hr(0.95, h, LTHR) == pytest.approx(167.0)                     # Z4's bottom
    assert HP.frac_hr(1.02, h, LTHR) == pytest.approx(173.0)                     # Z4's top
    assert HP.frac_hr(1.0, h, LTHR) == pytest.approx(167.0 + 6.0 * 5 / 7)       # 5/7 into Z4
    assert HP.frac_hr(0.85, h, LTHR) == pytest.approx((133.0 + 154.0) / 2)      # mid Z2
    assert HP.frac_hr(1.5, h, LTHR) == MHR                                      # Z6: never above the max HR
    hm = HRZ["hrmax"]
    assert HP.frac_hr(1.0, hm, LTHR) == pytest.approx(132.0 + 19.0 * 5 / 7)     # % HRmax Z4 132–151


def _class_variants():
    out = {}
    for rung in IL.LIBRARY:
        f = IL.fit(rung, None)
        out.setdefault(f["variant"].cls, f)
    return out


@pytest.mark.parametrize("model", HP.PLAN_MODELS)
def test_interval_texts_follow_the_zones_in_effect(model):
    th0 = {"cp": 260.0, "lthr": LTHR, "aet": 140.0}
    for cls, f in _class_variants().items():
        old = IL.session_for(f, th0)["target"]
        new = IL.session_for(f, {**th0, "hr_model": HRZ[model]})["target"]
        if model == "lthr":
            assert new == old, cls                                   # the LTHR model: unchanged
            continue
        m = BPM.search(new)
        if m is None:
            assert not BPM.search(old), cls                          # a short Z5 rep: no HR either way
            continue
        assert (int(m.group(1)), int(m.group(2))) == HP.work_band(HRZ[model], cls), cls


@pytest.mark.parametrize("model", HP.PLAN_MODELS)
def test_dose_step_texts_follow_the_zones_in_effect(model):
    th0 = {"cp": 260.0, "lthr": LTHR, "aet": 140.0}
    for spec in QG.LADDER:
        if spec[0] not in QG._HR_CLASS:
            continue
        old = QG.session(spec, th0)["target"]
        new = QG.session(spec, {**th0, "hr_model": HRZ[model]})["target"]
        if model == "lthr":
            assert new == old, spec[0]
        else:
            m = BPM.search(new)
            assert m and (int(m.group(1)), int(m.group(2))) == HP.work_band(HRZ[model], QG._HR_CLASS[spec[0]]), spec[0]


def test_the_owners_threshold_session_moves_up_to_the_hrr_threshold_zone():
    """The ticket's numbers: the threshold interval (0.95–1.00 × LTHR 155 = 147–155) becomes the
    儲備心率 Z4 (167–173) — the week card's 閾值 row."""
    f = _class_variants()["Z3near"]
    th0 = {"cp": 260.0, "lthr": LTHR, "aet": 140.0}
    assert "心率 147–155 bpm" in IL.session_for(f, th0)["target"]
    assert "心率 167–173 bpm" in IL.session_for(f, {**th0, "hr_model": HRZ["hrr"]})["target"]
    assert HRZ["hrr"]["targets"]["threshold"] == [167.0, 173.0]


@pytest.mark.parametrize("model", HP.PLAN_MODELS)
def test_projected_weeks_read_the_zones_in_effect(model):
    from backend.engine.projection import target_texts
    from backend.engine.zones import target_hr
    th = {"cp": 260.0, "lthr": LTHR, "aet": 140.0, "hr_model": HRZ[model]}
    t = target_texts(th)
    lo, hi = HRZ[model]["targets"]["threshold"]
    assert f"{lo:.0f}–{hi:.0f} bpm" in t["threshold"]
    assert target_hr("z2", None, "aet", LTHR, 140.0, HRZ[model]) == [None, HRZ[model]["easy"][1]]
    # without zones: Friel × LTHR, as before
    assert target_hr("threshold", 0.95, 1.0, LTHR, 140.0, None) == [0.95 * LTHR, 1.0 * LTHR]


@pytest.mark.parametrize("model", HP.PLAN_MODELS)
def test_a_templates_own_lthr_percent_and_the_push(model):
    from backend.engine import workout_steps as WS
    from backend.sync import coros_workouts as CW
    th = {"cp": 260.0, "lthr": LTHR, "aet": 140.0, "hr_model": HRZ[model]}
    c = WS.Ctx.of(th, "hr")
    lo, hi = WS.hrp_band(c, WS.MP_HR)
    assert (lo, hi) == (round(HP.frac_hr(WS.MP_HR[0], HRZ[model], LTHR)), round(HP.frac_hr(WS.MP_HR[1], HRZ[model], LTHR)))
    if model == "lthr":
        assert (lo, hi) == (round(WS.MP_HR[0] * LTHR), round(WS.MP_HR[1] * LTHR))
    # the push reads the session text's bpm: the zones' band of the class
    f = _class_variants()["Z3near"]
    s = IL.session_for(f, th)
    got = CW._work_hr(s, CW.Thresholds.of(th), "Z3near")
    want = HP.work_band(HRZ[model], "Z3near") if model != "lthr" else (round(0.95 * LTHR), round(1.0 * LTHR))
    assert got == ("hr", *want)


def test_race_calculator_lthr_cap_follows_the_zones_but_the_trail_model_stays():
    from backend.engine.racepower import seg_targets as ST
    from backend.engine.racepower import zonebar as ZB
    b = {"model": "hrr", "mhr": MHR, "rhr": RHR, "acc": None}
    thr = ZB.threshold_hr(LTHR, b)
    assert thr[0] == round(HP.frac_hr(1.0, HRZ["hrr"], LTHR)) and "儲備心率" in thr[1]
    assert ZB.threshold_hr(LTHR, {"model": "lthr"}) == (LTHR, "LTHR")
    road = {"type": "trail", "summary": {"time_s": 2 * 3600}}
    assert ST.hr_cap(road, 140.0, LTHR, thr) == thr                  # ≤ 3 h, no trail model: the zones' threshold
    assert ST.hr_cap(road, 140.0, LTHR) == (LTHR, "LTHR")
    fitted = {"type": "trail", "summary": {"time_s": 2 * 3600, "trail_hr": {"x": 0.9}}}
    assert ST.hr_cap(fitted, 140.0, LTHR, thr)[0] == pytest.approx(0.9 * LTHR)   # the runner's own ratio: bpm
    long_ = {"type": "trail", "summary": {"time_s": 5 * 3600}}
    assert ST.hr_cap(long_, 140.0, LTHR, thr) == (140.0, ST.hr_cap(long_, 140.0, LTHR)[1])


def test_load_stays_on_lthr():
    """hrTSS / the step IF stay bpm ÷ LTHR whatever the zones (Garmin / COROS do the same)."""
    from backend.engine.racepower import watch_export as WE
    legs = [{"t": 3600.0, "target": {"type": "hr", "lo": 150.0, "hi": 160.0}}]
    assert WE.tss_info(legs, {"lthr": LTHR})["tss"] == pytest.approx(round(100 * (155.0 / LTHR) ** 2, 1))


# ---------------------------------------------------------------------------
# 3. 目標依據: a stale CP
# ---------------------------------------------------------------------------

def test_cp_stale_rule():
    d = dt.date(2026, 10, 14)
    assert BCK.cp_stale(d, None, None)
    assert BCK.cp_stale(d, "2026-08-01", "2026-07-01")
    assert not BCK.cp_stale(d, "2026-09-20", None)                    # a CP test 24 days ago
    assert not BCK.cp_stale(d, None, "2026-10-05")                    # a race 9 days ago
    assert BCK.cp_stale(d, (d - dt.timedelta(days=BCK.EP_ALL_OUT_DAYS + 1)).isoformat(), None)


@pytest.fixture
def stryd(monkeypatch):
    monkeypatch.setattr(TP, "_auto_power_ok", lambda: True)


def test_a_stale_cp_runs_the_road_intervals_by_hr(stryd):
    th = {"cp": 260.0, "lthr": LTHR, "aet": 140.0}
    iv = {"kind": "quality", "title": "閾值 3×10 分"}
    assert TP.target_policy(iv, None, th)["basis"] == "power"
    p = TP.target_policy(iv, None, {**th, "cp_stale": True})
    assert p["basis"] == "hr" and "CP 太舊" in p["why"]
    assert TP.target_policy(iv, None, th, cp_stale=True)["basis"] == "hr"
    # the rest keeps its rule: road easy / long power, hill repeats power, trail HR, climbs none
    for s, b in (({"kind": "easy", "title": "輕鬆跑"}, "power"), ({"kind": "long", "title": "長跑"}, "power"),
                 ({"kind": "quality", "title": "上坡 6×2 分"}, "power"),
                 ({"kind": "easy", "title": "越野輕鬆跑", "terrain": "trail"}, "hr"),
                 ({"kind": "hike", "title": "越野跑"}, "hr"), ({"kind": "long", "title": "長爬坡"}, "none"),
                 ({"kind": "easy", "title": "下坡練習"}, "none")):
        assert TP.target_policy(s, None, {**th, "cp_stale": True})["basis"] == b, s["title"]


def test_a_chosen_power_basis_stays_with_a_stale_cp(stryd):
    from backend.engine import plan_prefs as PP
    th = {"cp": 260.0, "lthr": LTHR, "aet": 140.0, "cp_stale": True}
    assert TP.target_policy({"kind": "quality", "title": "閾值", "target_basis": "power"}, None, th)["basis"] == "power"
    assert TP.target_policy({"kind": "quality", "title": "閾值"}, PP.Prefs(target_basis="power"), th)["basis"] == "power"


def test_push_dict_carries_the_stale_cp(stryd):
    from backend.engine import plan_store as PS
    s = {"uid": "u", "week_start": "2026-10-12", "kind": "quality", "title": "閾值 3×10 分", "minutes": 60,
         "target": "功率 250–270 W · 心率 147–155 bpm", "state": "active", "day": "2026-10-15"}
    assert PS.push_dict(s, None)["basis"] == "power"
    assert PS.push_dict(s, None, {"cp_stale": True})["basis"] == "hr"


# ---------------------------------------------------------------------------
# 4. the reminders: the box's one row; SP-471 under 儲備心率
# ---------------------------------------------------------------------------

def test_the_lthr_test_row_says_the_zones_and_what_a_test_does():
    est = lt(150.0, kind="estimate", date="2026-09-01")
    for model, word in (("lthr", "乳酸閾值"), ("hrr", "儲備心率"), ("hrmax", "最大心率")):
        r = _assess(est, MANUAL_MHR, REST, model=model)
        sg = next((s for s in r["suggestions"] if "tt30" in s["tests"]), None)
        assert sg is not None and f"課表目前用{word}區間" in sg["text"], model
        if model != "lthr":
            assert "課表的心率目標不靠 LTHR" in sg["text"]
        assert r["warn"]["model"] == model
    r = _assess(est, WATCH_MHR, REST, model="auto")                   # 自動 → hrmax here
    sg = next(s for s in r["suggestions"] if "tt30" in s["tests"])
    assert "課表目前用最大心率區間（自動）" in sg["text"] and "自動」區間也會改用乳酸閾值區間" in sg["text"]


def test_the_editor_has_no_badge_and_the_status_line_no_lthr(monkeypatch):
    r = _assess(lt(150.0, kind="estimate", date="2026-09-01"), MANUAL_MHR, REST, model="lthr")
    assert r["lthr"]["confidence"] == "low"
    assert TC.warn_of(r) is None and TC.session_warn(r["warn"], {"kind": "quality"}, [], {}) is None


def _note(cap=165.0, cp=250.0, pw=225.0):
    return {"src": "easy_power", "nums": {"power": pw, "cap": cap, "cp": cp, "pct": pw / cp * 100,
                                          "band_top": 0.8 * cp, "band_top_pct": 80.0}}


def test_sp471_under_hrr_looks_at_the_max_hr():
    d = dt.date(2026, 10, 14)
    fresh = {"last_cp_test": "2026-10-01"}
    # 儲備心率, LTHR estimated low, max HR from the watch (low): the max-HR line, the max-HR test link
    ep = BCK.easy_power_tests(_note(cap=154.0), d, lthr=155.0, lthr_conf="low", hr_model="hrr", mhr=189.0,
                              mhr_conf="low", model_label="COROS 儲備心率", **fresh)
    aet = next(t for t in ep["tests"] if t["kind"] == "aet")
    assert aet["mhr_doubt"] and not aet["lthr_doubt"]
    assert "從最大心率 189 算的，最大心率可信度低" in aet["why"] and "自動估算" not in aet["why"]
    assert "LTHR 155" not in aet["why"]                                # an unbelievable LTHR is no yardstick
    # the max HR believable, the LTHR not: no HR side at all (and no 「LTHR 可信」 CP reasoning)
    ep = BCK.easy_power_tests(_note(cap=154.0), d, lthr=155.0, lthr_conf="low", hr_model="hrr", mhr=189.0,
                              mhr_conf="medium", **fresh)
    assert ep is None or all(t["kind"] != "aet" for t in ep["tests"])
    # a believable LTHR under 儲備心率: the cap-vs-LTHR comparison stays
    ep = BCK.easy_power_tests(_note(cap=154.0), d, lthr=155.0, lthr_conf="medium", hr_model="hrr", mhr=189.0,
                              mhr_conf="medium", **fresh)
    assert any("跟 LTHR 155 幾乎一樣" in t["why"] for t in ep["tests"] if t["kind"] == "aet")
    # a measured AeT caps easy runs in every model: the max HR is not its basis
    ep = BCK.easy_power_tests(_note(cap=154.0), d, lthr=155.0, lthr_conf="low", hr_model="hrr", mhr=189.0,
                              mhr_conf="low", cap_measured=True, **fresh)
    assert ep is None or all(not t.get("mhr_doubt") for t in ep["tests"])


def test_sp471_under_lthr_is_unchanged():
    d = dt.date(2026, 10, 14)
    ep = BCK.easy_power_tests(_note(cap=154.0), d, lthr=155.0, lthr_conf="low", hr_model="lthr", mhr=189.0,
                              mhr_conf="low", last_cp_test="2026-10-01")
    aet = next(t for t in ep["tests"] if t["kind"] == "aet")
    assert aet["lthr_doubt"] and not aet["mhr_doubt"] and "LTHR 155 是自動估算、可信度低" in aet["why"]


def test_easy_power_rows_link_the_max_hr_test():
    from backend.engine import overview as O
    ep = {"summary": "S", "tests": [{"kind": "aet", "why": "W", "lthr_doubt": False, "mhr_doubt": True}]}
    rows = O.easy_power_suggestions([], ep, lambda: None, lambda: {"title": "AeT", "minutes": 50, "kind": "test"}, "ua60")
    assert [x["test"] for x in rows[0]["links"]] == ["hrmax"] and rows[0]["easy_power"]["mhr_doubt"]


# ---------------------------------------------------------------------------
# 5. a switch 自動 made: recorded once, told once, re-sent
# ---------------------------------------------------------------------------

def test_hr_switch_row_once():
    rec = {"model": "hrr", "setting": "auto", "since": "2026-10-14", "from": "lthr", "reason": "R", "auto": True}
    rows = SG.hr_switch_rows(rec, "2026-10-14")
    assert [r["id"] for r in rows] == ["hr_zone_switch:2026-10-14"] and rows[0]["pick"] is None
    assert "儲備心率" in rows[0]["title"] and "乳酸閾值 → 儲備心率：R。" == rows[0]["reason"]
    assert SG.hr_switch_rows(rec, "2026-10-29") == []                 # past HR_SWITCH_DAYS
    assert SG.hr_switch_rows({**rec, "auto": None}, "2026-10-14") == []   # a model the user picked
    assert SG.hr_switch_rows({"model": "hrr", "since": "2026-10-14"}, "2026-10-14") == []   # the first record
    dis = SG.record({}, rows[0]["id"], "dismissed", dt.datetime(2026, 10, 14, 9))
    assert SG.visible(rows, dis, dt.datetime(2026, 10, 15)) == []     # ✕: once


def test_model_history_says_an_auto_switch():
    from backend.engine import threshold_history as TH
    h = TH.model_history_add(None, dt.date(2026, 10, 14), "lthr", "hrr", "R")
    assert h == [{"date": "2026-10-14", "from": "lthr", "to": "hrr", "auto": True, "reason": "R"}]
    TH.validate_model_history(h + [{"date": "2026-10-15", "from": "hrr", "to": "auto"}])
    e = TH._models(h)[0]
    assert e["source"] == "hr_model_auto" and e["detail"] == "R" and e["to_label"] == "COROS 儲備心率"


def _hr_inputs(model, setting="auto", easy=(130, 150)):
    from backend.tests.test_plan_auto import base_inputs
    inp = base_inputs()
    hrz = {**zones(model if model != "x" else "lthr", auto=setting == "auto"), "easy": list(easy)}
    hrz["setting"] = setting
    for t in (inp["thresholds"], inp["cur"]["thresholds"]):
        t["hr_model"] = copy.deepcopy(hrz)
    return inp


def test_check_hr_model_records_and_starts_the_run(monkeypatch):
    from backend.engine import plan_auto as PA
    from backend.engine import threshold_history as TH
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import make_db, run as arun
    seen = []
    monkeypatch.setattr(PA, "after_hr_model", lambda: seen.append(1))

    async def go():
        db = await make_db()
        repo = SettingsRepository(db)
        assert await PA.check_hr_model(db, _hr_inputs("lthr")) is None and seen == []      # the first record
        assert (await repo.get(PA.HR_ZONE_KEY))["model"] == "lthr"
        assert await PA.check_hr_model(db, _hr_inputs("lthr")) is None                     # unchanged
        assert await PA.check_hr_model(db, {**_hr_inputs("hrr"), "stale": {"r": 1}}) is None   # a stale view
        new = await PA.check_hr_model(db, _hr_inputs("hrr"))
        assert new["from"] == "lthr" and new["model"] == "hrr" and new["auto"] and seen == [1]
        h = await repo.get(TH.MODEL_HISTORY_KEY)
        assert h[-1]["auto"] and (h[-1]["from"], h[-1]["to"]) == ("lthr", "hrr")
        # a model the user picked (setting hrmax): recorded quietly — no run, no row, no history
        assert await PA.check_hr_model(db, _hr_inputs("hrmax", setting="hrmax")) is None and seen == [1]
        rec = await repo.get(PA.HR_ZONE_KEY)
        assert rec["model"] == "hrmax" and not rec.get("auto") and SG.hr_switch_rows(rec, rec["since"]) == []
        assert len(await repo.get(TH.MODEL_HISTORY_KEY)) == 1
        # 自動調整 off: recorded and told, nothing started
        await repo.set("plan.auto.enabled", False)
        await db.commit()
        await PA.check_hr_model(db, _hr_inputs("lthr", setting="auto"))      # hrmax(locked) → auto: quiet
        assert await PA.check_hr_model(db, _hr_inputs("hrr")) is not None and seen == [1]
    arun(go())


class RowPush:
    """CW.push_sessions stand-in that records the push rows (coros_plan_push) with the workout's
    fingerprint, as the real push does — so 已推送 / 要重推 (status_of) is the real rule."""

    def __init__(self):
        self.calls = []

    async def push(self, db, sessions, thresholds, today, *, stale_keys=(), missed_keys=(), **kw):
        from backend.db.models import CorosPlanPush
        from backend.sync import coros_workouts as CW
        self.calls.append({"sessions": [dict(s) for s in sessions], "thresholds": dict(thresholds or {})})
        rows = await CW.all_rows(db)
        for x in sessions:
            spec = CW.session_workout(x, thresholds, today)
            r = rows.get(x["id"]) or CorosPlanPush(athlete_id=1, session_key=x["id"], week_start=x["week_start"],
                                                   session_id=x["id"])
            r.day, r.fingerprint, r.status, r.provider = x.get("day"), spec.fingerprint, "pushed", "coros"
            db.add(r)
        await db.commit()
        return {"sessions": [{"id": x["id"], "status": "pushed", "changed": True} for x in sessions], "removed": []}

    async def remove(self, db, keys, **kw):
        return []


def test_the_switch_run_resends_the_sessions_on_the_watch(monkeypatch):
    from sqlalchemy import select
    from backend.db.models import PlanChangeLog
    from backend.engine import plan_auto as PA
    from backend.sync import coros_workouts as CW
    from backend.tests.test_coros_workouts import make_db, run as arun
    from backend.tests.test_plan_auto import Box
    monkeypatch.setattr(CW, "real_today", lambda: dt.date(2026, 9, 30))
    b = Box(monkeypatch, _hr_inputs("lthr", easy=(130, 150)), RowPush())

    async def go():
        db = await make_db()
        assert (await PA.run(db))["status"] == "applied"
        assert (await PA.settings(db))["state"]["hr_model"] == "lthr"
        sent = {s["id"] for c in b.push.calls for s in c["sessions"]}
        assert sent and await PA.run(db) and len(b.push.calls) == 1        # nothing new: nothing re-sent
        # the same activities, 自動 switched to 儲備心率 (an easy band 133–154): every easy step on the watch
        # changes, so those sessions go out again — 「已推到手錶的課重推一次」
        b.inp = _hr_inputs("hrr", easy=(133, 154))
        r = await PA.run(db, trigger="hr_model")
        assert r["hr_change"]["old"] == "lthr" and r["hr_change"]["new"] == "hrr"
        assert len(b.push.calls) == 2 and b.push.calls[-1]["thresholds"]["hr_model"]["model"] == "hrr"
        again = {s["id"] for s in b.push.calls[-1]["sessions"]}
        assert again and again <= sent and r["hr_change"]["n"] == len(sent) and r["hr_change"]["pushed"]
        rows = (await db.execute(select(PlanChangeLog).order_by(PlanChangeLog.id))).scalars().all()
        row = next(x for x in rows if x.trigger == "hr_model")
        assert row.summary.startswith("課表心率區間（自動）：乳酸閾值 → 儲備心率") and "已重新推送" in row.summary
        assert (await PA.settings(db))["state"]["hr_model"] == "hrr"
        # the next run with nothing new: nothing logged again (told once)
        n1 = len(rows)
        await PA.run(db)
        rows = (await db.execute(select(PlanChangeLog))).scalars().all()
        assert sum(1 for x in rows if x.trigger == "hr_model") == 1 and len(rows) >= n1
    arun(go())


def test_a_model_the_user_picked_is_no_hr_switch_run(monkeypatch):
    from backend.engine import plan_auto as PA
    from backend.sync import coros_workouts as CW
    from backend.tests.test_coros_workouts import make_db, run as arun
    from backend.tests.test_plan_auto import Box, Push
    monkeypatch.setattr(CW, "real_today", lambda: dt.date(2026, 9, 30))
    b = Box(monkeypatch, _hr_inputs("lthr", setting="lthr"), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = _hr_inputs("hrr", setting="hrr", easy=(133, 154))
        r = await PA.run(db)
        assert "hr_change" not in r and (await PA.settings(db))["state"]["hr_model"] == "hrr"
    arun(go())


def test_the_settings_view_says_auto_and_why(monkeypatch):
    from backend.api import plan as API
    monkeypatch.setattr(HP, "plan_model", lambda user_id=1: "auto")
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    monkeypatch.setattr(HP, "max_hr", lambda ds, day, acc=None, use_account=True: {"value": MHR, "kind": "manual",
                                                                                    "source": "m"})
    monkeypatch.setattr(HP, "rest_hr", lambda ds, day, acc=None, use_account=True: {"value": RHR, "source": "r"})
    monkeypatch.setattr(HP, "effective_model", lambda ds, day=None, setting=None:
                        {"model": "hrr", "setting": "auto", "auto": True, "reason": "LTHR 沒測過", "why": "hrr"})
    import backend.engine.zones as Z
    monkeypatch.setattr(Z, "training_targets", lambda ds, end: {"hr_model": zones("hrr", auto=True)})
    from backend.engine import coros_compare as CC
    monkeypatch.setattr(CC, "view", lambda *a, **k: None)

    class DS:
        today = 20000.0
    v = API.hr_profile_view(DS(), TODAY)
    assert v["model"] == "auto" and [m["id"] for m in v["models"]] == ["auto", "lthr", "hrr", "hrmax"]
    assert v["effective"]["model"] == "hrr" and v["effective"]["reason"] == "LTHR 沒測過"
    assert v["effective"]["label"].startswith("儲備心率")
    from backend.i18n import use_locale
    with use_locale("en"):
        v = API.hr_profile_view(DS(), TODAY)
        assert v["models"][0]["label"] == "Auto (by which numbers are believable)"
