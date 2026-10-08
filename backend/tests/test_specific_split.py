"""SP-75: the 專項期 in two halves (docs/research/specific-phase-progression.md §4, owner 2026-10-05).

前段 (賽前第 10–7 週) keeps climbing the ladders, 後段 (第 6–3 週) leans the 1-a-week ratio toward the
race; the fixed 2×15′ / 5×4′ sessions are gone (越野 = the rung's uphill version); a long trail race's 後段
has no Zone 5; the road MP segment grows week by week, every other week. Synthetic data only."""
import datetime as dt
from datetime import date

import pytest

from backend.engine import interval_library as IL
from backend.engine import overview as O
from backend.engine import quality_gate as QG
from backend.engine.planning import Event

TODAY = date(2026, 9, 7)
RACE = date(2026, 11, 29)                      # a Sunday: 賽前第 n 週 = the Monday n weeks before + 6 days


def ev(kind="race", km=None, hours=None, days=1, start=RACE):
    return Event(id="a", name="A", date=start.isoformat(), kind=kind, priority="A", distance_km=km,
                 est_hours=hours, days=days)


def monday_of(w: int) -> date:
    """The Monday of 賽前第 w 週 before RACE."""
    return RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=w - 1)


def gate(events, z5=True, **kw):
    return {"state": "none", "guard": {}, "z3": {"open": True}, "z5": {"open": z5}, "z3_recent": {"done": 3},
            "dose": {"z3": {"step": 2, "met": 2, "done": 2}, "z5": {"step": 1, "done": 1}},
            "ratio": QG.track_ratio(events, TODAY), "monday": TODAY.isoformat(), **kw}


# ---- the race's class and the halves' ratios --------------------------------------------------------

@pytest.mark.parametrize("e, cls", [
    (ev("race", 21, hours=3.0), "trail_short"),
    (ev("race", 42, hours=4.5), "trail_long"),            # the 4-h line (owner 2026-10-05)
    (ev("race", 50), "trail_long"),                       # no time: EP → km (planning.event_size)
    (ev("baiyue", 30, days=3), "trail_long"),             # a multi-day trip is long
    (ev("road", 5), "road_5k"), (ev("road", 10), "road_10k"), (ev("road", 21.1), "road_half"),
    (ev("road", 42.195), "road_long"), (ev("road"), "road_long"),
])
def test_race_class_by_the_4_hour_line_and_the_road_distance(e, cls):
    assert QG.race_class(e) == cls


def test_track_ratio_keeps_the_base_ratio_and_adds_the_halves():
    r = QG.track_ratio([ev("race", 42, hours=5.0)], TODAY)
    assert (r["z3"], r["z5"]) == (2, 1) and r["class"] == "trail_long"         # the base phase: as before
    assert (r["early"]["z3"], r["early"]["z5"]) == (2, 1) and (r["late"]["z3"], r["late"]["z5"]) == (1, 0)
    assert "4 小時以上" in r["late"]["why"]
    s = QG.track_ratio([ev("race", 21, hours=3.0)], TODAY)
    assert (s["late"]["z3"], s["late"]["z5"]) == (1, 1)
    h = QG.track_ratio([ev("road", 21.1)], TODAY)
    assert (h["late"]["z3"], h["late"]["z5"]) == (3, 1) and h["late"]["tp"] == [4, 3]
    assert QG.track_ratio([], TODAY) == {"z3": 2, "z5": 1, "why": "沒有 A 賽"}


