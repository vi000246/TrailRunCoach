"""
SP-650 engine/race_readiness.py: the next A race's 賽前完備程度 — a week (賽前第 10 → 1 週) × item board,
each cell the target the plan engine sets that week and what the activities reached; the longest
continuous climb and the race simulation detected from activities. Synthetic rows only.
"""
import datetime as dt
from datetime import date

from backend.engine import race_readiness as RD
from backend.engine import specific_phase as SP
from backend.engine.algorithms.chart_metrics import course_constant

START = date(2026, 12, 12)            # Sat; its week's Monday 12/7 = 賽前第 1 週
TODAY = date(2026, 11, 25)            # Wed of 賽前第 3 週 (Monday 11/23)


def monday(w: int) -> date:
    return date(2026, 12, 7) - dt.timedelta(weeks=w - 1)


def race(**kw) -> dict:
    """8 h, 50 km ↑3000 ↓3000; the GPX's longest continuous climb 1000 m."""
    r = {"id": "e1", "name": "測試越野", "start": START.isoformat(), "kind": "race", "days": 1,
         "hours": 8.0, "km": 50.0, "climb_m": 3000.0, "descent_m": 3000.0,
         "cc": course_constant(8.0, 50.0, 3000.0, 3000.0), "per_day": None,
         "day": {"hours": 8.0, "km": 50.0, "climb_m": 3000.0, "descent_m": 3000.0},
         "h_per_ekm": 8.0 / (50.0 + 30.0), "features": {"climb": {"dz": 1000.0, "km": 6.0, "grade": 16.7}}}
    r.update(kw)
    return r


def act(day: date, hours: float, km: float, climb: float, climb_dz=None, foot=True) -> dict:
    return {"date": day, "km": km, "climb_m": climb, "descent_m": climb, "hours": hours, "minutes": hours * 60.0,
            "idx": hash((day, hours)) % 10000, "foot": foot, "climb_dz": climb_dz}


def at_share(share: float, rc: dict, pace: float = 1.0) -> tuple[float, float, float]:
    """(hours, km, climb) of a run whose コース定数 is `share` of the race's 6-h-capped aim, at the race's
    hours per effort-km × `pace`."""
    aim = rc["cc"] * min(1.0, 6.0 / rc["hours"]) * share
    # the race's mix: km : climb = 50 : 3000, climb = descent; solve cc(h, km, c, c) = aim with h = hpe × ekm × pace
    unit = course_constant(rc["h_per_ekm"] * 80.0 * pace, 50.0, 3000.0, 3000.0)   # the race's own mix, scaled
    k = aim / unit
    return rc["h_per_ekm"] * 80.0 * pace * k, 50.0 * k, 3000.0 * k


def stage(b: dict, sid: str) -> dict:
    return next(s for s in b["stages"] if s["id"] == sid)


def item(b: dict, iid: str) -> dict:
    return next(i for s in b["stages"] for i in s["items"] if i["id"] == iid)


def cell(b: dict, iid: str, w: int):
    return item(b, iid)["cells"].get(w)


def ctl_series(days: int, value=lambda i: 40.0 + i * 0.05, end=TODAY):
    """`days` daily CTL values ending on `end`."""
    return [(end - dt.timedelta(days=days - 1 - i), value(i)) for i in range(days)]


def board(acts, rc=None, today=TODAY, **kw):
    kw.setdefault("b2b", True)
    return RD.board(rc or race(), today, acts, **kw)


# ---- 驗收 1: three stages, items met, 2/3, unlocking -------------------------------------------

def test_three_stages_with_their_items():
    b = board([act(monday(10), 1.0, 8, 300)])
    assert [s["id"] for s in b["stages"]] == ["volume", "validation", "race_state"]
    assert [i["id"] for i in stage(b, "volume")["items"]] == \
        ["long", "weekly_long", "long_climb", "terrain", "b2b", "race_sim"]
    assert [i["id"] for i in stage(b, "validation")["items"]] == ["repeat", "ctl_top", "uphill", "high_test"]
    assert [i["id"] for i in stage(b, "race_state")["items"]] == \
        ["taper_volume", "climb_early", "ctl_down_days", "race_tsb"]
    assert [i["id"] for i in b["weekly"]] == ["ramp", "injury"]          # RPE only once something is rated
    assert "biggest_week" not in str(b)
    for s in b["stages"]:
        assert {"done", "total", "passed", "unlocked", "unlock_date", "state", "label", "need"} <= set(s)


def test_two_thirds_passes_a_stage():
    assert RD.passed(4, 6) and not RD.passed(3, 6)
    assert RD.passed(2, 3) and not RD.passed(1, 3)
    assert not RD.passed(0, 0)
    assert RD.need(3, 6) == 1 and RD.need(4, 6) == 0


