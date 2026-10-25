"""
SP-672 race_readiness follow-ups: weeks before the data starts (or with no activity at all) are gray and not
counted; the targets of later / taper cells come from the board (readiness.js no longer works them out);
every non-count item names its chart (an existing one, or the card's own small chart); the judges that were
stricter than the plan (the long-day cells in the plan's own unit, the terrain band, the 20-min repeat).
Synthetic rows (SP-650's helpers).
"""
import datetime as dt
import json
from datetime import date
from pathlib import Path

from backend.engine import race_readiness as RD
from backend.engine import specific_phase as SP
from backend.tests.test_race_readiness_sp650 import START, TODAY, act, board, cell, ctl_series, item, monday, race

ROOT = Path(__file__).resolve().parents[1]
COUNT_ITEMS = {"weekly_long", "terrain", "b2b", "race_sim", "high_test"}
LINKED = {"long", "ctl_top", "uphill"}
OWN = {"long_climb", "repeat"}


def _all_cells(b):
    return [c for s in b["stages"] for i in s["items"] for c in i["cells"].values()]


# ---- 1. before the data / a week without any activity: 資料不足, not counted --------------------------

def test_weeks_before_the_first_activity_are_gray_and_not_counted():
    acts = [act(monday(w) + dt.timedelta(days=2), 1.0, 8, 50) for w in (7, 6, 5, 4)]
    b = board(acts, first_day=monday(7))
    for w in (10, 9, 8):
        c = cell(b, "long", w)
        assert c["state"] == "unk" and not c["counts"] and "資料" in c["detail"], (w, c)
        assert cell(b, "weekly_long", w)["state"] == "unk"
    assert cell(b, "long", 7)["state"] == "miss" and cell(b, "long", 7)["counts"]
    assert not any(c["counts"] and c["state"] == "unk" for c in _all_cells(b))


def test_a_past_week_without_any_activity_is_gray_but_a_bike_week_is_judged():
    acts = [act(monday(9) + dt.timedelta(days=2), 1.0, 8, 50), act(monday(7) + dt.timedelta(days=2), 1.0, 30, 50, foot=False)]
    b = board(acts, first_day=date(2025, 1, 1))
    assert cell(b, "long", 8)["state"] == "unk" and not cell(b, "long", 8)["counts"]       # nothing at all
    assert cell(b, "long", 7)["state"] == "miss" and cell(b, "long", 7)["counts"]          # a ride: no long run
    assert cell(b, "long", 3)["state"] == "due"                                            # this week: still due
    due = sum(1 for c in _all_cells(b) if c["counts"])
    assert b["due"] == due


# ---- 2. targets of later / taper cells from the board --------------------------------------------

def test_taper_and_later_cells_show_their_target():
    # SP-677: the taper's checks are the 減量期 card's (taper()); locked before it, showing their targets
    t = {c["id"]: c for c in RD.taper(race(), TODAY, [act(monday(9), 1.0, 8, 200)])["checks"]}
    assert t["taper_volume"]["state"] == "lock" and t["taper_volume"]["show"] == "40–59 %"
    assert t["climb_early"]["show"] == "≤59%"
    assert t["ctl_down_days"]["show"] == "≤ 10"
    assert t["race_tsb"]["show"] == "+10～+35 %"
    b = board([act(monday(9), 1.0, 8, 200)], today=monday(8) + dt.timedelta(days=2), first_day=date(2025, 1, 1))
    assert cell(b, "long", 6)["state"] == "fut" and cell(b, "long", 6)["show"] == "75%"
    assert cell(b, "repeat", 5)["show"] == "≥95%" and cell(b, "ctl_top", 5)["show"] == "前 20%"


def test_the_card_script_does_not_work_out_targets_itself():
    js = (ROOT / "static" / "readiness.js").read_text("utf-8")
    assert "Array.isArray(t)" not in js and "c.target" not in js


def test_top_of_the_own_year_is_not_top_zero_percent():
    b = board([act(monday(9), 1, 8, 200)], ctl=ctl_series(400), first_day=date(2025, 1, 1))
    assert all("前 0%" not in c["show"] for c in item(b, "ctl_top")["cells"].values())


