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


# ---- SP-280: a B race inside the A race's 減量期, or longer than the A race (Runna [482], 廠商規則) ----

def _runna(notes):
    return [n["text"] for n in notes if "Runna" in n["text"]]


def test_short_b_inside_the_a_taper_warns():
    a = ev("a", "2026-11-07", "A", **MARA)                   # road marathon: taper 14 days, from 10/24
    b = ev("b", "2026-10-31", **SHORT)
    notes = PR.b_hints([a, b], date(2026, 10, 26))
    txt = _runna(notes)
    assert len(txt) == 1 and "B 賽「b」落在 A 賽事「a」的減量期內（A 賽前 7 天）" in txt[0]
    assert "廠商規則" in txt[0] and "推估" in txt[0] and "比 A 賽還長" not in txt[0]
    assert all(n["level"] == "watch" and n["src"] == "race" for n in notes)
    assert not any("長距離" in t for t in (n["text"] for n in notes))     # short: the 28-day hint stays off


def test_short_b_outside_the_a_taper_says_nothing():
    a = ev("a", "2026-11-07", "A", **MARA)
    assert PR.b_hints([a, ev("b", "2026-10-17", **SHORT)], MON) == []      # 21 days out, taper is 14
    assert PR.b_hints([a, ev("b", "2026-10-23", **SHORT)], date(2026, 10, 19)) == []   # the day before it


def test_the_taper_window_is_the_app_taper_length():
    a = ev("a", "2026-11-07", "A", **MARA)
    b = ev("b", "2026-10-17", **SHORT)                        # 21 days before the A race
    assert _runna(PR.b_hints([a, b], MON, taper_pref=21))           # 課表偏好 21 days (road marathon)
    # the planned taper phase wins (a manual one: ends the day before the race)
    ph = [{"kind": "taper", "start": "2026-10-15", "end": "2026-11-06"}]
    assert _runna(PR.b_hints([a, b], MON, phases=ph))
    # a 2-day 百岳 tapers 7 days: a B race 9 days before is outside
    trip = ev("t", "2026-11-07", "A", kind="baiyue", days=2)
    assert PR.b_hints([trip, ev("b", "2026-10-29", **SHORT)], date(2026, 10, 26)) == []
    assert _runna(PR.b_hints([trip, ev("b", "2026-10-31", **SHORT)], date(2026, 10, 26)))


def test_b_longer_than_the_a_race_warns():
    a = ev("a", "2026-11-07", "A", **SHORT)                   # 10 km road, 0.8 h
    b = ev("b", "2026-10-17", **MEDIUM)                       # 3 h, 21 days before: outside the taper
    txt = [n["text"] for n in PR.b_hints([a, b], MON)]
    runna = [t for t in txt if "Runna" in t]
    assert len(runna) == 1 and "B 賽「b」比 A 賽事「a」還長（預估時間 3.0 h 對 0.8 h）" in runna[0]
    assert "廠商規則" in runna[0] and "推估" in runna[0]
    # the 28-day hint for a long B race stays as it was
    assert ("長距離 B 賽「b」在 A 賽事「a」前 21 天：A 賽前 2–4 週只建議較短、地形相似的熱身賽，"
            "或把它改成 C 賽輕鬆跑（CTS）") in txt
    far = ev("a", "2026-12-19", "A", **SHORT)                 # > 28 days: only the Runna hint
    txt = [n["text"] for n in PR.b_hints([far, b], MON)]
    assert len(txt) == 1 and "還長" in txt[0]
    # shorter than the A race: nothing
    assert PR.b_hints([ev("a", "2026-12-19", "A", **MARA), b], MON) == []


def test_b_inside_the_taper_and_longer_one_hint():
    a = ev("a", "2026-10-24", "A", **SHORT)
    b = ev("b", "2026-10-17", **MEDIUM)
    runna = _runna(PR.b_hints([a, b], MON))
    assert len(runna) == 1 and "減量期內（A 賽前 7 天），而且比 A 賽還長（預估時間 3.0 h 對 0.8 h）" in runna[0]


def test_b_longer_by_the_size_order():
    def e(**kw):
        return P.Event(id="x", name="x", date="2026-10-17", **kw)
    assert PR.b_longer(e(days=3, kind="baiyue"), e(days=2, kind="baiyue")) == "3 天對 2 天"
    assert PR.b_longer(e(days=2, kind="baiyue"), e(distance_km=50, climbing_m=3000)) == "2 天對 1 天"  # 多日 first
    assert PR.b_longer(e(distance_km=50, climbing_m=3000), e(days=2, kind="baiyue")) is None
    assert PR.b_longer(e(est_hours=5.0, distance_km=10), e(est_hours=4.0, distance_km=50)) == "預估時間 5.0 h 對 4.0 h"
    ep = PR.b_longer(e(distance_km=30, climbing_m=3000), e(distance_km=35, climbing_m=0))
    assert ep and ep.startswith("EP ")                       # no predicted time: EP
    assert PR.b_longer(e(distance_km=30), e(distance_km=21)) == "30 公里對 21 公里"   # no climb: km
    assert PR.b_longer(e(distance_km=21), e(distance_km=30)) is None
    assert PR.b_longer(e(), e(distance_km=30)) is None       # nothing to compare