def test_race_state_unlocks_when_the_taper_starts():
    b = board([act(monday(9), 1.0, 8, 200)])
    rs = stage(b, "race_state")
    assert rs["unlocked"] is False and rs["state"] == "lock"
    assert rs["unlock_date"] == (START - dt.timedelta(days=10)).isoformat()      # planning.TAPER_DAYS
    assert all(c["state"] == "tap" for i in rs["items"] for c in i["cells"].values())
    assert rs["total"] == 0 or not any(c["counts"] for i in rs["items"] for c in i["cells"].values())
    b = board([act(monday(9), 1.0, 8, 200)], today=START - dt.timedelta(days=5))
    assert stage(b, "race_state")["unlocked"] is True and stage(b, "race_state")["state"] != "lock"
    # 表現驗證 opens at 賽前第 6 週
    assert stage(board([act(monday(10), 1.0, 8, 200)], today=monday(8)), "validation")["unlocked"] is False
    assert stage(b, "validation")["unlocked"] is True


# ---- 驗收 2: weekly targets / actual / state, targets from the plan engine; this week -----------

def test_weekly_cells_and_targets_from_the_plan_engine():
    b = board([act(monday(10), 1.0, 8, 300)])
    assert [w["week"] for w in b["weeks"]] == list(range(10, 0, -1))
    assert [w["phase"] for w in b["weeks"]] == ["specific"] * 8 + ["taper"] * 2
    assert next(w for w in b["weeks"] if w["current"])["week"] == 3
    long = item(b, "long")["cells"]
    assert sorted(long) == list(range(3, 11))
    assert all(long[w]["target"] == SP.FRAC[w] for w in long)
    assert sorted(item(b, "race_sim")["cells"]) == sorted(SP.SIM_WEEKS)
    for c in long.values():
        assert {"state", "show", "detail", "target", "actual", "counts"} <= set(c)


def test_long_day_cells_and_met_at_70_percent_of_the_hardest_day():
    rc = race()
    h, km, cl = at_share(SP.FRAC[6] * 0.95, rc)
    h7, km7, cl7 = at_share(SP.FRAC[7] * 0.5, rc)
    b = board([act(monday(6) + dt.timedelta(days=5), h, km, cl), act(monday(7) + dt.timedelta(days=5), h7, km7, cl7)], rc)
    assert cell(b, "long", 6)["state"] == "done" and cell(b, "long", 7)["state"] == "miss"
    assert cell(b, "long", 3)["state"] == "due" and not cell(b, "long", 3)["counts"]
    assert item(b, "long")["met"] is True                   # 0.71 ≥ 0.70
    b = board([act(monday(7) + dt.timedelta(days=5), h7, km7, cl7)], rc)
    assert item(b, "long")["met"] is False


def test_this_week_is_the_current_weeks_due_cells():
    b = board([act(monday(9), 1.0, 8, 200)])
    due = {(t["item"]) for t in b["this_week"]}
    cur = {i["id"] for s in b["stages"] for i in s["items"] if (i["cells"].get(3) or {}).get("state") == "due"}
    assert due == cur and "long" in due


# ---- 驗收 3: detection, gray for no data, preview --------------------------------------------

def test_longest_climb_of_a_profile():
    km = [0, 1, 2, 3, 4, 5, 6]
    assert RD.longest_climb(km, [100, 500, 480, 780, 280, 480, 480]) == 680
    assert RD.longest_climb(km, [100, 500, 450, 750, 250, 450, 450]) == 400
    assert RD.longest_climb([0, 1], [100, 100]) == 0
    assert RD.longest_climb([], []) is None


def test_long_climb_is_a_share_of_the_races_longest_climb():
    b = board([act(monday(9) + dt.timedelta(days=2), 2.0, 12, 800, climb_dz=520),
               act(monday(7) + dt.timedelta(days=2), 2.0, 12, 800, climb_dz=650)])
    assert {w: c["target"] for w, c in item(b, "long_climb")["cells"].items()} == RD.CLIMB_MILESTONES
    assert cell(b, "long_climb", 9)["state"] == "done" and cell(b, "long_climb", 7)["state"] == "miss"
    assert item(b, "long_climb")["met"] is False                   # 650 < 70 % of 1000
    b = board([act(monday(7), 2.0, 12, 800, climb_dz=720)])
    assert item(b, "long_climb")["met"] is True
    b2 = board([act(monday(7), 2.0, 12, 800, climb_dz=720)],
               race(features={"climb": {"dz": 2000.0, "km": 10.0, "grade": 20.0}}))
    assert item(b2, "long_climb")["met"] is False                  # the same climb, a race twice as big