# ---- 3. a chart per non-count item ---------------------------------------------------------------

def test_every_non_count_item_names_a_chart_and_count_items_none():
    b = board([act(monday(9), 1.0, 8, 200)], first_day=date(2025, 1, 1))
    for s in b["stages"]:
        for i in s["items"]:
            ch = i.get("chart")
            if i["id"] in COUNT_ITEMS:
                assert ch is None, i["id"]
            elif i["id"] in LINKED:
                assert ch and ch["id"] and ch["begin"] <= ch["end"], (i["id"], ch)
            else:
                assert i["id"] in OWN and ch and ch.get("own") == i["id"], (i["id"], ch)
    views = json.loads((ROOT.parent / "views" / "periodization.json").read_text("utf-8"))
    views2 = json.loads((ROOT.parent / "views" / "training.json").read_text("utf-8"))
    ids = {c.get("id") for v in (views, views2) for d in v["dashboards"] for c in d["charts"]}
    for s in b["stages"]:
        for i in s["items"]:
            if i["id"] in LINKED:
                assert i["chart"]["id"] in ids, i["chart"]


def test_road_long_day_links_the_long_run_chart():
    rc = race(kind="road", km=21.0, climb_m=0.0, descent_m=0.0, features=None,
              day={"hours": 2.0, "km": 21.0, "climb_m": 0.0, "descent_m": 0.0})
    b = RD.board(rc, TODAY, [act(monday(9), 1.0, 10, 0)], sport="road")
    assert item(b, "long")["chart"]["id"] == "long-run-km-specific"
    assert item(b, "long")["chart"]["id"] != "route-difficulty-specific"


def test_repeat_carries_its_points_and_the_longest_climb_its_weeks():
    d = monday(5)
    pw = [{"date": d - dt.timedelta(days=i * 3), "300": 300 - i, "1200": 270 - i, "3600": None} for i in range(7)]
    b = board([act(monday(9), 1, 8, 200, climb_dz=400)], perf={"power": pw}, first_day=date(2025, 1, 1))
    rp = item(b, "repeat")
    assert rp["mode"] == "power" and rp["key"] == "1200"
    pts = rp["points"]
    assert set(pts) == {"300", "1200", "3600"} and len(pts["1200"]) == 7 and pts["3600"] == []
    assert {"date", "v"} <= set(pts["1200"][0])
    lc = item(b, "long_climb")
    assert lc["series"] and {"week", "monday", "m"} <= set(lc["series"][0])
    assert next(x for x in lc["series"] if x["week"] == 9)["m"] == 400


def test_the_panel_turns_chart_ids_into_viewer_links():
    from backend.api import wko5views as WV
    href = WV.readiness_chart_href({"id": "uphill-vam", "begin": "2026-08-01", "end": "2026-11-25"})
    assert href.startswith("/api/v1/app/viewer?") and "chart=" in href and "dash=" in href
    assert "begin=2026-08-01" in href and "end=2026-11-25" in href
    assert WV.readiness_chart_href({"own": "repeat"}) is None
    viewer = (ROOT / "static" / "charts.html").read_text("utf-8")
    assert 'qp.get("begin")' in viewer and 'qp.get("end")' in viewer


# ---- 4. the judges in the plan's own unit / bands ------------------------------------------------

def test_trail_long_day_cells_are_the_plans_minutes():
    """The plan sets the trail long day in minutes (frac × the race day's hours, ≤ 6 h: specific_phase.
    trail_aim); a flat run of that length is that week's long day although its course constant is lower."""
    rc = race()                                       # 8 h → 6-h cap
    t = SP.FRAC[7]
    b = board([act(monday(7) + dt.timedelta(days=5), 6.0 * t, 6.0 * t * 9, 50)], rc, first_day=date(2025, 1, 1))
    c = cell(b, "long", 7)
    assert c["state"] == "done", c
    assert "小時" in c["detail"]
    assert item(b, "long")["met"] is False            # the difficulty (70 % of the race's constant) stays
    assert cell(b, "weekly_long", 7)["state"] == "done"


