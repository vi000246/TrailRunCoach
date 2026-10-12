"""
SP-383: the structure editor's 區間 mode picks a run of zones (從–到, e.g. Palladino 1B–2), stored
{"mode": "zone", "zone": "1B", "zone_to": "2"} and resolved from the first zone's lower bound to the
last one's upper bound — power, HR (the 課表心率區間 in use, else Friel) and pace alike. One-zone
steps and stored structures stay as they were. Synthetic thresholds only: no DB, no WKO5 folder,
no network, nothing pushed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import hr_profile as HP
from backend.engine import workout_steps as WS
from backend.engine import workout_templates as WTPL
from backend.sync import coros_workouts as CW

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
TH = {"cp": 200.0, "lthr": 160.0, "aet": 150.0, "tpace": 280.0}


def _doc(*targets, sec=600):
    return {"v": 1, "origin": "user", "items": [
        {"id": f"s{i}", "kind": "work", "dur": {"type": "time", "value": sec}, "target": t, "note": ""}
        for i, t in enumerate(targets)]}


def _tg(ty, zone, zone_to=None):
    t = {"type": ty, "mode": "zone", "zone": zone}
    if zone_to is not None:
        t["zone_to"] = zone_to
    return t


def _norm_target(t):
    return WS.normalize(_doc(t))["items"][0]["target"]


# ---------------------------------------------------------------------------
# the stored form
# ---------------------------------------------------------------------------

def test_normalize_keeps_a_range_and_one_zone_steps_unchanged():
    assert _norm_target(_tg("power", "1B", "2")) == {"type": "power", "mode": "zone", "zone": "1B", "zone_to": "2"}
    assert _norm_target(_tg("pace", "2", "4")) == {"type": "pace", "mode": "zone", "zone": "2", "zone_to": "4"}
    assert _norm_target(_tg("hr", "2", "3")) == {"type": "hr", "mode": "zone", "zone": "2", "zone_to": "3"}
    assert _norm_target(_tg("hr", "Z2", "Z4"))["zone_to"] == "Z4"
    # a one-zone step (and every structure saved before SP-383) comes out exactly as it went in
    for t in (_tg("power", "3A"), _tg("hr", "aet"), _tg("pace", "4"), _tg("hr", "Z3")):
        assert _norm_target(t) == t
    # 「到」 = 「從」, or empty: one zone
    assert _norm_target(_tg("power", "3A", "3A")) == _tg("power", "3A")
    assert _norm_target(_tg("power", "3A", "")) == _tg("power", "3A")


def test_a_reversed_range_is_swapped():
    """「到」 below 「從」 is swapped (the easier zone first), not refused."""
    assert _norm_target(_tg("power", "2", "1B")) == _tg("power", "1B", "2")
    assert _norm_target(_tg("hr", "Z4", "Z2")) == _tg("hr", "Z2", "Z4")
    assert _norm_target(_tg("pace", "5a", "3")) == _tg("pace", "3", "5a")       # listed easy → hard


@pytest.mark.parametrize("t,msg", [
    (_tg("power", "1B", "9"), "沒有這個區間：'9'"),
    (_tg("hr", "aet", "2"), "這兩格不能當一段區間：aet–2"),            # the easy-run cap is not a zone of a set
    (_tg("hr", "2", "aet"), "這兩格不能當一段區間：2–aet"),
    (_tg("hr", "Z2", "3"), "這兩格不能當一段區間：Z2–3"),              # 課表心率區間 + Friel: two sets
    (_tg("pace", "2", "3A"), "沒有這個區間：'3A'"),                   # a power id on pace
])
def test_a_range_across_sets_or_unknown_is_refused(t, msg):
    with pytest.raises(WS.StepsError) as e:
        WS.normalize(_doc(t))
    assert msg in e.value.errors


# ---------------------------------------------------------------------------
# resolve: the page, the watch, the TSS
# ---------------------------------------------------------------------------

def test_power_range_is_the_first_zones_lower_to_the_last_zones_upper_bound():
    c = WS.Ctx.of(TH, "power")
    r = WS.resolve(WS.normalize(_doc(_tg("power", "1B", "2")))["items"][0], c)
    assert (r.type, r.lo, r.hi, r.intensity, r.err) == ("power", 130, 176, ("power", 130, 176), "")
    assert r.text == "130–176 W" and r.sub == "65–88% CP · Z1B–Z2"            # readable as a range
    assert r.frac == pytest.approx((130 + 176) / 2 / 200)
    # one zone: as before (the middle's zone in the sub)
    one = WS.resolve(WS.normalize(_doc(_tg("power", "1C")))["items"][0], c)
    assert (one.lo, one.hi, one.sub) == (150, 160, "75–80% CP · Z1C")
    # Zone 6–7: Zone 7's open top closed as for one zone (1.8 × CP)
    r = WS.resolve(WS.normalize(_doc(_tg("power", "6", "7")))["items"][0], c)
    assert (r.lo, r.hi) == (round(1.16 * 200), round(1.8 * 200))


def test_hr_range_follows_the_chosen_hr_zone_set():
    """課表心率區間 (設定): the range is in that set's bpm; another set gives other bpm for the same ids."""
    hrr = WS.Ctx.of({**TH, "hr_model": HP.plan_hr_zones(160.0, None, False, 202, 53, "hrr")}, "hr")
    lthr = WS.Ctx.of({**TH, "hr_model": HP.plan_hr_zones(160.0, None, False, None, None)}, "hr")
    d = WS.normalize(_doc(_tg("hr", "Z2", "Z4")))
    r = WS.resolve(d["items"][0], hrr)
    assert (r.lo, r.hi, r.intensity, r.err) == (141, 184, ("hr", 141, 184), "")       # Z2 141–163 … Z4 178–184
    assert r.sub.endswith(" · Z2–Z4")
    r = WS.resolve(d["items"][0], lthr)
    assert (r.lo, r.hi) == (128, 163)                                                   # Z2 128–144 … Z4 152–163
    # no 課表心率區間: a model range can't resolve (as a model zone alone)
    assert WS.resolve(d["items"][0], WS.Ctx.of(TH, "hr")).err
    # Friel ids (no 課表心率區間, or a step saved before): × LTHR
    r = WS.resolve(WS.normalize(_doc(_tg("hr", "2", "3")))["items"][0], WS.Ctx.of(TH, "hr"))
    assert (r.lo, r.hi) == (round(0.85 * 160), round(0.95 * 160)) and r.sub == "85–95% LTHR · Z2–Z3"


