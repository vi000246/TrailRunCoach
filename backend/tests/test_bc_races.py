"""
SP-95: B and C races change their weeks (engine/post_race.py): a B race's week at 75 % with no long
run, no interval 5 days / no tempo or long run 4 days before it, the race as the week's key session,
the recovery after it by event size (短 3 / 中 5 / 馬拉松級+ = the A recovery), a C race in place of a
quality session or the long run, the hints; week_plan and the projection use the same rules.
(Two A races < 12 / < 8 weeks apart: test_post_race.py.) Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import planning as P
from backend.engine import post_race as PR


def ev(name, date_, pri="B", **kw):
    return P.Event(id=name, name=name, date=date_, priority=pri, **kw)


SHORT = dict(kind="road", distance_km=10, climbing_m=0, est_hours=0.8)
MEDIUM = dict(distance_km=21, climbing_m=800, est_hours=3.0)
MARA = dict(kind="road", distance_km=42.2, est_hours=4.5)
MON = date(2026, 10, 12)                      # B race Sat 10/17


def s(id_, kind, day, minutes=50, **kw):
    return {"id": id_, "kind": kind, "day": day, "minutes": minutes, "title": "x", "tss": 50.0, "done": False,
            "detail": "", "source": "", "terrain": None, **kw}


def test_b_week_volume_75():
    b = ev("b", "2026-10-17", **MEDIUM)
    f, why = PR.b_week_factor([b], MON, "base")
    assert f == 0.75 and "「b」10/17" in why and "75%" in why
    assert PR.b_week_factor([b], MON, "taper") == (1.0, None)
    assert PR.b_week_factor([b], MON + dt.timedelta(weeks=1), "base") == (1.0, None)
    assert PR.b_week_factor([ev("c", "2026-10-17", "C", **MEDIUM)], MON, "base") == (1.0, None)


def test_b_race_week_rules():
    b = ev("b", "2026-10-17", **MEDIUM)
    ss = [s("quality", "quality", "2026-10-12", 60, variant_key="x"),      # 5 days out: an interval → easy
          s("quality2", "quality", "2026-10-13", 60, title="節奏跑 2×10"),   # 4 days out: tempo → easy
          s("long", "long", "2026-10-18", 120),                             # race week: no long run
          s("easy1", "easy", "2026-10-17"),                                 # the race day: goes
          s("strength1", "strength", "2026-10-15", 35)]
    notes = []
    out = PR.bc_apply(ss, [b], MON, notes)
    by = {x["day"]: x for x in out}
    assert by["2026-10-12"]["kind"] == "easy" and by["2026-10-13"]["kind"] == "easy"
    assert by["2026-10-18"]["kind"] == "easy" and by["2026-10-18"]["minutes"] <= PR.B_EASY_MAX
    race = by["2026-10-17"]
    assert race["kind"] == "race" and race["minutes"] == 180 and "B 賽事：b" in race["title"]
    assert sum(1 for x in out if x["day"] == "2026-10-17") == 1
    assert any("B 賽事「b」10/17" in n["text"] and "重點課" in n["text"] for n in notes)
    # a tempo 5 days out stays (only intervals are out that day)
    out = PR.bc_apply([s("quality", "quality", "2026-10-12", 60, title="節奏跑 2×10")], [b], MON)
    assert out[0]["kind"] == "quality"
    # the mini-taper days reach into the week before a Monday B race
    mon_b = ev("m", "2026-10-19", **SHORT)
    out = PR.bc_apply([s("long", "long", "2026-10-17", 120), s("long2", "long", "2026-10-14", 120)], [mon_b], MON)
    assert out[0]["kind"] == "easy" and out[1]["kind"] == "long"


@pytest.mark.parametrize("kw, days, a_like", [(SHORT, 3, False), (MEDIUM, 5, False), (MARA, 7, True)])
def test_b_recovery_by_size(kw, days, a_like):
    b = ev("b", "2026-10-10", **kw)                              # Sat; the next week holds its recovery
    w = PR.b_windows([b], MON)
    assert w and w[0]["days"] == days and (w[0]["short"] > w[0]["end"]) == a_like
    ss = [s("quality", "quality", "2026-10-12", 60), s("long", "long", "2026-10-17", 120)]
    out = PR.bc_apply(ss, [b], MON, [])
    q = next(x for x in out if x["day"] == "2026-10-12" or x["id"].startswith("easy") and x["minutes"] <= 60)
    assert q["kind"] == "easy"
    lng = next((x for x in out if x["day"] == "2026-10-17"), None)
    assert lng is not None and (lng["kind"] == "easy") == (days >= 7)
    if a_like:                                                    # day 2 (10/12) is a no-run day
        assert all(x["day"] != "2026-10-12" or x["kind"] == "strength" for x in out)


def test_c_race_replaces_a_quality_or_the_long_run():
    ss = [s("long", "long", "2026-10-18", 120), s("quality", "quality", "2026-10-14", 60),
          s("easy1", "easy", "2026-10-17")]
    notes = []
    out = PR.bc_apply([dict(x) for x in ss], [ev("c", "2026-10-17", "C", **SHORT)], MON, notes)
    assert not any(x["kind"] == "quality" for x in out) and any(x["kind"] == "long" for x in out)
    assert next(x for x in out if x["kind"] == "race")["day"] == "2026-10-17"
    assert next(x for x in out if x["id"] == "easy1")["day"] == "2026-10-14"       # moved to the freed day
    assert "取代這週的一堂強度課" in notes[0]["text"]
    out = PR.bc_apply([dict(x) for x in ss], [ev("c", "2026-10-17", "C", **MEDIUM)], MON, [])
    assert not any(x["kind"] == "long" for x in out) and any(x["kind"] == "quality" for x in out)


def test_hints():
    a = ev("a", "2026-11-07", "A", **MARA)
    b1, b2 = ev("b1", "2026-10-17", **MEDIUM), ev("b2", "2026-10-31", **SHORT)
    txt = [n["text"] for n in PR.b_hints([a, b1, b2], MON)]
    assert any("B 賽太多" in t and "30 天內還有 1 場" in t for t in txt)
    assert any("長距離 B 賽「b1」在 A 賽事「a」前 21 天" in t for t in txt)
    assert not PR.b_hints([b1], MON)                                # one B race, no A race after it
    assert not any("長距離" in n["text"] for n in PR.b_hints([a, b2], date(2026, 10, 26)))   # short: fine
    # the CTL down > 10 % over the weeks holding a B race
    ctl = [(MON - dt.timedelta(days=i), 50.0 if i > 20 else 43.0) for i in range(56, -1, -1)]
    assert any("CTL 從近 8 週的高點 50 掉到 43" in n["text"] for n in PR.b_hints([ev("x", "2026-09-26", **SHORT)], MON, ctl))
    assert not PR.b_hints([ev("x", "2026-09-26", **SHORT)], MON, [(MON, 50.0), (MON, 48.0)])


def test_b_windows_markers_by_size():
    w = P.b_event_windows([ev("b", "2026-10-17", **MEDIUM)])
    rec = next(x for x in w if x["kind"] == "mini_recovery")
    assert rec["end"] == "2026-10-22" and "恢復 5 天" in rec["text"]


def test_week_plan_and_projection_with_a_b_race():
    """A medium B race Sat 10/10 in the base phase: the week of TODAY (9/30) is untouched, the projected
    race week at 75 % with the race and no long run, the next week starts with easy runs only."""
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as PJ
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY
    ds = _history(TODAY)
    plan = _plan_with("2027-06-01", 1, TODAY)
    plan.events = list(plan.events) + [ev("b", "2026-10-10", **MEDIUM), ev("c", "2026-10-03", "C", **SHORT)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    race = [x for x in wp["sessions"] if x["kind"] == "race"]
    assert race and race[0]["day"] == "2026-10-03" and "C 賽事" in race[0]["title"]
    assert any("C 賽事「c」10/3 當訓練跑" in n["text"] for n in wp["notes"])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18), events=plan.events)
    bw = next(w for w in weeks if w["start"] == "2026-10-05")
    assert any("B 賽事「b」10/10" in w and "75%" in w for w in bw["why"])
    assert any(x["kind"] == "race" and x["day"] == "2026-10-10" for x in bw["sessions"])
    assert not any(x["kind"] == "long" for x in bw["sessions"])
    nxt = next(w for w in weeks if w["start"] == "2026-10-12")
    early = [x for x in nxt["sessions"] if x["day"] <= "2026-10-15" and x["kind"] in O.RUN_KINDS]
    assert all(x["kind"] == "easy" for x in early)