def test_terrain_takes_steeper_and_a_third_flatter():
    rc = race()                                       # 60 m/km
    for mpk, ok in ((38, False), (42, True), (130, True), (160, False)):
        b = board([act(monday(7) + dt.timedelta(days=5), 2.0, 300 / mpk * 4, 1200)], rc, first_day=date(2025, 1, 1))
        assert (cell(b, "terrain", 7)["state"] == "done") is ok, mpk


def test_repeat_is_the_20_minute_best_at_95_percent():
    d = monday(5)
    # 20-min within 5 % (0.96), the 5-min spread wider (0.90): the 20-min one decides
    pw = [{"date": d - dt.timedelta(days=i * 3), "300": 330 if i == 0 else 297, "1200": 270 if i == 0 else 259,
           "3600": None} for i in range(6)]
    b = board([act(monday(9), 1, 8, 200)], perf={"power": pw}, first_day=date(2025, 1, 1))
    assert item(b, "repeat")["met"] is True and item(b, "repeat")["key"] == "1200"
    assert RD.REPEAT_OK == 0.95


# ---- SP-652 Q1: the season plan's B race card keeps only 可行性 ------------------------------------

def test_season_plan_b_race_card_loses_the_old_readiness_row():
    from backend.api.overview import drop_old_readiness
    from backend.tests.test_plan_readiness_line_sp652 import RACE, _render, needs_node  # noqa: F401
    a = {**RACE, "event_id": "a1", "priority": "A"}
    b = {**RACE, "event_id": "b1", "priority": "B", "name": "貓空越野 17K",
         "hints": [{"id": "PL15", "text": "舊的完備程度"}, {"id": "PL9", "text": "建議"}]}
    line = {"race_id": "a1", "text": "賽前準備：第 1 段〈專項量〉1/4（還差 2 項）", "phase": "build", "href": "/v"}
    out = drop_old_readiness([a, b], line)
    assert out[0]["readiness"] == RACE["readiness"]                 # the A card: the line replaces it on the page
    assert out[1]["readiness"] is None and [h["id"] for h in out[1]["hints"]] == ["PL9"]
    assert drop_old_readiness([a, b], None) == [a, b]               # no line (the board failed): kept
    import shutil
    if shutil.which("node"):
        h = _render([out[1]])[0]
        assert "不足" not in h and "最長 3 h" not in h and "Koop" not in h
        assert "完備程度" not in h and "來得及" in h                  # only the 可行性 row


# ---- owner check (SP-672 follow-up): a race known by its GPX alone; the race decides trail vs road ---------

def test_an_a_race_with_only_a_gpx_gets_a_board(monkeypatch):
    from backend.engine.planning import Event, Plan
    monkeypatch.setattr(SP, "gpx_features", lambda eid, hpe: None)
    gpx = {"totals": {"km": 18.7, "gain_m": 1488.0, "loss_m": 1489.0},
           "days": [{"day": 1, "km": 18.7, "gain_m": 1488.0, "loss_m": 1489.0}], "split_source": "single", "filename": "x.gpx"}
    e = Event("g1", "北插天山", START.isoformat(), kind="race", priority="A")          # no km typed in the plan
    rc = SP.race_day(Plan(events=[e]), TODAY, lambda ev, c: [2.86], gpx=lambda ev: gpx)
    assert rc is not None and rc["id"] == "g1" and rc["km"] == 18.7
    # neither km nor a GPX: the next A race that can be measured
    later = Event("e2", "後面那場", (START + dt.timedelta(days=30)).isoformat(), kind="race", priority="A",
                  distance_km=20, climbing_m=1000, est_hours=3.0)
    rc = SP.race_day(Plan(events=[e, later]), TODAY, None, gpx=lambda ev: None)
    assert rc is not None and rc["id"] == "e2"


def test_a_mountain_race_is_trail_whatever_the_main_sport():
    rc = race()                                       # 50 km ↑3000
    b = RD.board(rc, TODAY, [act(monday(9), 1.0, 8, 200)], sport="road", first_day=date(2025, 1, 1))
    assert b["race"]["sport"] == "trail"
    assert "terrain" in [i["id"] for s in b["stages"] for i in s["items"]]
    flat = race(kind="race", climb_m=100.0, descent_m=100.0,
                day={"hours": 2.0, "km": 21.0, "climb_m": 100.0, "descent_m": 100.0}, km=21.0, hours=2.0, features=None)
    assert RD.board(flat, TODAY, [], sport="trail")["race"]["sport"] == "road"     # < 200 m: judged on km