def test_pace_range_spans_both_zones():
    c = WS.Ctx.of(TH, "hr")
    r = WS.resolve(WS.normalize(_doc(_tg("pace", "2", "4")))["items"][0], c)
    # Friel pace Zone 2 (114–129 % of threshold pace) … Zone 4 (100–106 %): 100–129 %, faster bound first
    assert r.type == "pace" and r.intensity == ("pace", 280, round(1.29 * 280))
    assert r.text == "4:40–6:01 /km" and r.sub == "100–129% 閾值配速（推估） · Z2–Z4"
    # without a threshold pace: the same warning as one pace zone
    r = WS.resolve(WS.normalize(_doc(_tg("pace", "2", "4")))["items"][0], WS.Ctx.of({"cp": 200.0, "lthr": 160.0}, "hr"))
    assert r.type == "none" and r.need == "tpace"


def test_the_watch_gets_the_whole_range():
    """推到手錶: the COROS payload carries the first zone's lower and the last zone's upper bound."""
    th = CW.Thresholds.of({**TH, "hr_model": HP.plan_hr_zones(160.0, None, False, 202, 53, "hrr")})
    s = {"kind": "easy", "title": "x", "minutes": 30, "day": "2026-10-13",
         "steps": _doc(_tg("power", "1B", "2"), _tg("hr", "Z2", "Z4"), _tg("pace", "4", "2"), _tg("power", "3A"))}
    steps = CW.session_steps(s, th)
    assert [x.intensity for x in steps] == [("power", 130, 176), ("hr", 141, 184), ("pace", 280, 361),
                                            ("power", 176, 190)]            # the one-zone step as before
    ex = CW.build_program("TRC x", steps, th)["exercises"]
    assert [(e["intensityType"], e["intensityValue"], e["intensityValueExtend"]) for e in ex] == \
        [(CW.INT_POWER, 130, 176), (CW.INT_HR, 141, 184), (CW.INT_PACE, 280, 361), (CW.INT_POWER, 176, 190)]
    pv = WS.watch_preview(WS.normalize(s["steps"]), WS.Ctx.of({**TH, "hr_model": th.hrz}, "hr"))
    assert [x["target"] for x in pv["lines"]] == ["功率 130–176 W", "心率 141–184 bpm", "配速 4:40–6:01 /km",
                                                  "功率 176–190 W"]


def test_totals_chart_and_summary_use_the_range():
    c = WS.Ctx.of(TH, "power")
    d = WS.normalize(_doc(_tg("power", "1B", "2")))
    v = WS.view(d, c)
    f = (130 + 176) / 2 / 200
    assert v["order"][0]["frac"] == pytest.approx(f) and v["order"][0]["level"] == WS.level(f)
    assert v["totals"]["tss"] == round(600 * f * f * 100 / 3600, 1)
    assert v["resolved"]["s0"]["sub"] == "65–88% CP · Z1B–Z2"
    assert v["summary"] == "功率 130–176 W（65–88% CP）"                # the calendar chip


