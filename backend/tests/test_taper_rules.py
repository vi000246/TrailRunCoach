"""
SP-96: the 減量期 by event (14 days, 7 before a 2–3 day 百岳, up to 21 by 課表偏好 for a road
marathon / an ultra), the run count kept, the race-week rules (overview.taper_rules), the climb
following the minutes, and the strength stop / phases following the real taper. Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import planning as P
from backend.engine import projection as PJ
from backend.engine.planning import Event
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30


def ev(**kw):
    a = dict(id="e", name="測試", date="2027-03-06", kind="race", priority="A")
    a.update(kw)
    return Event(**a)


# ---- length ---------------------------------------------------------------------------------

@pytest.mark.parametrize("kw, pref, days", [
    (dict(kind="baiyue", days=2), None, 7), (dict(kind="baiyue", days=3), 21, 7),
    (dict(kind="baiyue", days=5), 21, 10),                                       # longer trips: 10 (SP-114)
    (dict(kind="baiyue", days=1), 21, 14),                                       # 單攻: the default
    (dict(kind="road", distance_km=42.2, est_hours=4.0), 21, 21),                # road marathon
    (dict(kind="road", distance_km=21.1, est_hours=1.9), 21, 14),                # half: the default
    (dict(kind="race", distance_km=50, climbing_m=3000, est_hours=9.0), 18, 18),  # ultra
    (dict(kind="race", distance_km=25, climbing_m=1000, est_hours=3.5), 21, 14),
    (dict(kind="road", distance_km=42.2, est_hours=4.0), 30, 21),                # capped at 21
    (dict(kind="road", distance_km=42.2, est_hours=4.0), None, 14),
])
def test_taper_days_by_event(kw, pref, days):
    assert P.taper_days(ev(**kw), pref) == days


def test_phases_follow_the_taper_length():
    b, e = date(2026, 6, 1), date(2027, 12, 31)
    trip = ev(kind="baiyue", days=3, distance_km=30, climbing_m=3000)
    ph = P.auto_phases([trip], b, e, 0)
    tap = next(p for p in ph if p.kind == "taper")
    assert (tap.start, tap.end) == ("2027-02-27", "2027-03-05")
    sp = next(p for p in ph if p.kind == "specific")
    assert sp.end == "2027-02-26" and P._d(sp.start) == date(2027, 2, 27) - dt.timedelta(weeks=P.SPECIFIC_WEEKS)
    mar = ev(kind="road", distance_km=42.2, est_hours=4.0)
    tap = next(p for p in P.auto_phases([mar], b, e, 0, 21) if p.kind == "taper")
    assert tap.start == "2027-02-13"
    assert P.taper_start(P.auto_phases([mar], b, e, 0, 21), mar) == date(2027, 2, 13)
    assert P.taper_start([], mar, 21) == date(2027, 2, 13) and P.taper_start([], trip) == date(2027, 2, 27)


def test_close_races_compare_with_the_events_own_taper():
    """SP-90's 「減量期縮短」 note reads the event's full taper (7 for a 百岳), not TAPER_DAYS."""
    b, e = date(2026, 6, 1), date(2027, 12, 31)
    r1 = ev(id="r1", name="r1", distance_km=21, climbing_m=0, est_hours=2.5)
    trip = ev(id="t", name="t", date="2027-03-20", kind="baiyue", days=2, distance_km=20, climbing_m=2000)
    ph = P.auto_phases([r1, trip], b, e, 0)
    tap = next(p for p in ph if p.kind == "taper" and p.event_id == "t")
    assert (P._d(tap.end) - P._d(tap.start)).days + 1 == 7 and "減量期縮短" not in tap.note


def test_strength_stop_follows_the_taper():
    trip = ev(kind="baiyue", days=2, distance_km=20, climbing_m=2000)
    st = O.strength_stops([trip], date(2027, 1, 1))
    assert st[0]["from"] == "2027-02-27" and st[0]["days"] == 7
    ph = P.auto_phases([ev(kind="road", distance_km=42.2, est_hours=4.0)], date(2026, 6, 1), date(2027, 12, 31), 0, 21)
    st = O.strength_stops([ev(kind="road", distance_km=42.2, est_hours=4.0)], date(2027, 1, 1), ph)
    assert st[0]["from"] == "2027-02-13" and st[0]["days"] == 21


def test_prefs_taper_days_is_a_phase_setting():
    assert PP.Prefs(taper_days=21).active is False                      # not shaping
    PP.check(PP.Prefs(taper_days=21))
    with pytest.raises(ValueError):
        PP.check(PP.Prefs(taper_days=28))
    assert PP.from_settings({"plan.prefs.taper_days": 21}).taper_days == 21


# ---- run count ------------------------------------------------------------------------------

def test_taper_keeps_the_run_count():
    # 150 min left: easy_count gives 3 (÷ 50); with 6 runs before the taper and 1 quality → 4 easy
    assert O.easy_count(150, "taper") == 3
    assert O.taper_easy_count(150, 6.0, 1) == 4
    assert O.taper_easy_count(150, None, 1) == 3
    assert O.taper_easy_count(50, 6.0, 1) == 2                          # each ≥ 20 min
    assert O.taper_easy_count(400, 3.0, 1) == O.easy_count(400, "taper")  # never fewer than before


# ---- race-week rules ------------------------------------------------------------------------

RACE = "2026-10-17"          # Sat; taper 10/03–10/16


def tc(sore=False, trail=True):
    return {"race": "r", "id": "e", "start": RACE, "taper_start": "2026-10-03", "days": 14, "trail": trail,
            "sore": sore, "long_days": 14 if sore else 7}


def s(id_, kind, day, minutes=50, **kw):
    return {"id": id_, "kind": kind, "day": day, "minutes": minutes, "title": "x", "tss": 50.0, "done": False,
            "detail": "", "source": "", "terrain": None, **kw}


def test_quality_by_days_out():
    ss = [s("quality", "quality", "2026-10-06", variant_key="z5_x"), s("easy1", "easy", "2026-10-07")]
    notes = []
    O.taper_rules(ss, tc(), date(2026, 10, 5), notes)
    assert ss[0]["title"] == O.TAPER_FULL["trail"]["title"] and ss[0]["variant_key"] is None   # 11 days out
    assert notes and notes[0]["src"] == "taper"
    ss = [s("quality", "quality", "2026-10-09")]                                               # 8 days out
    O.taper_rules(ss, tc(trail=False), date(2026, 10, 5), road=True)
    assert ss[0]["title"] == O.TAPER_CUT["road"]["title"]
    ss = [s("quality", "quality", "2026-10-13")]                                               # 4 days out
    O.taper_rules(ss, tc(), date(2026, 10, 12))
    assert ss[0]["title"] == O.TAPER_SHORT["title"] and ss[0]["minutes"] == O.TAPER_SHORT["minutes"]


def test_race_week_quality_moves_into_3_to_5_days_out_or_becomes_easy():
    ss = [s("quality", "quality", "2026-10-16"), s("easy1", "easy", "2026-10-13")]
    O.taper_rules(ss, tc(), date(2026, 10, 12))
    assert ss[0]["day"] == "2026-10-13" and ss[0]["title"] == O.TAPER_SHORT["title"]
    assert ss[1]["day"] == "2026-10-16"
    ss = [s("quality", "quality", "2026-10-16"), s("easy1", "easy", "2026-10-11")]
    notes = []
    O.taper_rules(ss, tc(), date(2026, 10, 12), notes)
    assert ss[0]["kind"] == "easy" and ss[0]["minutes"] <= 40 and "賽前 3 天內不排強度課" in notes[0]["text"]


def test_last_long_run():
    ss = [s("long", "long", "2026-10-10", 150)]                                                # 7 days out
    O.taper_rules(ss, tc(), date(2026, 10, 5))
    assert ss[0]["kind"] == "long" and ss[0]["minutes"] == O.TAPER_LONG_MAX_MIN
    ss = [s("long", "long", "2026-10-11", 150)]                                                # 6 days out
    O.taper_rules(ss, tc(), date(2026, 10, 5))
    assert ss[0]["kind"] == "easy" and ss[0]["minutes"] <= 60
    # sore (a long downhill race): ≥ 14 days out — 10/03 is the last day it may be
    ss = [s("long", "long", "2026-10-04", 150)]                                                # 13 days out
    O.taper_rules(ss, tc(sore=True), date(2026, 9, 28))
    assert ss[0]["kind"] == "easy"
    ss = [s("long", "long", "2026-10-03", 150)]                                                # 14 days out
    O.taper_rules(ss, tc(sore=True), date(2026, 9, 28))
    assert ss[0]["kind"] == "long" and ss[0]["minutes"] == 90 and ss[0]["terrain"] == "road"


def test_no_long_climb_repeats_and_untouched_outside_the_taper():
    ss = [s("climb", "easy", "2026-10-05", 70, climb_m=600), s("long", "long", "2026-10-02", 150)]
    O.taper_rules(ss, tc(), date(2026, 9, 28))
    assert ss[0]["climb_m"] is None and "不排長爬坡反覆" in ss[0]["detail"]
    assert ss[1]["minutes"] == 150                                                             # before the taper
    assert O.taper_rules(ss, None, date(2026, 9, 28)) is ss



def test_runs_made_easy_take_the_easy_target():
    """SP-408: a long run / quality / 長爬坡反覆 the taper turns into 輕鬆跑 carries the easy run's target
    (the long band 169–229 W stayed on a 40-minute easy run whose structure and watch were 169–208 W)."""
    easy = "心率 < 150 bpm · 功率 169–208 W"
    ss = [s("long", "long", "2026-10-11", 150, target="心率 < 150 bpm · 功率 169–229 W", source="Koop"),
          s("quality", "quality", "2026-10-16", target="功率 255–263 W（98–101% CP）"),
          s("climb", "easy", "2026-10-06", 70, target="心率約輕鬆跑上限 150 bpm（實測 AeT）")]
    O.taper_rules(ss, tc(), date(2026, 10, 5), easy_target=easy)
    assert [x["kind"] for x in ss] == ["easy"] * 3
    assert all(x["target"] == easy and x["source"] == "" for x in ss)
    ss = [s("long", "long", "2026-10-11", 150, target="心率 < 150 bpm · 功率 169–229 W")]
    O.taper_rules(ss, tc(), date(2026, 10, 5))
    assert ss[0]["target"] == ""

def test_climb_note_follows_the_minutes():
    n = O.taper_climb_note(tc(), 1500.0, 0.5)
    assert n["src"] == "taper" and "750 m" in n["text"] and "50%" in n["text"]
    assert O.taper_climb_note(tc(trail=False), 1500.0, 0.5) is None


def test_taper_context_and_sore():
    e = Event("e1", "越野賽", RACE, kind="race", priority="A", distance_km=30, climbing_m=2000, est_hours=5.0)
    c = O.taper_context([], [e], date(2026, 10, 5))
    assert c["taper_start"] == "2026-10-03" and c["sore"] and c["long_days"] == 14
    assert O.taper_context([], [e], date(2026, 9, 21)) is None
    flat = Event("e2", "城市馬", RACE, kind="road", priority="A", distance_km=42.2, climbing_m=50, est_hours=4.0)
    c = O.taper_context([], [flat], date(2026, 10, 5))
    assert not c["trail"] and not c["sore"] and c["long_days"] == 7


# ---- week_plan + projection -----------------------------------------------------------------

def _week(ev_day, prefs=None):
    ds = _history(TODAY)
    plan = _plan_with("2027-06-01", 1, TODAY)
    plan.events = [Event("e1", "越野賽", ev_day, kind="race", priority="A", distance_km=30, climbing_m=2000,
                         est_hours=5.0)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return ds, plan, O.week_plan(ds, st, TODAY, prefs=prefs)


def test_week_plan_and_projection_in_the_taper():
    # A race Sat 10/10: this week (9/28–10/4) is the taper's first week (from 9/26)
    ds, plan, wp = _week("2026-10-10")
    assert wp["phase"] == "taper" and wp["taper"]["id"] == "e1"
    runs = [x for x in wp["sessions"] if x["kind"] in O.RUN_KINDS]
    pre = wp["taper"].get("pre_runs")
    if pre:
        assert len(runs) >= min(round(pre) - 1, int(wp["target"]["hours"] * 60 // O.TAPER_RUN_MIN))
    for q in (x for x in wp["sessions"] if x["kind"] == "quality" and x["day"] and not x["done"]):
        out = (date(2026, 10, 10) - date.fromisoformat(q["day"])).days
        assert out >= 3 and ("上坡" in q["title"] or "短強度" in q["title"])
    assert any(n.get("src") == "taper" for n in wp["notes"])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18), events=plan.events)
    race_wk = next(w for w in weeks if w["start"] == "2026-10-05")
    for x in race_wk["sessions"]:
        out = (date(2026, 10, 10) - date.fromisoformat(x["day"])).days
        if x["kind"] == "quality":
            assert 3 <= out <= 5 and x["title"] == O.TAPER_SHORT["title"]
        assert x["kind"] != "long"                                   # sore race: no long run in 14 days
    assert any(n.get("src") == "taper" for n in race_wk.get("notes") or [])


# ---- SP-408: the 減量期 quality session takes its template's target -------------------------------

TH_TARGET = "功率 247–263 W · 心率 160–168 bpm"        # the week's threshold row (tgt["threshold"]), CP 260
Z3_TARGET = "心率 150–159 bpm"                         # the week's Zone 3 row (z3_hr_text), the 越野 88–95 % CP ones
OLD_TARGET = "功率 276–302 W（106–116% CP） · 心率 175–182 bpm"   # the session's own before the taper (VO2)


def _taper_q(day, monday, road=False, **kw):
    ss = [s("quality", "quality", day, target=OLD_TARGET, source="Seiler")]
    O.taper_rules(ss, tc(trail=not road), monday, road=road, cp=260.0, q_target=TH_TARGET, z3_target=Z3_TARGET,
                  **kw)
    return ss[0]


@pytest.mark.parametrize("day, monday, road, tmpl, text", [
    # SP-408 (the user 2026-10-13): the 越野 88–95 % CP ones take the Zone 3 row's HR, not the threshold row's
    ("2026-10-06", date(2026, 10, 5), False, ("full", "trail"), "功率 229–247 W（88–95% CP） · 心率 150–159 bpm"),
    ("2026-10-09", date(2026, 10, 5), False, ("cut", "trail"), "功率 229–247 W（88–95% CP） · 心率 150–159 bpm"),
    ("2026-10-06", date(2026, 10, 5), True, ("full", "road"), "功率 247–263 W（95–101% CP） · 心率 160–168 bpm"),
    ("2026-10-09", date(2026, 10, 5), True, ("cut", "road"), "功率 247–263 W（95–101% CP） · 心率 160–168 bpm"),
    ("2026-10-13", date(2026, 10, 12), False, ("short", None), "功率 255–265 W（98–102% CP） · 心率 160–168 bpm"),
])
def test_taper_quality_takes_its_templates_target(day, monday, road, tmpl, text):
    """SP-408 (a): the session the taper makes a 減量期 quality session says the template's own % CP in
    watts (the demo's 10/27 上坡節奏跑: card 247–263 W, structure 229–247 W), not the old session's
    text; the structure has the same band and the watch the SP-378 band (lo, hi × 1.05)."""
    from backend.engine import workout_steps as WS
    from backend.engine.zones import watch_band
    from backend.sync import coros_workouts as CW
    t = {"full": O.TAPER_FULL, "cut": O.TAPER_CUT}[tmpl[0]][tmpl[1]] if tmpl[1] else O.TAPER_SHORT
    x = _taper_q(day, monday, road)
    assert x["title"] == t["title"] and x["target"] == text
    lo, hi = t["power_pct"]
    th = {"cp": 260.0, "lthr": 170.0, "aet": 150.0}
    work = WS.derive(x, th)["items"][1]["items"][0]
    assert (work["target"]["lo"], work["target"]["hi"]) == (lo, hi)                # structure = the card
    rep = CW.session_steps(x, CW.Thresholds.of(th))[1]
    wl, wh = watch_band(lo, hi)
    assert rep.steps[0].intensity == ("power", round(wl * 260), round(wh * 260))  # the watch: SP-378's band


def test_taper_quality_target_follows_the_basis_and_never_keeps_the_old_text():
    """SP-408: 課表偏好 目標依據 心率 → only the HR (the 越野 one's Zone 3 row since 2026-10-13; pushed as
    is), never the old session's (VO2) HR; 功率 → only the power; no CP and no HR text → empty, not the old text."""
    from backend.sync import coros_workouts as CW
    x = _taper_q("2026-10-06", date(2026, 10, 5), prefs=PP.Prefs(target_basis="hr"))
    assert x["target"] == "心率 150–159 bpm"
    rep = CW.session_steps({**x, "basis": "hr"}, CW.Thresholds.of({"cp": 260.0, "lthr": 170.0}))[1]
    assert rep.steps[0].intensity == ("hr", 150, 159)
    x = _taper_q("2026-10-06", date(2026, 10, 5), prefs=PP.Prefs(target_basis="power"))
    assert x["target"] == "功率 229–247 W（88–95% CP）"
    ss = [s("quality", "quality", "2026-10-06", target=OLD_TARGET)]
    O.taper_rules(ss, tc(), date(2026, 10, 5))
    assert ss[0]["target"] == ""


def test_week_plan_and_projection_taper_quality_take_the_templates_target():
    """SP-408: week_plan and the projection pass the CP and the threshold row to the taper rules."""
    ds, plan, wp = _week("2026-10-10")
    cp = float(wp["thresholds"]["cp"])

    def want(t):
        lo, hi = t["power_pct"]
        return f"功率 {lo * cp:.0f}–{hi * cp:.0f} W（{lo * 100:.0f}–{hi * 100:.0f}% CP）"
    q = [x for x in wp["sessions"] if x["kind"] == "quality" and x["day"] and not x["done"]
         and x["title"] in (O.TAPER_FULL["trail"]["title"], O.TAPER_CUT["trail"]["title"], O.TAPER_SHORT["title"])]
    by_title = {t["title"]: t for t in (O.TAPER_FULL["trail"], O.TAPER_CUT["trail"], O.TAPER_SHORT)}
    for x in q:
        assert x["target"].startswith(want(by_title[x["title"]]))
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18), events=plan.events)
    race_wk = next(w for w in weeks if w["start"] == "2026-10-05")
    pq = [x for x in race_wk["sessions"] if x["kind"] == "quality"]
    assert pq and all(x["target"].startswith(want(O.TAPER_SHORT)) for x in pq)


