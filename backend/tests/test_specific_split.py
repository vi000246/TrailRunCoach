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

def test_a_long_trail_race_has_no_zone5_in_the_late_half():
    g = gate([ev("race", 42, hours=5.0)])
    for w in (6, 4):
        for n in (1, 2):
            d = QG.week_decision(g, "specific", "specific", monday_of(w), n=n)
            # 2 a week: the rung + a 巡航版 Zone 3 (the existing only-Zone-3 rule), never Zone 5
            assert {it["track"] for it in d["items"]} == {"z3"} and d["items"][0]["spec"][0] == "a3"
            assert len(d["items"]) == n and all(it.get("cruise") for it in d["items"][1:])
            assert "4 小時以上" in d["seg_note"] and "5 區不排" in d["seg_note"] and "推估" in d["seg_note"]
    # the 前段 keeps Zone 5 (2:1, and 2 a week = one of each); no 後段 note there
    d2 = QG.week_decision(g, "specific", "specific", monday_of(9), n=2)
    assert [it["track"] for it in d2["items"]] == ["z3", "z5"] and d2["seg_note"] == ""
    picks = [QG.week_decision(g, "specific", "specific", monday_of(w))["track"] for w in (10, 9, 8, 7)]
    assert "z5" in picks
    # not a lock: the gate's Zone 5 state and step are untouched
    assert g["z5"] == {"open": True} and g["dose"]["z5"]["step"] == 1


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