# ---- owner answers (2026-10-16): the taper length, 本週要做 says what to do, a deep link's range once -------

def test_chronic_load_may_fall_as_long_as_the_taper():
    falling = ctl_series(400, lambda i: 60.0 if i < 391 else 60.0 - (i - 390) * 0.5, end=START - dt.timedelta(days=1))
    today = START - dt.timedelta(days=1)
    def down(**kw):
        t = RD.taper(race(), today, [act(monday(1), 1.0, 8, 50)], ctl=falling, **kw)
        return t, next(c for c in t["checks"] if c["id"] == "ctl_down_days")
    t, c = down()
    assert c["value"] == 9 and c["state"] == "ok" and "10" in c["target"]       # 9 days < 10 (was ≤ 7)
    t, c = down(taper_days=14)
    assert t["taper_from"] == (START - dt.timedelta(days=14)).isoformat() and "14" in c["target"]


def test_compute_takes_the_taper_length_from_the_plan_prefs(monkeypatch):
    from backend.engine import planning as P
    seen = {}
    monkeypatch.setattr(P, "taper_days", lambda e, pref=None: 13)
    monkeypatch.setattr(P, "taper_days_setting", lambda *a, **k: 13)
    orig = RD.taper
    monkeypatch.setattr(RD, "taper", lambda *a, **k: seen.update(k) or orig(*a, **k))
    from backend.tests.test_race_readiness_sp650 import test_compute_reads_the_next_a_race_from_the_plan as t
    t(monkeypatch)                                       # the patches stay: compute_taper on the same plan
    from backend.engine.planning import Event, Plan
    plan = Plan(events=[Event("e1", "測試越野", START.isoformat(), kind="race", priority="A", distance_km=50,
                              climbing_m=3000, est_hours=8.0)])
    assert RD.compute_taper(plan, None, TODAY)["taper_days"] == 13 and seen.get("taper_days") == 13


def test_this_week_says_what_to_do_not_what_was_reached():
    rc = race()
    acts = [act(monday(3) + dt.timedelta(days=1), 2.0, 10, 100, climb_dz=50)]
    b = board(acts, rc, first_day=date(2025, 1, 1))
    tw = {t["item"]: t["detail"] for t in b["this_week"]}
    assert tw["long"] == "3.9 小時（還差 1.9 小時）", tw                     # 65 % of 6 h, 2 h done
    assert tw["terrain"].startswith("排一次每公里爬升 40–150 m"), tw
    for iid, txt in tw.items():
        assert "做到" not in txt and " 0 次" not in txt, (iid, txt)


def test_a_deep_links_range_is_used_once():
    viewer = (ROOT / "static" / "charts.html").read_text("utf-8")
    save = viewer[viewer.index("function save()"):viewer.index("function restore()")]
    assert "S.keepRange" in save and "kr.range" in save and "kr.begin" in save
    deep = viewer[viewer.index('qp.get("begin")'):viewer.index('qp.get("begin")') + 400]
    assert "S.keepRange = {" in deep
    pre = viewer[viewer.index("function preset(d)"):viewer.index("function preset(d)") + 120]
    assert "S.keepRange = null" in pre
    assert '$("begin").onchange = $("end").onchange = () => { S.keepRange = null;' in viewer


def test_uphill_speed_is_the_uphill_charts_measure():
    """The card judged the whole session's VAM (flats and descents in the hour), which drops on every long
    mountain day; it now uses the 上坡腳程 chart's uphill-only climbing speed."""
    views = json.loads((ROOT.parent / "views" / "periodization.json").read_text("utf-8"))
    ch = next(c for d in views["dashboards"] for c in d["charts"] if c.get("id") == "uphill-vam")
    assert all(RD.UPHILL_EXPR[3:] in s["expression"] for s in ch["series"]), RD.UPHILL_EXPR
    src = (ROOT / "engine" / "race_readiness.py").read_text("utf-8")
    assert 'st.ev.evaluate(UPHILL_EXPR' in src and 'st.m(w, "vam")' not in src


