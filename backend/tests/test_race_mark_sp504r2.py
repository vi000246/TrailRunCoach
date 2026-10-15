"""
SP-504 R2 (the owner's Q3, all as suggested): a 百岳 trip (planning event kind baiyue) is a 行程 everywhere
the week plan talks about it —
1. the week plan's notes (post_race: bc_apply, b_week_factor, the B recovery, the A / big-B first-week rules)
   say 「B 行程「X」…行程是這週的重點課」／「C 行程「X」…當訓練走」, not 賽事 / 比賽; a trail race keeps its words;
2. an A trip's event phase is labelled 行程, not 賽事 (planning.phase_label: the 課表's phase label and
   「目前期別」, the season plan page, the status);
3. (schedule.html / overview.html, the browser side: e2e/test_schedule_race_mark_sp504r2_e2e.py) the trip's
   activity card has the hike icon, the A race's day has the mark 「A 賽：X」 (A 行程：X) like the overview.
Synthetic.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from backend.engine import planning as P
from backend.engine import post_race as PR
from backend.i18n import translate

MON = date(2026, 10, 12)                                # a trip / race on Sat 10/17
TRIP = dict(kind="baiyue", days=1, distance_km=12, climbing_m=1200, est_hours=7.0)
TRAIL = dict(distance_km=21, climbing_m=800, est_hours=3.0)
EN = json.loads((Path(PR.__file__).resolve().parents[1] / "i18n" / "locales" / "en.json").read_text("utf-8"))
META = json.loads((Path(PR.__file__).resolve().parents[1] / "i18n" / "locales" / "en.meta.json").read_text("utf-8"))


def ev(name, date_, pri="B", **kw):
    return P.Event(id=name, name=name, date=date_, priority=pri, **kw)


def s(id_, kind, day, minutes=50, **kw):
    return {"id": id_, "kind": kind, "day": day, "minutes": minutes, "title": "x", "tss": 50.0, "done": False,
            "detail": "", "source": "", "terrain": None, **kw}


def _week():
    return [s("quality", "quality", "2026-10-12", 60, variant_key="x"), s("quality2", "quality", "2026-10-14", 60),
            s("long", "long", "2026-10-18", 120), s("easy1", "easy", "2026-10-17")]


def _no_race_words(txt: str):
    assert "賽事" not in txt and "比賽" not in txt and "賽前" not in txt, txt


# ---- 1. the week plan's notes -----------------------------------------------------------------------------

@pytest.mark.parametrize("prio", ["B", "C"])
def test_bc_apply_notes_say_trip_for_a_baiyue_trip(prio):
    notes, marks = [], []
    PR.bc_apply(_week(), [ev("大小霸", "2026-10-17", prio, **TRIP)], MON, notes, races=marks)
    race = [n["text"] for n in notes if n["src"] == "race"]
    assert race and marks
    for t in race:
        assert t.startswith(f"{prio} 行程「大小霸」10/17"), t
        _no_race_words(t)
    if prio == "B":
        assert "出發前 5 天不排間歇、4 天不排節奏跑和長跑，行程是這週的重點課" in race[0]
    else:
        assert "當訓練走" in race[0] and "當訓練跑" not in race[0]


@pytest.mark.parametrize("prio, words", [("B", "B 賽事「貓空」10/17：賽前 5 天不排間歇"), ("C", "C 賽事「貓空」10/17 當訓練跑")])
def test_a_trail_race_keeps_its_race_words(prio, words):
    notes = []
    PR.bc_apply(_week(), [ev("貓空", "2026-10-17", prio, **TRAIL)], MON, notes)
    assert any(n["text"].startswith(words) for n in notes), notes


def test_b_week_factor_says_trip():
    f, why = PR.b_week_factor([ev("大小霸", "2026-10-17", **TRIP)], MON, "base")
    assert f == PR.B_WEEK_SHARE and why == "B 行程「大小霸」10/17：這週量 75%（推估），不排長跑，行程算這週的重點課"
    _, why = PR.b_week_factor([ev("貓空", "2026-10-17", **TRAIL)], MON, "base")
    assert why.startswith("B 賽事「貓空」10/17") and "比賽算這週的重點課" in why


def test_the_recovery_after_a_b_trip_says_trip():
    b = ev("大小霸", "2026-10-10", **TRIP)                       # Sat; the next week holds its recovery
    w = PR.b_windows([b], MON)
    assert w and w[0]["trip"] is True
    notes = []
    PR.bc_apply([s("quality", "quality", "2026-10-12", 60)], [b], MON, notes)
    t = next(n["text"] for n in notes if n["src"] == "race")
    assert t.startswith("B 行程「大小霸」10/10 後") and "依行程大小" in t and "照 A 級的恢復期" in t, t
    _no_race_words(t.replace("馬拉松級", ""))
    assert PR.b_windows([ev("貓空", "2026-10-10", **TRAIL)], MON)[0]["trip"] is False


def test_an_a_trip_s_first_week_notes_say_trip():
    """apply (the A race's days and first week, SP-492): an A 百岳 trip's notes say 行程."""
    t = ev("嘉明湖", "2027-03-05", "A", kind="baiyue", days=3, distance_km=30, climbing_m=2500, est_hours=18.0)
    ph = P.auto_phases([t], date(2026, 6, 1), date(2027, 12, 31), 0)
    mon = date(2027, 3, 1)
    w = PR.a_windows(ph, [t], mon)
    assert w and w[0]["trip"] is True and w[0]["start"] == date(2027, 3, 5)
    notes = []
    PR.apply([s("easy1", "easy", "2027-03-06")], w, mon, notes)            # only the trip's day had a run
    assert notes and notes[0]["text"].startswith("A 行程「嘉明湖」3/5 當天只排行程"), notes
    _no_race_words(notes[0]["text"])
    # the week after: the first-week rules, still 行程 (and the race-day part when both)
    notes = []
    PR.apply([s("easy1", "easy", "2027-03-07"), s("easy2", "easy", "2027-03-09", 60)], w, mon, notes)
    assert notes and notes[0]["text"].startswith("A 行程「嘉明湖」3/7 後"), notes
    _no_race_words(notes[0]["text"])
    # a trail A race keeps 賽事 / 比賽
    r = ev("50K", "2027-03-06", "A", **TRAIL)
    notes = []
    PR.apply([s("easy1", "easy", "2027-03-06")], PR.a_windows(P.auto_phases([r], date(2026, 6, 1),
                                                                              date(2027, 12, 31), 0), [r], mon), mon, notes)
    assert notes[0]["text"].startswith("A 賽事「50K」3/6 當天只排比賽")


TRIP_MSGIDS = [
    "{p} 行程「{race}」{date} 後",
    "{p} 行程「{race}」{date} 當天只排行程",
    "{p} 行程「{race}」{date} 後：前 {n} 天不排跑步和肌力，第 1 週每次 ≤ {max} 分輕鬆跑",
    "；行程當天只排行程",
    "；下坡比你近 6 週練過的多，行程後 {h} 小時內走平路、不排硬課",
    "B 行程「{race}」{date}：這週量 {share:.0%}（推估），不排長跑，行程算這週的重點課",
    "B 行程「{race}」{date}：出發前 {a} 天不排間歇、{b} 天不排節奏跑和長跑，行程是這週的重點課（Pfitzinger、Friel，教練級）",
    "B 行程「{race}」{date} 後 {n} 天只排輕鬆跑（依行程大小：短 3 天；中 每 3 km 一天、用 EP、最多 7 天；馬拉松級以上照 A 級的恢復期；Daniels、Pfitzinger，推估）",
    "C 行程「{race}」{date} 當訓練走，其他照常",
    "C 行程「{race}」{date} 當訓練走：取代這週的{what}，其他照常",
    "行程",
]
WITHIN = ["出發前 {n} 天內不排節奏跑、長跑", "出發前 {n} 天內不排間歇"]       # the B trip note's parts (same English)


def test_the_trip_strings_have_english_that_says_trip():
    for k in TRIP_MSGIDS:
        assert k in EN and EN[k], k
        assert k in META, k                                           # the source map has it too
        words = re.sub(r"\{[^}]*\}", "", EN[k]).lower()                 # (the placeholder {race} aside)
        assert "trip" in words and "race" not in words, EN[k]
        assert set(re.findall(r"\{(\w+)", k)) == set(re.findall(r"\{(\w+)", EN[k])), k
    for k in WITHIN:
        assert EN[k] == EN[k.replace("出發前", "賽前")] and k in META, k
    assert translate("C 行程「{race}」{date} 當訓練走：取代這週的{what}，其他照常", "en", race="大小霸", date="10/17",
                     what="long run") == 'C trip "大小霸" on 10/17 is a training hike: it replaces this week\'s long run; ' \
                                         "the rest as usual"


def test_the_new_english_entries_sit_beside_their_race_entries():
    keys = list(EN)
    for race, trip in [("{p} 賽事「{race}」{date} 後", "{p} 行程「{race}」{date} 後"),
                       ("；比賽當天只排比賽", "；行程當天只排行程"),
                       ("C 賽事「{race}」{date} 當訓練跑，其他照常", "C 行程「{race}」{date} 當訓練走，其他照常"),
                       ("賽事", "行程")]:
        assert keys.index(trip) == keys.index(race) + 1, trip


# ---- 2. an A trip's phase label -----------------------------------------------------------------------------

def _a_trip_plan():
    t = ev("嘉明湖", "2027-03-05", "A", kind="baiyue", days=3, distance_km=30, climbing_m=2500, est_hours=18.0)
    r = ev("50K", "2027-06-05", "A", **TRAIL)
    return P.Plan(events=[t, r])


def test_an_a_trip_s_event_phase_is_labelled_trip():
    plan = _a_trip_plan()
    phs = P.phases(plan, date(2026, 10, 1), date(2027, 8, 1), 0, P.TAPER_DAYS)
    ev_ph = {p.event_id: p for p in phs if p.kind == "event"}
    assert P.phase_label(ev_ph["嘉明湖"], plan.events) == "行程"
    assert P.phase_label(ev_ph["50K"], plan.events) == "賽事"
    assert {P.phase_label(p, plan.events) for p in phs if p.kind != "event"} <= set(map(str, P.PHASES.values()))
    assert P.phase_label(ev_ph["嘉明湖"]) == "賽事"                     # no events: the kind's label
    # a dict phase (the calendar's rows) and a manual phase without event_id (its first day)
    assert P.phase_label({"kind": "event", "start": "2027-03-05", "end": "2027-03-07"}, plan.events) == "行程"
    assert P.phase_json(ev_ph["嘉明湖"], plan.events)["label"] == "行程"
    assert P.phase_json(ev_ph["50K"], plan.events)["label"] == "賽事"
    assert translate("行程", "en") == "Trip"


def test_the_calendar_and_the_status_use_the_trip_label():
    """The 課表's phases (api/plan_sessions._range_extras: the cell's phase label, 目前期別, the week row) and
    the status (overview's 周期) read planning.phase_label."""
    import inspect

    from backend.api import plan_sessions
    from backend.engine import status as ST
    assert "planning.phase_label(p, st.plan.events)" in inspect.getsource(plan_sessions._range_extras)
    src = inspect.getsource(ST)
    assert "phase_label(p, self.plan.events)" in src and "phase_label(self.phase, self.plan.events)" in src


def test_the_status_says_trip_on_an_a_trip_day():
    from backend.engine import status as ST
    plan = _a_trip_plan()
    st = ST.Status.__new__(ST.Status)
    st.plan, st.today = plan, date(2027, 3, 6)
    st.phase = P.phase_on(plan, st.today)
    st.goals = P.goals(plan, st.today)
    assert st.phase.kind == "event"
    assert st.i_phase().text == "行程"