def test_long_climb_without_the_race_gpx_is_gray_and_not_counted():
    b = board([act(monday(9), 2.0, 12, 800, climb_dz=520)], race(features=None))
    lc = item(b, "long_climb")
    assert lc["reason"] and "GPX" in lc["reason"] and lc["met"] is None
    assert all(c["state"] == "unk" and not c["counts"] for c in lc["cells"].values())
    assert stage(b, "volume")["total"] == 5


def test_race_simulation_is_a_long_day_at_race_pace():
    rc = race()
    h, km, cl = at_share(SP.FRAC[6], rc)
    sim = act(monday(6) + dt.timedelta(days=5), h, km, cl)
    assert RD.is_race_sim(sim, rc, SP.FRAC[6])
    assert not RD.is_race_sim(dict(sim, hours=h * 0.6), rc, SP.FRAC[6])
    assert not RD.is_race_sim(act(sim["date"], h * 0.4, km * 0.4, cl * 0.4), rc, SP.FRAC[6])
    b = board([sim], rc)
    assert cell(b, "race_sim", 6)["state"] == "done" and item(b, "race_sim")["met"] is True
    assert sum(c["counts"] for c in item(b, "race_sim")["cells"].values()) == 1
    crit = item(b, "race_sim")["crit"] + cell(b, "race_sim", 6)["detail"]
    for w in ("補給", "吃", "信心", "裝備"):
        assert w not in crit


def test_terrain_and_weekly_long():
    rc = race()                                       # 60 m/km
    h, km, cl = at_share(SP.FRAC[6], rc)
    acts = [act(monday(w) + dt.timedelta(days=5), h, km, cl) for w in (7, 6, 5, 4)]
    b = board(acts, rc)
    assert all(cell(b, "terrain", w)["state"] == "done" for w in (7, 6, 5, 4))
    assert item(b, "terrain")["met"] is True and item(b, "weekly_long")["met"] is True
    flat = [act(monday(w) + dt.timedelta(days=5), 1.0, 10, 50) for w in (7, 6, 5, 4)]
    b = board(flat, rc)
    assert item(b, "terrain")["met"] is False and item(b, "weekly_long")["met"] is False


def test_b2b_weeks_from_the_rules_or_accepted():
    assert sorted(item(board([]), "b2b")["cells"]) == [5]          # Saturday race: readiness' line
    b = RD.board(race(start="2026-12-13"), TODAY, [], b2b=True)
    assert sorted(item(b, "b2b")["cells"]) == [4]
    d = monday(6) + dt.timedelta(days=5)
    b = board([act(d, 3.0, 20, 1000), act(d + dt.timedelta(days=1), 2.0, 14, 600)],
              accepted_b2b=[{"week": monday(6).isoformat()}])
    assert cell(b, "b2b", 6)["state"] == "done" and item(b, "b2b")["met"] is True


def test_best_performance_repeats_with_power_and_without():
    d = monday(5)
    good = [{"date": d - dt.timedelta(days=i * 3), "300": 300 - i, "1200": 270 - i, "3600": 250 - i} for i in range(6)]
    b = board([act(monday(9), 1, 8, 200)], perf={"power": good}, first_day=date(2025, 1, 1))
    assert item(b, "repeat")["met"] is True and item(b, "repeat")["mode"] == "power"
    bad = [dict(x, **{"1200": 300 if i == 0 else 250}) for i, x in enumerate(good)]
    b = board([act(monday(9), 1, 8, 200)], perf={"power": bad}, first_day=date(2025, 1, 1))
    assert item(b, "repeat")["met"] is False
    vam = [{"date": d - dt.timedelta(days=i * 3), "vam": 700 - i * 3} for i in range(6)]
    b = board([act(monday(9), 1, 8, 200)], perf={"vam": vam}, first_day=date(2025, 1, 1))
    assert item(b, "repeat")["met"] is True and item(b, "repeat")["mode"] == "climb"
    b = board([act(monday(9), 1, 8, 200)], perf={"vam": vam[:3]}, first_day=date(2025, 1, 1))
    assert item(b, "repeat")["met"] is None and item(b, "repeat")["reason"]


def test_chronic_load_in_the_top_fifth_of_the_own_year():
    b = board([act(monday(9), 1, 8, 200)], ctl=ctl_series(400), first_day=date(2025, 1, 1))
    assert item(b, "ctl_top")["met"] is True                        # rising all year: today is the top
    b = board([act(monday(9), 1, 8, 200)], ctl=ctl_series(400, lambda i: 80.0 - i * 0.05), first_day=date(2025, 1, 1))
    assert item(b, "ctl_top")["met"] is False


def test_under_90_days_of_data_is_gray():
    b = board([act(monday(9), 1, 8, 200)], ctl=ctl_series(60), first_day=TODAY - dt.timedelta(days=60))
    v = stage(b, "validation")
    assert all(i["met"] is None and i["reason"] for i in v["items"] if i["id"] != "high_test")
    assert v["total"] <= 1