def test_weekly_long_has_the_long_days_tolerance():
    rc = race()                                       # 6-h aim
    need = 6.0 * SP.FRAC[7] * RD.WEEKLY_LONG_SHARE
    b = board([act(monday(7) + dt.timedelta(days=5), need * 0.92, 10, 50)], rc, first_day=date(2025, 1, 1))
    assert cell(b, "weekly_long", 7)["state"] == "done"
    b = board([act(monday(7) + dt.timedelta(days=5), need * 0.85, 10, 50)], rc, first_day=date(2025, 1, 1))
    assert cell(b, "weekly_long", 7)["state"] == "miss"


# ---- 第 3 輪 (owner 2026-10-16): high_test = matching the own 90-day best for ≥ 20 min in the window ----

def _pw(day, w20):
    return {"date": day, "300": None, "1200": w20, "3600": None}


def test_high_test_done_when_a_window_session_matches_the_90_day_best_power():
    win = monday(5) + dt.timedelta(days=2)
    prior = [_pw(monday(5) - dt.timedelta(days=d), 300 - d % 7) for d in (20, 40, 60)]   # best 299 (d=20 → 300-6)
    best = max(p["1200"] for p in prior)
    b = board([act(monday(9), 1, 8, 200)], perf={"power": prior + [_pw(win, best)]}, first_day=date(2025, 1, 1))
    assert cell(b, "high_test", 5)["state"] == "done" and item(b, "high_test")["met"] is True
    every = [act(monday(w) + dt.timedelta(days=1), 1, 8, 200) for w in range(10, 2, -1)]
    b = board(every, perf={"power": prior + [_pw(win, best - 1)]}, first_day=date(2025, 1, 1))
    c = cell(b, "high_test", 4)
    assert c["state"] == "miss" and item(b, "high_test")["met"] is False
    assert "安排一次測驗驗證一下" in c["detail"] and f"{best} W" in c["detail"] and f"{best - 1} W" in c["detail"], c
    assert "安排一次測驗驗證一下" in next(t["detail"] for t in b["this_week"] if t["item"] == "high_test")


def test_high_test_only_counts_the_90_days_before_the_session():
    win = monday(5) + dt.timedelta(days=2)
    old = _pw(win - dt.timedelta(days=100), 330)                      # outside the 90 days
    prior = _pw(win - dt.timedelta(days=30), 290)
    b = board([act(monday(9), 1, 8, 200)], perf={"power": [old, prior, _pw(win, 295)]}, first_day=date(2025, 1, 1))
    assert cell(b, "high_test", 5)["state"] == "done"


def test_high_test_without_power_takes_the_20_minute_uphill_speed():
    win = monday(4) + dt.timedelta(days=1)
    vam = [{"date": win - dt.timedelta(days=d), "vam": 700, "vam20": 650} for d in (50, 70)]
    b = board([act(monday(9), 1, 8, 200)], perf={"vam": vam + [{"date": win, "vam": 690, "vam20": 650}]},
              first_day=date(2025, 1, 1))
    assert cell(b, "high_test", 4)["state"] == "done"
    b = board([act(monday(9), 1, 8, 200)], perf={"vam": vam + [{"date": win, "vam": 720, "vam20": 600}]},
              first_day=date(2025, 1, 1))
    assert item(b, "high_test")["met"] is False and "650 m/h" in cell(b, "high_test", 3)["detail"]


def test_high_test_still_counts_a_b_race_and_is_gray_under_90_days():
    b = board([act(monday(9), 1, 8, 200)], tests=[monday(5) + dt.timedelta(days=5)], first_day=date(2025, 1, 1))
    assert item(b, "high_test")["met"] is True
    b = board([act(monday(9), 1, 8, 200)], first_day=TODAY - dt.timedelta(days=60))
    assert cell(b, "high_test", 4)["state"] == "unk" and item(b, "high_test")["met"] is None
    b = board([act(monday(9), 1, 8, 200)], tests=[monday(5)], first_day=TODAY - dt.timedelta(days=60))
    assert item(b, "high_test")["met"] is True