# ---- SP-411: the 減量期 trail template's minutes are the structure's ---------------------------------

def _pushed_seconds(steps) -> int:
    from backend.sync import coros_workouts as CW
    return sum(x.seconds if isinstance(x, CW.Step) else x.sets * _pushed_seconds(x.steps) for x in steps)


@pytest.mark.parametrize("day, monday, tmpl, minutes", [
    ("2026-10-06", date(2026, 10, 5), "full", 85),      # 15 + 3 × (10 + 10 jog down) + 10
    ("2026-10-09", date(2026, 10, 5), "cut", 65),       # 15 + 2 × (10 + 10) + 10
    ("2026-10-13", date(2026, 10, 12), "short", 35),    # 15 + 2 × (3 + 2) + 10 (already right; TSS untouched)
])
def test_taper_trail_quality_minutes_are_the_structures(day, monday, tmpl, minutes):
    """SP-411 (A, the owner 2026-10-13): the card's minutes = the structure / watch total — the trail
    templates said 60 / 50 while the steps (the rest = the rep when the text has no 「休 N 分」) were
    85 / 65; the TSS follows (≈ the structure's estimate)."""
    from backend.engine import workout_steps as WS
    from backend.sync import coros_workouts as CW
    t = {"full": O.TAPER_FULL["trail"], "cut": O.TAPER_CUT["trail"], "short": O.TAPER_SHORT}[tmpl]
    x = _taper_q(day, monday)
    assert x["minutes"] == t["minutes"] == minutes
    th = {"cp": 260.0, "lthr": 170.0, "aet": 150.0}
    # the session's own basis (target_policy: 「hill」 → power); SP-408 fixed this from Ctx.of({**th, "basis":
    # "power"}), whose dict key Ctx.of ignores (→ HR, the target text's HR row)
    ctx = WS.session_ctx(x, th)
    assert ctx.basis == "power"
    tot = WS.totals(WS.derive(x, th), ctx)
    assert tot["sec"] == minutes * 60
    assert _pushed_seconds(CW.session_steps(x, CW.Thresholds.of(th))) == minutes * 60
    if tmpl != "short":
        assert x["tss"] == pytest.approx(tot["tss"], rel=0.05)


