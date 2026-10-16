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
from backend.tests.test_race_readiness_sp650 import TODAY, act, board, cell, ctl_series, item, monday, race

ROOT = Path(__file__).resolve().parents[1]
COUNT_ITEMS = {"weekly_long", "terrain", "b2b", "race_sim", "high_test"}
LINKED = {"long", "ctl_top", "uphill", "taper_volume", "climb_early", "ctl_down_days", "race_tsb"}
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
    b = board([act(monday(9), 1.0, 8, 200)], first_day=date(2025, 1, 1))
    assert cell(b, "taper_volume", 2)["state"] == "tap"
    assert cell(b, "taper_volume", 2)["show"] == "40–59%"
    assert cell(b, "climb_early", 2)["show"] == "≤59%"
    assert cell(b, "ctl_down_days", 1)["show"] == "≤7"
    assert cell(b, "race_tsb", 1)["show"] == "+10–+35%"
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