def test_high_test_is_named_fitness_check_and_the_old_name_is_gone():
    b = board([act(monday(9), 1, 8, 200)], first_day=date(2025, 1, 1))
    it = item(b, "high_test")
    assert it["name"] == "實力測驗" and "追平近 90 天最佳" in it["crit"]
    assert item(b, "race_sim")["name"] == "賽事模擬"
    en = json.loads((ROOT / "i18n" / "locales" / "en.json").read_text("utf-8"))
    assert en["實力測驗"] == "Fitness check"
    import subprocess
    hits = subprocess.run(["git", "grep", "-n", "高區做一次驗證\\|One check at high intensity", "--", ":!docs",
                           ":!backend/tests"], cwd=ROOT.parent, capture_output=True, text=True).stdout
    assert hits == "", hits


def test_high_test_window_ends_with_week_3():
    late = monday(2) + dt.timedelta(days=1)                       # a taper-week best does not count
    prior = [_pw(late - dt.timedelta(days=d), 280) for d in (30, 60)]
    b = board([act(monday(9), 1, 8, 200)], today=monday(1) + dt.timedelta(days=2),
              perf={"power": prior + [_pw(late, 300)]}, first_day=date(2025, 1, 1))
    assert item(b, "high_test")["met"] is False


def test_fitness_check_counts_a_run_marked_a_race_by_hand(tmp_path, monkeypatch):
    # SP-674 (owner 2026-10-16): any run marked a race by hand counts, linked to an event or not;
    # a run that only has 賽 in its title does not, nor the A race
    from backend.engine import planning, race_activities as RA
    from backend.tests.test_activity_edit import _fit_ds, T0
    ds = _fit_ds(tmp_path)
    day = T0.date()
    plan = planning.Plan(events=[])

    def races(kind, prio=None):
        return lambda *a, **k: [{"date": day.isoformat(), "source": kind, "priority": prio}]

    for kind, prio, counts in (("manual", None, True), ("manual", "C", True), ("plan", "B", True),
                               ("title", None, False), ("plan", "A", False)):
        monkeypatch.setattr(RA, "race_activities", races(kind, prio))
        got = RD.inputs(ds, plan, day + dt.timedelta(days=1), day - dt.timedelta(days=7))["tests"]
        assert (day in got) is counts, (kind, prio, got)


# ---- R2 (Q2 = B): every cell is a bar of how much of its target was reached --------------------------

def test_every_judged_cell_carries_how_much_of_its_target_was_reached():
    rc = race()
    t = SP.FRAC[7]
    acts = [act(monday(7) + dt.timedelta(days=5), 6.0 * t * 0.5, 6.0 * t * 0.5 * 9, 50),     # half the long day
            act(monday(6) + dt.timedelta(days=5), 6.0 * SP.FRAC[6], 6.0 * SP.FRAC[6] * 9, 50)]
    b = board(acts, rc, first_day=date(2025, 1, 1))
    half = cell(b, "long", 7)
    assert half["state"] == "miss" and 0.4 < half["ratio"] < 0.6, half
    assert half["ratio"] == round(half["actual"] / half["target"], 3)
    assert cell(b, "long", 6)["state"] == "done" and cell(b, "long", 6)["ratio"] == 1.0
    assert cell(b, "terrain", 7)["ratio"] == 0.0                         # a count item: empty or full
    for c in _all_cells(b):
        if "ratio" in c and c["ratio"] is not None:
            assert 0.0 <= c["ratio"] <= 1.0, c
        if c["state"] == "done":
            assert c["ratio"] == 1.0, c


def test_the_card_draws_each_cell_as_a_bar_of_the_boards_ratio():
    js = (ROOT / "static" / "readiness.js").read_text("utf-8")
    assert "c.ratio" in js and "function bar(" in js and "shown(" not in js
    cal = js[js.index("function cal()"):js.index("function gr(")]
    assert "bar(c," in cal and "bar({ state: s, ratio: r })" in cal                 # cells and the legend
    assert "bar(c)" in js[js.index("function mob()"):js.index("function foot()")]   # the phone list too