@pytest.mark.parametrize("e, early, late, tp", [
    (ev("race", 21, hours=3.0), (1, 1), (1, 1), []),          # 越野 < 4 h: 1:1 → 1:1
    (ev("race", 42, hours=5.0), (2, 1), (1, 0), []),          # 越野 ≥ 4 h: 2:1 → Zone 3 (+ maintenance, SP-353)
    (ev("road", 5), (1, 1), (1, 2), []),                      # 5 km: 1:2
    (ev("road", 10), (1, 1), (1, 1), [6, 5, 4, 3]),           # 10 km: 1:1, Zone 3 = T+
    (ev("road", 21.1), (2, 1), (3, 1), [4, 3]),               # 半馬: 3:1, T+ in weeks 4–3
    (ev("road", 42.195), (2, 1), (3, 1), []),                 # 馬拉松: 3:1
])
def test_spec_ratio_table_of_sp353(e, early, late, tp):
    """SP-353 acceptance: 前段 / 後段 as the report's tables (§4.1 / §4.2; decisions 2–4 kept as built)."""
    r = QG.track_ratio([e], TODAY)
    assert (r["early"]["z3"], r["early"]["z5"]) == early
    assert (r["late"]["z3"], r["late"]["z5"]) == late and r["late"]["tp"] == tp
    g = gate([e])
    assert QG.week_decision(g, "specific", "specific", monday_of(8))["seg_note"] == ""     # 前段: no 後段 note


def test_late_half_recovery_weeks_keep_the_fartlek():
    """SP-97 in the 後段 (weeks 5 / 3): the recovery fartlek, no maintenance, no 後段 note — whatever the race."""
    for e in (ev("race", 42, hours=5.0), ev("road", 21.1)):
        for w in (5, 3):
            d = QG.week_decision(gate([e]), "specific", "recovery_week", monday_of(w), n=2)
            assert d["spec"] is QG.RECOVERY and len(d["items"]) == 1 and not d.get("seg_note")


def test_week_ratio_picks_the_half_by_the_weeks_out():
    g = gate([ev("race", 42, hours=5.0)])
    assert QG.week_ratio(g, "base", monday_of(5)) is g["ratio"]                # base phase: unchanged
    early, late = QG.week_ratio(g, "specific", monday_of(8)), QG.week_ratio(g, "specific", monday_of(6))
    assert early["segment"] == "early" and early["weeks_out"] == 8 and early["z5"] == 1
    assert late["segment"] == "late" and late["weeks_out"] == 6 and late["z5"] == 0
    # an older stored gate (no halves): its ratio
    old = {**g, "ratio": {"z3": 2, "z5": 1, "why": "A 賽越野"}}
    assert QG.week_ratio(old, "specific", monday_of(6)) == old["ratio"]


# ---- week_decision ---------------------------------------------------------------------------------

def test_a_long_trail_race_keeps_one_zone5_maintenance_every_3_weeks_in_the_late_half():
    """SP-353 decision 1 (owner 2026-10-07, report §4.1 / §5): the 後段 is Zone 3 (uphill) except one Zone 5
    maintenance session every 3 weeks — 賽前第 4 週 (weeks 5 / 3 are the recovery weeks)."""
    g = gate([ev("race", 42, hours=5.0)])
    assert QG.track_ratio([ev("race", 42, hours=5.0)], TODAY)["late"]["z5_every"] == 3
    for n in (1, 2):
        d = QG.week_decision(g, "specific", "specific", monday_of(6), n=n)
        # 2 a week: the rung + a 巡航版 Zone 3 (the existing only-Zone-3 rule), no Zone 5
        assert {it["track"] for it in d["items"]} == {"z3"} and d["items"][0]["spec"][0] == "a3"
        assert len(d["items"]) == n and all(it.get("cruise") for it in d["items"][1:])
        assert "4 小時以上" in d["seg_note"] and "5 區不排" in d["seg_note"] and "每 3 週" in d["seg_note"]
        assert "推估" in d["seg_note"]
        # 賽前第 4 週: the maintenance session — the Zone 5 rung as it stands, it doesn't move the rung
        m = QG.week_decision(g, "specific", "specific", monday_of(4), n=n)
        z5 = [it for it in m["items"] if it["track"] == "z5"]
        assert len(z5) == 1 and z5[0]["spec"] == QG.z5_spec(1) and z5[0]["advance"] is False and z5[0]["maint"]
        assert [it["track"] for it in m["items"]] == (["z5"] if n == 1 else ["z3", "z5"])
        assert "5 區維持課" in m["seg_note"] and "賽前第 4 週" in m["seg_note"]
    assert "1:0" not in QG.week_decision(g, "specific", "specific", monday_of(4))["z3_note"]
    # one in the 後段 (weeks 6–3), whatever the week's quality count
    picks = [it["track"] for w in (6, 5, 4, 3) for it in QG.week_decision(g, "specific", "specific", monday_of(w))["items"]]
    assert picks.count("z5") == 1
    # the 前段 keeps Zone 5 (2:1, and 2 a week = one of each); no 後段 note there
    d2 = QG.week_decision(g, "specific", "specific", monday_of(9), n=2)
    assert [it["track"] for it in d2["items"]] == ["z3", "z5"] and d2["seg_note"] == ""
    picks = [QG.week_decision(g, "specific", "specific", monday_of(w))["track"] for w in (10, 9, 8, 7)]
    assert "z5" in picks
    # not a lock: the gate's Zone 5 state and step are untouched
    assert g["z5"] == {"open": True} and g["dose"]["z5"]["step"] == 1
    # the other races: no maintenance rule (they keep Zone 5 in their ratio)
    assert not QG.track_ratio([ev("race", 21, hours=3.0)], TODAY)["late"].get("z5_every")