def test_uphill_faster_than_8_weeks_ago_and_a_high_zone_check():
    pts = [{"date": TODAY - dt.timedelta(days=d), "vam": 600 if d < 56 else 550} for d in range(3, 112, 7)]
    b = board([act(monday(9), 1, 8, 200)], perf={"vam": pts}, first_day=date(2025, 1, 1),
              tests=[monday(5) + dt.timedelta(days=5)])
    assert item(b, "uphill")["met"] is True and item(b, "high_test")["met"] is True


def test_no_records_is_gray_and_a_preview_before_ten_weeks():
    b = board([])
    assert b["due"] == 0 and b["level"] == "unknown"
    assert cell(b, "long", 7)["state"] == "unk" and cell(b, "long", 7)["detail"]
    p = board([], today=date(2026, 9, 1))
    assert p["preview"] is True and p["specific_from"] == monday(10).isoformat() and "專項期從" in p["text"]
    assert p["due"] == 0 and not p["this_week"]
    assert all(c["state"] in ("fut", "tap") for s in p["stages"] for i in s["items"] for c in i["cells"].values())
    assert RD.board(None, TODAY, [])["race"] is None


def test_behind_by_the_weekly_targets_two_thirds():
    assert RD.level_of(9, 6) == "on_track" and RD.level_of(9, 5) == "behind" and RD.level_of(0, 0) == "unknown"


# ---- 每週都看 ---------------------------------------------------------------------------------

def test_weekly_watch_ramp_injury_rpe():
    inj = [{"id": 1, "onset_date": monday(5).isoformat(), "status": "open", "area": "knee", "severity": "moderate"}]
    rpe = [{"date": monday(7), "kind": "long", "rpe": 4}, {"date": monday(6), "kind": "long", "rpe": 7},
           {"date": monday(5), "kind": "easy", "rpe": 3}]
    jump = ctl_series(120, lambda i: 40.0 if i < 110 else 40.0 + (i - 109) * 2.0)   # +20 in the last 10 days
    b = board([act(monday(9), 1, 8, 200)], injuries=inj, rpe=rpe, ctl=jump)
    wk = {i["id"]: i for i in b["weekly"]}
    assert list(wk) == ["ramp", "injury", "rpe"]
    assert wk["injury"]["cells"][5]["state"] == "bad" and wk["injury"]["cells"][7]["state"] == "ok"
    assert wk["rpe"]["cells"][6]["state"] == "watch"                 # long 4 → 7
    assert 5 not in wk["rpe"]["cells"] or wk["rpe"]["cells"][5]["state"] == "ok"
    assert wk["ramp"]["cells"][3]["state"] in ("watch", "bad")


# ---- 驗收 4: shares of the race / own values; no names or sources in the text -------------------

def test_display_text_has_no_names_or_sources():
    import json
    b = board([act(monday(9), 1, 8, 200, climb_dz=300)], ctl=ctl_series(400), first_day=date(2025, 1, 1))
    s = json.dumps(b, ensure_ascii=False, default=str)
    for w in ("Koop", "Uphill", "UA", "Golich", "江晏慶", "Daniels", "Friel", "研討會", "src", "Bosquet", "CTS"):
        assert w not in s, w


def test_compute_reads_the_next_a_race_from_the_plan(monkeypatch):
    from backend.engine import b2b as B2B
    from backend.engine import injuries as INJ
    from backend.engine import primary_sport as PSP
    from backend.engine.panels import race_refs as RR
    from backend.engine.planning import Event, Plan
    monkeypatch.setattr(RR, "calculator_hours", lambda e, c=None: None)
    monkeypatch.setattr(RR, "stored_course", lambda e: None)
    monkeypatch.setattr(SP, "gpx_features", lambda eid, hpe: None)
    monkeypatch.setattr(RD, "inputs", lambda *a, **k: {"acts": [act(monday(9), 1.0, 8, 200)]})
    monkeypatch.setattr(B2B, "load_accepted", lambda user_id=1: [])
    monkeypatch.setattr(INJ, "load_events", lambda *a, **k: [])
    monkeypatch.setattr(PSP, "effective", lambda *a, **k: "trail")
    plan = Plan(events=[Event("e1", "測試越野", START.isoformat(), kind="race", priority="A", distance_km=50,
                              climbing_m=3000, est_hours=8.0)])
    b = RD.compute(plan, None, TODAY)
    assert b["race"]["id"] == "e1" and b["weeks_out"] == 3 and len(b["stages"]) == 3
    assert RD.compute(Plan(events=[]), None, TODAY)["race"] is None