def test_easy_step_progression_band_and_family_read_the_range():
    # an easy range (its harder end ≤ Zone 2) is an easy step a warm-up may come out of
    assert WS._easy_target(_tg("hr", "1", "2")) and WS._easy_target(_tg("pace", "1", "2"))
    assert not WS._easy_target(_tg("hr", "2", "3")) and not WS._easy_target(_tg("power", "2", "3A"))
    # the ladder's band (variant_from_steps) and the 範本 family: the span, not the first zone
    c = WS.Ctx.of(TH, "power")
    st = WS.normalize(_doc(_tg("power", "3A", "3B")))["items"][0]
    assert WS._work_band(st, c) == (0.88, 1.01, False)
    hr = WS.normalize(_doc(_tg("hr", "4", "5b")))["items"][0]                      # 95–106 % LTHR, middle 100.5 %
    assert WS._work_band(hr, c) == (*WS.HR_CLASS_BAND["Z4"], True)
    assert WS._work_band(WS.normalize(_doc(_tg("hr", "4")))["items"][0], c) == (*WS.HR_CLASS_BAND["Z3near"], True)
    assert WTPL._level(_tg("power", "3A", "3B")) == "thr" and WTPL._level(_tg("power", "1B", "2")) == "easy"
    assert WTPL._level(_tg("power", "3B", "5")) == "above"


# ---------------------------------------------------------------------------
# the editor (static/workout_editor.js)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_editor_zone_range_picker():
    js = r"""
const vm = require("vm"), fs = require("fs"), w = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { window: w, document: {} });
const E = w.WorkoutEditor;
const P = ["1A", "1B", "1C", "2", "3A", "3B", "4", "5", "6", "7"].map((id) => ({ id, label: "Z" + id, lo: 0.5, hi: 0.6, text: "" }));
const H = [{ id: "aet", label: "≤ 輕鬆跑上限", text: "" }].concat(["Z1", "Z2", "Z3", "Z4", "Z5", "Z6"].map((id) => ({ id, label: id, text: "" })))
  .concat(["1", "2", "3", "4", "5a", "5b", "5c"].map((id) => ({ id, label: "Friel Z" + id, legacy: true, text: "" })));
const ctx = { thresholds: { cp: 200, lthr: 160 }, zones: { power: P, hr: H } };
const pw = (zone, zone_to) => ({ type: "power", mode: "zone", zone, ...(zone_to ? { zone_to } : {}) });
const hr = (zone, zone_to) => ({ type: "hr", mode: "zone", zone, ...(zone_to ? { zone_to } : {}) });
const st = (t) => ({ id: "a", kind: "work", dur: { type: "time", value: 600 }, target: t });
console.log(JSON.stringify({
  range: E.targetHtml(ctx, st(pw("1B", "2")), null),
  one: E.targetHtml(ctx, st(pw("3A")), null),
  aet: E.targetHtml(ctx, st(hr("aet")), null),
  model: E.targetHtml(ctx, st(hr("Z2", "Z4")), null),
  to: E.zoneEdit(pw("1B"), "tzone2", "2", P),
  swap: E.zoneEdit(pw("1B", "2"), "tzone2", "1A", P),
  fromUp: E.zoneEdit(pw("1B", "2"), "tzone", "3A", P),
  same: E.zoneEdit(pw("1B", "2"), "tzone2", "1B", P),
  none: E.zoneEdit(pw("1B", "2"), "tzone2", "", P),
  toAet: E.zoneEdit(hr("Z2", "Z4"), "tzone", "aet", H),
  toLegacy: E.zoneEdit(hr("Z2", "Z4"), "tzone", "3", H),
  span: E.zoneSpan([{ id: "2", lo: 1.14, hi: 1.29 }, { id: "3", lo: 1.06, hi: 1.14 }, { id: "4", lo: 1.0, hi: 1.06 }], { zone: "2", zone_to: "4" }),
}));
"""
    r = subprocess.run([NODE, "-e", js, str(ROOT / "static" / "workout_editor.js")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    g = json.loads(r.stdout)
    h = g["range"]
    # 「從」 and 「到」: the second select lists the same set, its value the range's last zone
    assert 'data-f="tzone"' in h and 'data-f="tzone2"' in h
    to = h.split('data-f="tzone2"')[1].split("</select>")[0]
    assert 'value="2" selected' in to and 'value=""' in to and 'value="7"' in to
    one = g["one"].split('data-f="tzone2"')[1].split("</select>")[0]
    assert 'value="" selected' in one                                          # one zone: 「只這一格」
    assert 'data-f="tzone2"' not in g["aet"]                                   # the easy-run cap has no range
    mto = g["model"].split('data-f="tzone2"')[1].split("</select>")[0]
    assert 'value="Z4" selected' in mto and 'value="aet"' not in mto and 'value="5a"' not in mto
    assert g["to"] == {"type": "power", "mode": "zone", "zone": "1B", "zone_to": "2"}
    assert g["swap"] == {"type": "power", "mode": "zone", "zone": "1A", "zone_to": "1B"}     # 到 below 從: swapped
    assert g["fromUp"] == {"type": "power", "mode": "zone", "zone": "2", "zone_to": "3A"}
    assert g["same"] == {"type": "power", "mode": "zone", "zone": "1B"}
    assert g["none"] == {"type": "power", "mode": "zone", "zone": "1B"}
    assert g["toAet"] == {"type": "hr", "mode": "zone", "zone": "aet"}
    assert g["toLegacy"] == {"type": "hr", "mode": "zone", "zone": "3"}                      # another set: one zone
    assert g["span"] == {"lo": 1.0, "hi": 1.29}