def test_the_zone5_maintenance_obeys_the_gate_and_the_guardrails():
    """The maintenance session is a Zone 5 session like any other: Zone 5 locked, the low-intensity share
    (tested AeT) or a load guardrail → not scheduled, and the week note says so."""
    races = [ev("race", 42, hours=5.0)]
    shut = gate(races, z5=False)
    d = QG.week_decision(shut, "specific", "specific", monday_of(4))
    assert [it["track"] for it in d["items"]] == ["z3"]
    assert "5 區維持課" in d["seg_note"] and "還沒開放" in d["seg_note"] and "不排" in d["seg_note"]
    bad = gate(races, levels={"intensity": "bad"})
    d = QG.week_decision(bad, "specific", "specific", monday_of(4))
    assert [it["track"] for it in d["items"]] == ["z3"] and "5 區維持課" in d["seg_note"] and "強度分配" in d["seg_note"]
    ramp = gate(races, guard={"block": True, "blocks": ["ramp"], "rule": "ramp", "verdict": "CTL 增幅太快",
                             "verdicts": {"ramp": "CTL 增幅太快"}})
    d = QG.week_decision(ramp, "specific", "specific", monday_of(4))
    assert d["items"] == [] and d["note"] == "CTL 增幅太快"
    assert "5 區維持課" in d["seg_note"] and "護欄" in d["seg_note"]
    # a guardrail week that isn't the maintenance week: no 後段 note added
    assert not QG.week_decision(ramp, "specific", "specific", monday_of(6)).get("seg_note")


def test_a_short_trail_race_keeps_both_tracks_late_and_alternates_in_two_week_blocks():
    g = gate([ev("race", 21, hours=3.0)])
    picks = {w: QG.week_decision(g, "specific", "specific", monday_of(w))["track"] for w in (6, 5, 4, 3)}
    # 賽前第 5、3 週 are the recovery weeks (SP-97): weeks 6 and 4 must not share the track on a 1:1
    assert picks[6] != picks[4] and picks[6] == picks[5] and picks[4] == picks[3]


def test_road_late_half_ratios_and_tplus():
    half = gate([ev("road", 21.1)])
    picks = [QG.week_decision(half, "specific", "specific", monday_of(w))["track"] for w in (6, 4)]
    assert picks == ["z3", "z3"]                                              # 3:1, Zone 3 first
    d = QG.week_decision(half, "specific", "specific", monday_of(4))
    assert d["items"][0]["spec"] is QG.TP and d["items"][0]["advance"] is False   # T+ doesn't move the rung
    assert "97–100% CP" in d["seg_note"] and "3:1" in d["seg_note"]
    assert QG.week_decision(half, "specific", "specific", monday_of(6))["items"][0]["spec"][0] == "a3"
    # Zone 5 not open: the ladder rung, no T+ (a maintenance session of an open Zone 5)
    shut = gate([ev("road", 21.1)], z5=False)
    assert QG.week_decision(shut, "specific", "specific", monday_of(4))["items"][0]["spec"][0] == "a3"
    five = gate([ev("road", 5)])
    picks = [QG.week_decision(five, "specific", "specific", monday_of(w))["track"] for w in (6, 4)]
    assert picks.count("z5") >= 1 and "1:2" in QG.week_decision(five, "specific", "specific", monday_of(6))["seg_note"]