# ---- SP-408 (the user 2026-10-13): the 越野 減量 tempo's HR is Zone 3, not the threshold row ---------

HRZ = {"work": {"Z3sub": [150.4, 158.6], "Z3near": [159.0, 168.0]}}      # a 課表心率區間 (hr_profile.plan_hr_zones)


def test_z3_hr_text_is_the_zone_3_row():
    """z3_hr_text: the 課表心率區間's Z3 (hr_profile.work_band — what a library Z3sub step pushes), else
    AeT–LTHR (interval_library.session_for / workout_steps.HR_WORK's Z3sub), else nothing."""
    assert O.z3_hr_text(HRZ, 140.0, 170.0) == "心率 150–159 bpm"
    assert O.z3_hr_text(None, 148.0, 170.0) == "心率 148–170 bpm"
    assert O.z3_hr_text(None, None, 170.0) == "" and O.z3_hr_text(None, 148.0, None) == ""


@pytest.mark.parametrize("basis", ["auto", "hr"])
def test_trail_taper_tempo_card_structure_and_push_use_zone_3(basis):
    """The 越野 減量 上坡節奏跑 (88–95 % CP): the card, the structure and the watch (目標依據 心率) all say
    the Zone 3 row — not the threshold row (Zone 4) the session used to take."""
    from backend.engine import workout_steps as WS
    from backend.sync import coros_workouts as CW
    z3 = O.z3_hr_text(HRZ, 140.0, 170.0)
    for day in ("2026-10-06", "2026-10-09"):                                 # the full one, the shorter one
        ss = [s("quality", "quality", day, target=OLD_TARGET)]
        O.taper_rules(ss, tc(), date(2026, 10, 5), cp=260.0, q_target=TH_TARGET, z3_target=z3,
                      prefs=PP.Prefs(target_basis=basis))
        x = ss[0]
        assert "上坡節奏跑" in x["title"] and "心率 150–159 bpm" in x["target"] and "160–168" not in x["target"]
        assert "hr_cls" not in x                                             # template-only key
        th = {"cp": 260.0, "lthr": 170.0, "aet": 140.0, "hr_model": HRZ}
        work = WS.derive(x, th)["items"][1]["items"][0]["target"]
        hr = (work["lo"], work["hi"]) if work["type"] == "hr" else tuple(work["hr"])
        assert hr == (150, 159)                                              # the structure = the card
        rep = CW.session_steps({**x, "basis": "hr"}, CW.Thresholds.of(th))[1]
        assert rep.steps[0].intensity == ("hr", 150, 159)                    # the watch = the card


