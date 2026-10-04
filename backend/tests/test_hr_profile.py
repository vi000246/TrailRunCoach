"""Max / resting HR and the COROS HR zone models (engine/hr_profile.py,
thresholds.estimate_mhr): optical-spike filtering, the max-HR estimate, the
COROS tables (HRR 202 / 53 → Z2 141–163, LTHR %), where each value comes from
(your setting > the watch > estimate), the 課表 targets and the charts' new
models. Synthetic data only — no WKO5 folder, no real DB, no COROS call."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from backend.engine import hr_profile as HP
from backend.engine.planning import Plan, Threshold
from backend.engine.thresholds import estimate_mhr, peak_sustained_hr
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)

# the user's COROS account (GET /account/query, 2026-10-03), HR part only
ACCOUNT_DATA = {
    "maxHr": 202, "rhr": 53, "hrZoneType": 2,
    "zoneData": {
        "lthr": 182, "maxHr": 202, "rhr": 53,
        "lthrZone": [{"index": i, "ratio": r, "hr": 0} for i, r in enumerate((80.0, 90.0, 95.0, 102.0, 106.0, 255.0))],
        "rhrZone": [{"index": i, "ratio": r, "hr": h} for i, (r, h) in enumerate(
            ((59.0, 141), (74.0, 163), (84.0, 178), (88.0, 184), (95.0, 195), (100.0, 202)))],
        "maxHrZone": [{"index": i, "ratio": r, "hr": h} for i, (r, h) in enumerate(
            ((50.0, 101), (60.0, 121), (70.0, 141), (80.0, 162), (90.0, 182), (100.0, 202)))],
    },
}


def _hr_run(day, peak, hold=30, spikes=(), minutes=40, sport="run"):
    """Easy running at 140, a ramp to `peak` held `hold` s, back to 150; `spikes`:
    (second, value, length) optical spikes jumping straight from the current HR."""
    n = minutes * 60
    hr = np.full(n, 140.0)
    a = n - 600
    hr[a - 120:a] = np.linspace(150, peak, 120)            # a real effort climbs ~0.5 bpm/s
    hr[a:a + hold] = peak
    hr[a + hold:] = 150.0
    for s, v, length in spikes:
        hr[s:s + length] = v
    t = np.arange(n, dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": list(np.full(n, 10.0)),
          "elapseddistance": list(np.arange(n) * 10.0 / 3600.0)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport=sport, tags=["running"],
                       sport_type="running", channels=ch, metrics={"duration": float(n)})


def _ds(runs, plan=None, lthr=160.0):
    ds = FakeDataset(runs, TODAY, settings={"runthr": lthr})
    ds.plan = plan or Plan()
    return ds


# ---------------------------------------------------------------------------
# spikes and the estimate
# ---------------------------------------------------------------------------

def test_spikes_are_dropped_and_a_held_peak_counts():
    t = np.arange(1200, dtype=float)
    hr = np.full(1200, 150.0)
    hr[600:640] = 195.0                       # held 40 s (reached through a ramp below)
    hr[560:600] = np.linspace(151, 195, 40)
    hr[100:103] = 223.0                       # a 3-s spike
    hr[300:310] = 220.0                       # a 10-s jump from 150 to 220: optical
    hr[900:904] = 208.0                       # a 4-s jump, under the 5-s hold anyway
    assert peak_sustained_hr(t, hr) == pytest.approx(195.0)
    # a value above 220 held for a minute (a lock-on) never counts
    hr2 = np.linspace(140, 230, 1200)
    assert peak_sustained_hr(t, hr2) <= 220.0
    assert peak_sustained_hr(t, [None] * 1200) is None


def test_gaps_are_not_bridged():
    t = np.concatenate([np.arange(0, 100, 1.0), np.arange(400, 500, 1.0)])
    hr = np.concatenate([np.full(100, 150.0), np.full(100, 190.0)])
    # 190 is held 100 s after the gap, the interpolated 150→190 ramp inside the gap is not data
    assert peak_sustained_hr(t, hr) == pytest.approx(190.0)


def test_estimate_takes_the_highest_run_and_drops_a_lone_outlier():
    d = TODAY - dt.timedelta(days=10)
    runs = [_hr_run(d - dt.timedelta(days=i * 20), p, spikes=((300, 223.0, 6),))
            for i, p in enumerate((196.0, 199.0, 202.0, 214.0))]
    est = estimate_mhr(_ds(runs), TODAY)
    assert est["n"] == 4
    assert est["value"] == 202 and est["dropped"][1] == 214     # 214 is > 5 above 202: an artefact
    assert "推估" in est["reason"]
    # within 5 bpm of the next: the top one stands
    runs = [_hr_run(d - dt.timedelta(days=i * 20), p) for i, p in enumerate((196.0, 203.0, 206.0))]
    assert estimate_mhr(_ds(runs), TODAY)["value"] == 206


def test_estimate_needs_three_runs_in_365_days():
    runs = [_hr_run(TODAY - dt.timedelta(days=400), 205.0), _hr_run(TODAY, 190.0), _hr_run(TODAY, 192.0)]
    est = estimate_mhr(_ds(runs), TODAY)
    assert est["value"] is None and est["n"] == 2 and "3" in est["reason"]


# ---------------------------------------------------------------------------
# COROS tables
# ---------------------------------------------------------------------------

def test_coros_account_parses_and_hrr_matches_the_watch():
    acc = HP.parse_account(ACCOUNT_DATA)
    assert acc["max_hr"] == 202 and acc["rest_hr"] == 53 and acc["lthr"] == 182
    assert acc["ratios"]["hrr"] == (0.59, 0.74, 0.84, 0.88, 0.95)
    z = HP.zone_rows("hrr", None, 202, 53, acc)
    assert z["rows"][1][2:] == (141.0, 163.0)                # the user's COROS app: Z2 141–163
    assert [r[3] for r in z["rows"][:5]] == [141, 163, 178, 184, 195]   # = rhrZone hr
    zm = HP.zone_rows("hrmax", None, 202, None, acc)
    assert [r[3] for r in zm["rows"][:5]] == [101, 121, 141, 162, 182]  # = maxHrZone hr
    assert HP.parse_account({"zoneData": {}}) is None


def test_coros_lthr_table():
    z = HP.zone_rows("lthr", 160.0)
    assert [(r[0], r[2], r[3]) for r in z["rows"]] == [
        ("Z1", 0.0, 128.0), ("Z2", 128.0, 144.0), ("Z3", 144.0, 152.0), ("Z4", 152.0, 163.0),
        ("Z5", 163.0, 170.0), ("Z6", 170.0, None)]
    assert HP.zone_rows("lthr", None) == {"reason": "沒有 LTHR，區間算不出來"}


def test_missing_rest_or_max_hr_says_where_to_fill_it():
    assert HP.zone_rows("hrr", None, 202, None)["reason"] == HP.NO_REST == "沒有靜息心率，到設定填"
    assert HP.zone_rows("hrr", None, None, 53)["reason"] == HP.NO_MAX
    assert HP.zone_rows("hrmax", None, None)["reason"] == HP.NO_MAX


# ---------------------------------------------------------------------------
# where max / resting HR come from
# ---------------------------------------------------------------------------

def test_resolution_order_setting_then_watch_then_estimate():
    acc = HP.parse_account(ACCOUNT_DATA)
    runs = [_hr_run(dt.date(2026, 8, 31) - dt.timedelta(days=i * 10), p) for i, p in enumerate((198.0, 204.0, 205.0))]
    ds = _ds(runs, Plan(thresholds=[Threshold("2026-09-20", mhr=200), Threshold("2026-10-05", mhr=210)]))
    m = HP.max_hr(ds, TODAY, acc)
    assert (m["value"], m["kind"], m["source"]) == (200, "manual", "你的設定 2026-09-20")   # the later row isn't due yet
    # before your setting: the watch, even when the runs went higher (owner: use the watch's data)
    m = HP.max_hr(ds, dt.date(2026, 9, 15), acc)
    assert (m["value"], m["kind"]) == (202, "coros") and "來自手錶" in m["source"] and "205" in m["source"]
    low = [_hr_run(dt.date(2026, 8, 31) - dt.timedelta(days=i * 10), p) for i, p in enumerate((186.0, 189.0, 191.0))]
    m = HP.max_hr(_ds(low), TODAY, acc)
    assert (m["value"], m["kind"]) == (202, "coros") and "191" in m["source"]
    # no watch value: the estimate
    m = HP.max_hr(ds, dt.date(2026, 9, 15), None, use_account=False)
    assert m["kind"] == "estimate" and m["value"] == 205 and "推估" in m["source"]
    m = HP.max_hr(_ds([]), TODAY, None, use_account=False)
    assert m["value"] is None and HP.NO_MAX in m["reason"]
    # resting HR: your setting > the watch > nothing
    ds = _ds([], Plan(thresholds=[Threshold("2026-09-01", rhr=50)]))
    assert HP.rest_hr(ds, TODAY, acc)["value"] == 50 and HP.rest_hr(ds, TODAY, acc)["kind"] == "manual"
    assert HP.rest_hr(_ds([]), TODAY, acc)["kind"] == "coros"
    r = HP.rest_hr(_ds([]), TODAY, None, use_account=False)
    assert r["value"] is None and r["reason"] == HP.NO_REST


def test_plan_thresholds_keep_rhr(tmp_path):
    p = tmp_path / "plan.json"
    Plan(thresholds=[Threshold("2026-09-20", mhr=203, rhr=52)]).save(p)
    t = Plan.load(p).thresholds[0]
    assert (t.mhr, t.rhr) == (203, 52)


# ---------------------------------------------------------------------------
# A. the 課表 targets
# ---------------------------------------------------------------------------

def test_plan_zones_lthr_default_and_measured_aet_caps():
    z = HP.plan_hr_zones(160.0, 0.89 * 160, False, None, None)
    assert z["model"] == "lthr" and z["fallback"] is None
    assert z["easy"] == [128, 144]                           # COROS Z2 80–90 % LTHR (was ≤ 0.89 × LTHR = 142)
    assert z["work"]["Z3near"] == [152.0, 163.0] and z["work"]["Z5"] == [163.0, 170.0]
    m = HP.plan_hr_zones(160.0, 148.0, True, None, None)      # a measured AeT beats the % formula
    assert m["easy"] == [128, 148] and "AeT" in m["easy_source"]


def test_easy_cap_label_says_aet_only_when_measured():
    z = HP.plan_hr_zones(160.0, 0.89 * 160, False, None, None)
    m = HP.plan_hr_zones(160.0, 148.0, True, None, None)
    assert HP.easy_cap_label(z) == "輕鬆跑上限 144 bpm"                     # the Z2 top, no 「AeT」
    assert HP.easy_cap_label(m) == "輕鬆跑上限 148 bpm（實測 AeT）"
    # a plan's thresholds dict (week_plan): the cap value + hr_model
    assert HP.easy_cap_label({"aet": 148.0, "hr_model": m}) == "輕鬆跑上限 148 bpm（實測 AeT）"
    assert HP.easy_cap_measured({"aet": 150.0, "aet_measured": True}) is True    # no 課表心率區間 (parity)
    assert HP.easy_cap_label(None) == "輕鬆跑上限" and HP.easy_cap_hr(142.0) == "心率 ≤ 輕鬆跑上限 142 bpm"
    assert HP.below(HP.easy_cap_label(None, 142.0)) == "輕鬆跑上限 142 bpm 以下"
    assert HP.below(HP.easy_cap_label(None)) == "輕鬆跑上限以下"


def test_plan_zones_hrr_and_its_fallback():
    z = HP.plan_hr_zones(160.0, None, False, 202, 53, "hrr")
    assert z["model"] == "hrr" and z["easy"] == [141, 163]
    f = HP.plan_hr_zones(160.0, None, False, 202, None, "hrr")
    assert f["model"] == "lthr" and f["requested"] == "hrr" and HP.NO_REST in f["fallback"]
    assert HP.plan_hr_zones(None, None, False, None, None, "hrr") is None


def test_coros_push_and_editor_use_the_plan_zones():
    from backend.engine import workout_steps as WS
    from backend.sync import coros_workouts as CW
    hrz = HP.plan_hr_zones(160.0, None, False, 202, 53, "hrr")
    th = CW.Thresholds.of({"lthr": 160, "aet": 163, "hr_model": hrz})
    assert CW.easy_hr(th) == ("hr", 141, 163)
    assert CW._work_hr({}, th, "Z3near") == ("hr", 178, 184)     # threshold → Z4 (84–88 % HRR)
    c = WS.Ctx.of({"lthr": 160, "aet": 163, "hr_model": hrz})
    assert WS.easy_hr(c) == ("hr", 141, 163) and WS._work_hr(c, {"cls": "Z5"}) == ("hr", 184, 195)
    # without a model: the old % LTHR behaviour
    assert CW.easy_hr(CW.Thresholds.of({"lthr": 160})) == ("hr", 120, 142)


def test_editor_hr_zone_choice_follows_the_plan_model():
    """SP-30: with 課表心率區間 = 儲備心率 the editor's HR 區間 lists that model's Z1–Z6 in bpm
    (the charts' edges), a picked zone pushes those bpm; Friel ids saved before still work."""
    from backend.engine import workout_steps as WS
    hrz = HP.plan_hr_zones(160.0, None, False, 202, 53, "hrr")
    c = WS.Ctx.of({"lthr": 160, "aet": 163, "hr_model": hrz}, "hr")
    hr = WS.zones_table(c)["hr"]
    cur = [z for z in hr if not z.get("legacy")]
    assert [z["id"] for z in cur] == ["aet", "Z1", "Z2", "Z3", "Z4", "Z5", "Z6"]
    texts = {z["id"]: z["text"] for z in cur}
    assert texts["Z2"] == "141–163 bpm" and texts["Z4"] == "178–184 bpm" and texts["Z5"] == "184–195 bpm"
    assert texts["Z6"] == "195–202 bpm" and texts["Z1"] == "121–141 bpm"     # open ends: max HR / 20 bpm
    assert all(z["id"] in {"1", "2", "3", "4", "5a", "5b", "5c"} for z in hr if z.get("legacy"))

    def one(zone):
        return WS.normalize({"items": [{"kind": "work", "dur": {"type": "time", "value": 300},
                                        "target": {"type": "hr", "mode": "zone", "zone": zone}}]})
    d = one("Z6")
    r = WS.resolve(d["items"][0], c)
    assert (r.lo, r.hi, r.intensity, r.err) == (195, 202, ("hr", 195, 202), "")
    assert WS.steps_to_coros(d, c)                     # pushes
    assert WS._work_band(one("Z4")["items"][0], c) is not None
    # a step saved with a Friel id keeps resolving as % LTHR
    r = WS.resolve(one("5b")["items"][0], c)
    assert (r.lo, r.hi) == (round(1.03 * 160), round(1.06 * 160))
    # the LTHR model (default): COROS % LTHR edges, Z6 open top 1.10 × LTHR
    lz = WS.Ctx.of({"lthr": 160, "hr_model": HP.plan_hr_zones(160.0, None, False, None, None)}, "hr")
    lt = {z["id"]: z["text"] for z in WS.zones_table(lz)["hr"] if not z.get("legacy")}
    assert lt["Z4"] == "152–163 bpm" and lt["Z6"] == "170–176 bpm"
    # no 課表心率區間: Friel only (as before), and a model zone id can't resolve
    plain = WS.Ctx.of({"lthr": 160}, "hr")
    assert [z["id"] for z in WS.zones_table(plain)["hr"]][:2] == ["aet", "1"]
    assert WS.resolve(one("Z4")["items"][0], plain).err


def test_training_targets_follow_the_setting_and_hint_the_drift_hr(monkeypatch):
    from backend.engine.zones import training_targets
    from backend.engine.wko5expr.dataset import date_to_day
    runs = [_hr_run(TODAY - dt.timedelta(days=i * 10), p) for i, p in enumerate((198.0, 204.0, 205.0))]
    ds = _ds(runs, Plan(thresholds=[Threshold("2026-09-01", rhr=55)]))
    ds.aethr = lambda w: None
    ds.cp = lambda w: None
    ds.settings_from = "app"
    ds.setting_label = lambda name, default="": "設定"
    end = int(date_to_day(TODAY))
    tt = training_targets(ds, end, aet_below=162)
    assert "飄移 < 5%" in tt["aet_source"] and "162" in tt["aet_source"]
    assert tt["hr_model"]["model"] == "lthr" and tt["easy_cap"]["value"] == 144
    z2 = next(r for r in tt["rows"] if r["id"] == "z2")
    assert z2["hr"] == [None, 144]
    monkeypatch.setattr(HP, "plan_model", lambda user_id=1: "hrr")
    tt = training_targets(ds, end)
    assert tt["hr_model"]["model"] == "hrr"                     # max 205 (推估), rest 55 (你的設定)
    assert tt["easy_cap"]["value"] == round(55 + 0.74 * 150)


# ---------------------------------------------------------------------------
# B. charts
# ---------------------------------------------------------------------------

def test_activity_and_period_zones_with_the_watch_values(monkeypatch):
    from backend.engine.panels import activity_charts as A
    from backend.engine.panels import period_zones as PZ
    from backend.engine.wko5expr.dataset import date_to_day
    monkeypatch.setattr(HP, "account", lambda user_id=1: HP.parse_account(ACCOUNT_DATA))
    hr = np.repeat([130.0, 150.0, 170.0], 600)
    t = np.arange(len(hr), dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": list(np.full(len(t), 10.0)),
          "elapseddistance": list(t * 10.0 / 3600.0)}
    w = FakeWorkout(start=dt.datetime.combine(TODAY, dt.time(7)), sport="run", tags=["running"],
                    sport_type="running", channels=ch, metrics={"duration": float(t[-1])})
    ds = _ds([w])
    res = A.zone_times(ds, ds.workouts[0], "hr")
    hrr = next(m for m in res["models"] if m["id"] == "rqhrr")
    assert hrr["available"] and hrr["rows"][1]["from"] == 141 and hrr["rows"][1]["to"] == 163
    assert [r["seconds"] for r in hrr["rows"][:3]] == [600.0, 600.0, 600.0]
    assert "來自手錶" in hrr["basis_text"]
    hm = next(m for m in res["models"] if m["id"] == "coroshrmax")
    assert hm["available"] and hm["rows"][3]["from"] == 141       # Z4 70–80 % of 202
    out = PZ.compute(ds, date_to_day(dt.date(2026, 9, 1)), date_to_day(TODAY),
                     {"zperiod": "range", "zkind": "hr", "zmodel": "rqhrr"})
    assert out["model"]["id"] == "rqhrr" and out["total_s"] == len(hr)
    assert out["rows"][1]["pct"] == "59–74% HRR" and out["rows"][1]["range"] == "141–163 bpm"


def test_zone_table_bpm_models(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.engine.zones import SYSTEMS, zone_table
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    (d / "000.fit").write_bytes(build_run(start=datetime(2026, 9, 28, 0, tzinfo=timezone.utc), seconds=1800,
                                          power=180, stryd=True))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=TODAY,
                          estimate_thresholds=False, tz=timezone.utc)
    ds.plan = Plan()
    end = int(date_to_day(TODAY))
    assert {"coroslthr", "coroshrr", "coroshrmax"} <= set(SYSTEMS)
    z = zone_table(ds, "coroshrr", end)
    assert z["no_data_reason"] == HP.NO_MAX and z["rows"][0]["from"] is None     # one run: no estimate
    ds.plan = Plan(thresholds=[Threshold("2026-09-01", mhr=200)])
    assert zone_table(ds, "coroshrr", end)["no_data_reason"] == HP.NO_REST
    ds.plan = Plan()
    monkeypatch.setattr(HP, "account", lambda user_id=1: HP.parse_account(ACCOUNT_DATA))
    z = zone_table(ds, "coroshrr", end)
    assert z["threshold"] == 149 and z["rows"][1]["from"] == 141 and z["rows"][1]["to"] == 163
    assert "來自手錶" in z["threshold_source"] and [c["id"] for c in z["choices"]][0] == "frielhr"


def test_zones_card_title_follows_the_chosen_model(tmp_path):
    """SP-40: 「區間與課表強度」's HR card switched with ?zsys= is titled after the chosen
    model, not the view's fixed 「Friel 心率區間」; the default model and other tables keep it."""
    from datetime import datetime, timezone
    from backend.api.wko5views import _render
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.customviews import REPO_VIEWS, load_custom_views
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.engine.zones import SYSTEMS
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    (d / "000.fit").write_bytes(build_run(start=datetime(2026, 9, 28, 0, tzinfo=timezone.utc), seconds=1800,
                                          power=180, stryd=True))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=TODAY,
                          estimate_thresholds=False, tz=timezone.utc)
    ds.plan = Plan()
    charts = {c.get("id"): c for v in load_custom_views([REPO_VIEWS]).values() if not v.get("error")
              for dash in v["dashboards"] for c in dash["charts"]}
    hr, power = charts["friel-hr-zones"], charts["palladino-power-zones"]
    end = date_to_day(TODAY)
    res = _render(hr, ds, end - 30, end, None, None, params={"zsys": "coroslthr"})
    assert res["title"] == "COROS 乳酸閾心率區間（跑步）" == res["zones"]["title"]
    assert "Friel" not in res["title"]
    res = _render(hr, ds, end - 30, end, None, None, params={"zsys": "classichr"})
    assert res["title"] == SYSTEMS["classichr"]["title"]
    for params in ({}, {"zsys": "frielhr"}, {"zsys": "nope"}):      # default model: the view's title
        assert _render(hr, ds, end - 30, end, None, None, params=params)["title"] == hr["title"]
    assert _render(power, ds, end - 30, end, None, None, params={"zsys": "coroslthr"})["title"] == power["title"]