# ---- SP-352: a manual 專項期 without an A race ------------------------------------------------------------

def test_manual_specific_without_an_a_race_and_zone3_on_its_tplus_turn():
    """A manual 專項期, no A race, and Zone 3's maintenance turn is T+ (z3_spec step 6): the week used to
    raise KeyError 'weeks_out' (no 前段／後段 without a race). Now: the ladder's T+ is scheduled as is, no 後段 note."""
    assert QG.z3_spec(6) is QG.TP
    for z5 in (False, True):
        g = gate([], z5=z5, dose={"z3": {"step": 6, "met": 6, "done": 6}, "z5": {"step": 1, "done": 1}})
        assert "late" not in g["ratio"]
        weeks = [TODAY + dt.timedelta(weeks=k) for k in range(3)]
        decs = [QG.week_decision(g, "specific", "specific", m) for m in weeks]
        assert all(d["allow"] for d in decs) and all(d["seg_note"] == "" for d in decs)
        z3 = [it for d in decs for it in d["items"] if it["track"] == "z3"]
        assert z3 and all(it["spec"] is QG.TP and it["advance"] for it in z3)   # the ladder's own turn: it counts
        assert all(QG.week_decision(g, "specific", "specific", m, n=2)["allow"] for m in weeks)


def test_specific_after_the_a_race_and_zone3_on_its_tplus_turn():
    """The same T+ turn in a 專項期 week after the A race (week_ratio has no half there): no KeyError either."""
    g = gate([ev("road", 21.1, start=TODAY + dt.timedelta(days=3))], z5=False,
             dose={"z3": {"step": 6, "met": 6, "done": 6}, "z5": {"step": 0, "done": 0}})
    d = QG.week_decision(g, "specific", "specific", TODAY + dt.timedelta(weeks=2))
    assert d["allow"] and d["items"][0]["spec"] is QG.TP and d["seg_note"] == ""


def test_the_late_half_note_with_an_a_race_is_unchanged():
    """With an A race the 後段 still says what it changed, the T+ week included (SP-75 as before)."""
    half = gate([ev("road", 21.1)])
    assert "賽前第 4 週" in QG.week_decision(half, "specific", "specific", monday_of(4))["seg_note"]
    assert QG.week_decision(half, "specific", "specific", monday_of(8))["seg_note"] == ""


# ---- the sessions: the ladder, uphill on trail ---------------------------------------------------------

def test_fit_hill_gives_the_rungs_uphill_version_from_the_first_session():
    f = IL.fit("z5a", None, (), None, True, hill=True)
    assert f["variant"].key == "v1c" and f["variant"].terrain == "hill" and f["equiv"]
    assert IL.fit("z5a", None, (), None, True)["variant"].key == "v1a"          # base: the studied protocol first
    assert IL.fit("a3", None, (), None, True, hill=True)["variant"].key == "a3d"
    # 課表偏好 interval terrain = flat wins
    from backend.engine import plan_prefs as PP
    assert IL.fit("z5a", None, (), PP.Prefs(terrain_quality="flat"), True, hill=True)["variant"].terrain == "flat"
    # a tight cap cuts the uphill version's reps
    tight = IL.fit("z5c", 40, (), None, True, hill=True)
    assert tight["variant"].terrain == "hill" or tight["action"] != "ok"