def test_only_an_a_race_within_16_weeks_and_10_percent_longer():
    """Owner 2026-10-06 (both 推估): the in-taper / longer hints only look at an A race ≤ 16 weeks after
    the B race, and 「longer」 needs ≥ 10 % more."""
    assert PR.B_A_WINDOW_WEEKS == 16 and PR.B_LONGER_MIN == 0.10
    b = ev("b", "2026-10-17", **MEDIUM)                                    # 3 h
    a16 = ev("a", (date(2026, 10, 17) + dt.timedelta(weeks=16)).isoformat(), "A", **SHORT)
    a17 = ev("a", (date(2026, 10, 17) + dt.timedelta(weeks=16, days=1)).isoformat(), "A", **SHORT)
    assert _runna(PR.b_hints([a16, b], MON))                                # 16 weeks: still looked at
    assert PR.b_hints([a17, b], MON) == []                                  # further: not compared
    # a further A race doesn't hide a nearer one either way; the nearest A race within the window counts
    assert _runna(PR.b_hints([a16, a17, b], MON))

    def e(**kw):
        return P.Event(id="x", name="x", date="2026-10-17", **kw)
    assert PR.b_longer(e(est_hours=3.3, distance_km=10), e(est_hours=3.0, distance_km=10)) == "預估時間 3.3 h 對 3.0 h"
    assert PR.b_longer(e(est_hours=3.1, distance_km=10), e(est_hours=3.0, distance_km=10)) is None   # +3 %
    assert PR.b_longer(e(distance_km=33), e(distance_km=30)) == "33 公里對 30 公里"                   # +10 %
    assert PR.b_longer(e(distance_km=32), e(distance_km=30)) is None                                 # +6.7 %
    assert PR.b_longer(e(days=2, kind="baiyue"), e(distance_km=50, climbing_m=3000)) == "2 天對 1 天"  # days first


def test_baiyue_b_races_take_no_hint():
    """Owner 2026-10-06: a 百岳 B race is hiking-level with a low TSS — no B-race hint looks at it (in the
    A race's taper, longer by days, the 28-day long-B hint, too many B races); a trail B race still warns."""
    a = ev("a", "2026-10-24", "A", kind="race", distance_km=30, climbing_m=1500, est_hours=5.0)
    trip = ev("t", "2026-10-17", kind="baiyue", days=2, distance_km=25, climbing_m=2000)   # 7 days before, 2 days
    assert PR.b_longer(trip, a) == "2 天對 1 天"                     # it would be longer by days …
    assert PR.b_hints([a, trip], MON) == []                            # … but no hint at all
    assert PR.b_hints([a, trip, ev("t2", "2026-10-10", kind="baiyue", days=2)], MON) == []   # not "too many"
    trail = ev("b", "2026-10-17", kind="race", distance_km=40, climbing_m=2500, est_hours=7.0)
    txt = [n["text"] for n in PR.b_hints([a, trail], MON)]
    assert any("減量期內" in t and "比 A 賽還長" in t for t in txt)
    assert any("長距離 B 賽「b」在 A 賽事「a」前 7 天" in t for t in txt)


def test_runna_hints_in_english():
    from backend.i18n import use_locale
    with use_locale("en"):
        taper = _runna(PR.b_hints([ev("a", "2026-11-07", "A", **MARA), ev("b", "2026-10-31", **SHORT)],
                                  date(2026, 10, 26)))
        longer = _runna(PR.b_hints([ev("a", "2026-12-19", "A", **SHORT), ev("b", "2026-10-17", **MEDIUM)], MON))
        both = _runna(PR.b_hints([ev("a", "2026-10-24", "A", **SHORT), ev("b", "2026-10-17", **MEDIUM)], MON))
    assert taper == ['B race "b" falls in the taper of the A race "a" (7 days before it): the taper is when your '
                     'body recovers before the A race, and another race then affects it; make it a C race run easy, '
                     'or drop it (Runna: no B race in the 7–10 days before the A race; an app vendor rule, estimate)']
    assert "is longer than the A race \"a\" (predicted 3.0 h vs 0.8 h)" in longer[0]
    assert "(7 days before it) and is longer than the A race (predicted 3.0 h vs 0.8 h)" in both[0]


def test_week_plan_and_projection_show_the_taper_hint():
    """A short B race inside a 4-day 百岳's 10-day taper: the week plan and the projected week both say so."""
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as PJ
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY        # Wed 9/30
    ds = _history(TODAY)
    plan = _plan_with("2026-10-10", 4, TODAY)                 # A 嘉明湖 10/10, taper from 9/30
    plan.events = list(plan.events) + [ev("b", "2026-10-03", **SHORT)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    assert any("B 賽「b」落在 A 賽事「嘉明湖」的減量期內" in n["text"] for n in wp["notes"])

    plan2 = _plan_with("2026-10-24", 4, TODAY)                # taper from 10/14
    plan2.events = list(plan2.events) + [ev("b", "2026-10-17", **SHORT)]
    ds.plan = plan2
    wp2 = O.week_plan(ds, Status(ds, plan2, TODAY, prefs=PP.Prefs()).compute(), TODAY)
    assert not any("Runna" in n["text"] for n in wp2["notes"])
    weeks = PJ.project_weeks(wp2, _phases(plan2, TODAY), date(2026, 10, 18), events=plan2.events)
    bw = next(w for w in weeks if w["start"] == "2026-10-12")
    assert any("B 賽「b」落在 A 賽事「嘉明湖」的減量期內（A 賽前 7 天）" in n["text"] for n in bw.get("notes") or [])


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