@pytest.mark.parametrize("day, monday, road", [
    ("2026-10-06", date(2026, 10, 5), True), ("2026-10-09", date(2026, 10, 5), True),      # road full / cut
    ("2026-10-13", date(2026, 10, 12), False),                                              # the short one
])
def test_other_taper_quality_keeps_the_threshold_row(day, monday, road):
    """Only the 越野 88–95 % CP ones changed: road 95–101 % and the short 98–102 % keep the threshold row."""
    x = _taper_q(day, monday, road)
    assert x["target"].endswith("心率 160–168 bpm")


def test_week_plan_and_projection_trail_taper_tempo_take_zone_3():
    """week_plan and the projection pass the week's Zone 3 row (z3_hr_text of its thresholds) to the taper
    rules: the 越野 上坡節奏跑 says it, the short one keeps the threshold row."""
    ds, plan, wp = _week("2026-10-17")          # 10/03–10/16 taper: this week's 10/02– gets the full one
    th = wp["thresholds"]
    z3 = O.z3_hr_text(th.get("hr_model"), th.get("aet"), th.get("lthr"))
    assert z3
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 25), events=plan.events)
    trail = [x for w in [{"sessions": wp["sessions"]}] + weeks for x in w["sessions"]
             if x["kind"] == "quality" and not x.get("done")
             and x["title"] in (O.TAPER_FULL["trail"]["title"], O.TAPER_CUT["trail"]["title"])]
    assert trail and all(x["target"].endswith(z3) for x in trail)