def test_specific_quality_sessions_are_the_ladder_uphill_on_trail_flat_on_road():
    g = {"state": "unlocked", "guard": {}, "lthr": {"default": False},
         "dose": {"z3": {"step": 1}, "z5": {"step": 0}}}
    dec = {"items": [{"track": "z3", "spec": QG.Z3[1], "advance": True, "first": False},
                     {"track": "z5", "spec": QG.Z5[0], "advance": True}]}
    trail = O.quality_sessions(g, dec, "specific", {"cp": 250.0}, {}, 10.0, mountain=True)
    assert [s["rung_key"] for s in trail] == ["a2", "z5a"]
    assert [s["variant_key"] for s in trail] == ["a2d", "v1c"] and all(s["title"].endswith("上坡") for s in trail)
    road = O.quality_sessions(g, dec, "specific", {"cp": 250.0}, {}, 10.0, road=True)
    assert [s["variant_key"] for s in road] == ["a2a", "v1a"]
    assert not any(s["title"] in (O.ROAD_SPECIFIC_Q["title"], O.TRAIL_SPECIFIC_Z5["title"]) for s in trail + road)
    # the Zone 5 uphill set is 10′ in zone at V1, not the old 20′ (in the 10–16′ of a Zone 5 session)
    assert O.session_tiz_min(trail[1]) == 10


def test_the_zone5_maintenance_session_is_the_rungs_uphill_version():
    """SP-353: the long trail race's maintenance week builds the Zone 5 rung's uphill session (1 or 2 a week)."""
    g = {**gate([ev("race", 42, hours=5.0)]), "state": "unlocked", "lthr": {"default": False}}
    for n in (1, 2):
        dec = QG.week_decision(g, "specific", "specific", monday_of(4), n=n)
        ss = O.quality_sessions(g, dec, "specific", {"cp": 250.0}, {}, 10.0, mountain=True)
        z5 = [s for s in ss if s.get("rung_key") == QG.z5_spec(1)[0]]
        assert len(z5) == 1 and z5[0]["title"].endswith("上坡") and len(ss) == n


# ---- the road MP segment ---------------------------------------------------------------------------------

def test_mp_week_grows_every_other_week():
    race = O.mp_race([ev("road", 42.195)], TODAY)
    shares = {w: O.mp_week(race, monday_of(w))["share"] for w in range(10, 2, -1)}
    assert shares == {10: 0.20, 9: 0.25, 8: 0.30, 7: None, 6: 0.35, 5: None, 4: 0.40, 3: None}
    assert O.mp_week(race, monday_of(12))["share"] == 0.20                     # a longer 專項期: the first share
    short = O.mp_race([ev("road", 10)], TODAY)
    assert short["short"] and O.mp_week(short, monday_of(6))["share"] is None
    assert O.mp_race([], TODAY) is None and O.mp_week(None, monday_of(6)) is None


def test_road_long_session_follows_the_mp_week():
    s = O.road_long_session(150, "specific", 145.0, 50.0, mp={"weeks_out": 10, "share": 0.20})
    assert s["title"] == "長跑＋馬拉松配速 30 分"
    s = O.road_long_session(150, "specific", 145.0, 50.0, mp={"weeks_out": 4, "share": 0.40})
    assert s["title"] == "長跑＋馬拉松配速 60 分"
    easy = O.road_long_session(150, "specific", 145.0, 50.0, mp={"weeks_out": 7, "share": None})
    assert easy["title"] == "LSD（路跑）" and "隔週" in easy["detail"] and easy["minutes"] == 150
    ten = O.road_long_session(150, "specific", 145.0, 50.0, mp={"weeks_out": 6, "share": None, "short": True})
    assert ten["title"] == "LSD（路跑）" and "10 公里以內" in ten["detail"]
    # no race known: the old 40 % every week
    assert O.road_long_session(150, "specific", 145.0, 50.0)["title"] == "長跑＋馬拉松配速 60 分"


def test_week_plan_and_projection_carry_the_mp_plan():
    from backend.engine import projection as P
    from backend.tests.test_primary_sport import TODAY as T, _phases, _wp
    _, plan, wp = _wp("road")
    assert wp["mp_race"] == {"start": "2026-12-05", "short": False}
    weeks = P.project_weeks(wp, _phases(plan, T), date(2026, 11, 22))
    for w in weeks:
        if w["phase"] != "specific" or w["mode"] != "specific":
            continue
        lg = next(s for s in w["sessions"] if s["id"] == "long")
        mw = O.mp_week(wp["mp_race"], date.fromisoformat(w["start"]))
        if mw["share"] is None:
            assert "馬拉松配速" not in lg["title"]
        else:
            assert "馬拉松配速" in lg["title"]
