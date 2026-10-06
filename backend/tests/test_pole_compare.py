"""有杖 vs 沒杖 comparison (SP-243): activity_tags.pole_counts, panels/pole_compare.py, the
chart list's `needs`, the viewer / activity editor wiring. Synthetic data only — never the
WKO5 folder or the real DB."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from backend.engine import activity_tags as AT
from backend.engine.panels import pole_compare as PC
from backend.engine.wko5expr.dataset import date_to_day
from backend.tests.test_climb_vam import _act
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 9, 30)


def _row(day: dt.date, poles=None, hour=7, file=None, tags=()):
    t = list(tags) + ([AT.POLES[poles]] if poles else [])
    return {"start_local": f"{day.isoformat()}T{hour:02d}:00", "file": file, "tags": t}


# ---- eligibility -----------------------------------------------------------------

def test_pole_counts_needs_five_of_each_in_the_last_365_days():
    rows = [_row(TODAY - dt.timedelta(days=k), "with") for k in range(4)] \
        + [_row(TODAY - dt.timedelta(days=10 + k), "without") for k in range(6)] \
        + [_row(TODAY - dt.timedelta(days=365), "with"),        # one day too old
           _row(TODAY + dt.timedelta(days=1), "with"),          # tomorrow: not counted
           _row(TODAY - dt.timedelta(days=20), None, tags=["雨天"])]
    c = AT.pole_counts(rows, TODAY)
    assert (c["with"], c["without"], c["need"], c["days"]) == (4, 6, 5, 365)
    assert (c["more_with"], c["more_without"], c["eligible"]) == (1, 0, False)
    assert c["since"] == (TODAY - dt.timedelta(days=364)).isoformat()
    rows.append(_row(TODAY - dt.timedelta(days=364), "with"))   # the first day of the window counts
    c = AT.pole_counts(rows, TODAY)
    assert c["with"] == 5 and c["eligible"] is True
    assert AT.pole_counts([], TODAY)["more_with"] == 5
    # rows straight from the DB (tags_json, no parsed list) count too
    raw = [{"start_local": f"{TODAY.isoformat()}T07:00", "tags_json": json.dumps(["有杖"])}]
    assert AT.pole_counts(raw, TODAY)["with"] == 1
    assert AT.pole_marks_stamp(raw) == [[f"{TODAY.isoformat()}T07:00", "", "with"]]


# ---- the panel ---------------------------------------------------------------------

def _hike(day: dt.date, down_kmh=4.0, up_kmh=3.0, up_hr=150, kind="hike", down_s=600, up_s=600):
    """5 min flat, a −20 % descent (vertical speed = km/h × 1000 × 0.2), a +20 % climb, 5 min flat."""
    a = _act([(300, 0.0, 5.0, 120, 55), (down_s, -0.20, down_kmh, 120, 50), (up_s, 0.20, up_kmh, up_hr, 50),
              (300, 0.0, 5.0, 120, 55)])
    ch = {"elapsedtime": a["t"], "elapseddistance": [x / 1000 for x in a["dist_m"]], "_elevation": a["elev"],
          "heartrate": a["hr"], "cadence": a["cadence"], "speed": a["speed_kmh"]}
    sport, tags, st = {"hike": ("walk", ["hiking"], "hiking"), "trail": ("run", ["runningtrail"], "trail running"),
                       "road": ("run", ["running"], "running")}[kind]
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport=sport, tags=tags, sport_type=st, channels=ch)


def _eligible_rows():
    """5 + 5 marks on far-away days (no activity there): the athlete qualifies."""
    return [_row(TODAY - dt.timedelta(days=200 + k), "with", hour=5) for k in range(5)] \
        + [_row(TODAY - dt.timedelta(days=250 + k), "without", hour=5) for k in range(5)]


def test_panel_medians_per_grade_bin_with_n_and_no_point_under_three():
    days = [TODAY - dt.timedelta(days=3 * k + 1) for k in range(8)]
    acts, rows = [], _eligible_rows()
    for k, d in enumerate(days[:4]):                 # 有杖: slower down (3 km/h → 600 m/h), VAM 450 / 150 bpm
        acts.append(_hike(d, down_kmh=3.0, up_kmh=2.25))
        rows.append(_row(d, "with"))
    for k, d in enumerate(days[4:6]):                # 沒杖: only 2 activities
        acts.append(_hike(d, down_kmh=5.0))
        rows.append(_row(d, "without"))
    acts.append(_hike(days[6]))                      # 未標: not in it
    acts.append(_hike(days[7], kind="road"))         # a road run marked 沒杖: not a trail run / hike
    rows.append(_row(days[7], "without"))
    ds = FakeDataset(acts, TODAY)
    res = PC.compute(ds, date_to_day(TODAY - dt.timedelta(days=60)), date_to_day(TODAY), {}, rows=rows, today=TODAY)
    assert res["kind"] == "polecompare" and res["counts"]["eligible"] and not res.get("empty")
    assert res["activities"] == {"with": 4, "without": 2}
    assert "背包較重" in res["caveat"]
    m = {x["id"]: x for x in res["metrics"]}
    assert set(m) == {"vspeed", "cadence", "vam_hr"}           # no impact data on these
    vs = {r["bin"]: r for r in m["vspeed"]["rows"]}
    assert [r["bin"] for r in m["vspeed"]["rows"]] == ["d15", "d8", "d3"]
    assert vs["d15"]["with"]["n"] == 4 and vs["d15"]["with"]["value"] == pytest.approx(600, rel=0.02)
    assert vs["d15"]["without"]["n"] == 2 and vs["d15"]["without"]["value"] is None     # n < 3: not drawn
    assert vs["d8"]["with"]["n"] == 0                       # a few transition seconds aren't an activity's bin
    up = m["vam_hr"]["rows"]
    assert [r["bin"] for r in up] == ["u15"]
    assert up[0]["with"]["value"] == pytest.approx(450 / 150, rel=0.02)
    assert m["cadence"]["rows"][0]["with"]["value"] == pytest.approx(100, abs=0.5)
    assert "note" not in res


def test_panel_not_eligible_says_how_many_marks_and_draws_nothing():
    rows = [_row(TODAY - dt.timedelta(days=k + 1), "with") for k in range(2)]
    ds = FakeDataset([_hike(TODAY - dt.timedelta(days=1))], TODAY)
    res = PC.compute(ds, 0, date_to_day(TODAY), {}, rows=rows, today=TODAY)
    assert res["metrics"] == [] and res["counts"]["eligible"] is False
    assert "「有杖」2 次" in res["empty"] and "「沒杖」0 次" in res["empty"] and "5 次" in res["empty"]


def test_panel_eligible_but_nothing_in_range_or_every_bin_too_small():
    ds = FakeDataset([_hike(TODAY - dt.timedelta(days=1))], TODAY)
    res = PC.compute(ds, 0, date_to_day(TODAY), {}, rows=_eligible_rows(), today=TODAY)
    assert res["list"] == [] and "沒有標了" in res["empty"]
    rows = _eligible_rows() + [_row(TODAY - dt.timedelta(days=1), "with")]
    res = PC.compute(ds, 0, date_to_day(TODAY), {}, rows=rows, today=TODAY)
    assert not res.get("empty") and "不到 3 次" in res["note"]


def test_short_bins_and_altitude_glitches_are_left_out():
    short = PC.values({"d15": {"time_s": 100.0, "dz_m": -20.0, "dist_m": 100.0}})
    assert short == {}
    glitch = PC.values({"d15": {"time_s": 600.0, "dz_m": -300.0, "dist_m": 100.0}})
    assert glitch == {}
    no_hr = PC.values({"u15": {"time_s": 600.0, "dz_m": 100.0, "dist_m": 500.0, "hr": None, "hr_s": 0.0,
                               "hr_dz_m": 0.0}})
    assert no_hr == {"u15": {}}


# ---- the chart list / API ------------------------------------------------------------

def test_chart_sits_after_downhill_rate_on_trail_and_needs_poles():
    from backend.engine.wko5expr.customviews import REPO_VIEWS, load_custom_views
    v = load_custom_views([REPO_VIEWS])["我的訓練"]
    dash = next(d for d in v["dashboards"] if d["title"] == "能力")
    ids = [c["id"] for c in dash["charts"]]
    assert ids.index("pole-compare") == ids.index("downhill-rate") + 1
    ch = dash["charts"][ids.index("pole-compare")]
    assert ch["kind"] == "polecompare" and ch["sports"] == ["trail"] and ch["needs"] == "poles"
    assert "推估" in ch["description"] and "不一定是杖造成的" in ch["description"]
    sc = json.loads((ROOT.parent / "views" / "i18n" / "en.json").read_text(encoding="utf-8"))
    assert sc["training"]["charts"]["pole-compare"]["title"]


def test_needs_must_be_known():
    from backend.engine.wko5expr.customviews import CustomViewError, parse_view
    raw = {"name": "x", "dashboards": [{"title": "d", "charts": [{"title": "c", "kind": "polecompare",
                                                                   "needs": "rain"}]}]}
    with pytest.raises(CustomViewError):
        parse_view(raw, Path("x.json"))


@pytest.mark.parametrize("eligible", [False, True])
def test_list_views_reports_needs_met(monkeypatch, eligible):
    from backend.api import wko5views as WV
    rows = _eligible_rows() if eligible else []
    monkeypatch.setattr(AT, "load", lambda *a, **k: rows)
    monkeypatch.setattr(WV, "today_local", lambda *a, **k: TODAY)
    views = {v["name"]: v for v in WV.list_views()}
    dash = next(d for d in views["我的訓練"]["dashboards"] if d["title"] == "能力")
    c = next(c for c in dash["charts"] if c["id"] == "pole-compare")
    assert c["needs"] == "poles" and c["needs_met"] is eligible
    others = [c for c in dash["charts"] if c["id"] != "pole-compare"]
    assert all("needs" not in c for c in others)


def test_api_render_goes_to_the_panel(monkeypatch):
    from backend.api.wko5views import _render
    from backend.engine.wko5expr.customviews import REPO_VIEWS, load_custom_views
    v = load_custom_views([REPO_VIEWS])["我的訓練"]
    ch = next(c for d in v["dashboards"] for c in d["charts"] if c.get("kind") == "polecompare")
    monkeypatch.setattr(PC, "SOURCES_OVERRIDE", [])
    res = _render(ch, FakeDataset([], TODAY), 0, date_to_day(TODAY), None, None, params={})
    assert res["kind"] == "polecompare" and res["title"] == ch["title"] and res["empty"]


# ---- no model reads the mark ----------------------------------------------------------

def test_only_the_comparison_panel_imports_itself():
    import re
    imp = re.compile(r"import\s+pole_compare|pole_compare\s+import|panels\.pole_compare")
    hits = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "engine").rglob("*.py"))
            if p.name != "pole_compare.py" and imp.search(p.read_text(encoding="utf-8"))]
    assert not hits, hits


# ---- the pages -----------------------------------------------------------------------

def test_viewer_and_editor_wiring_and_i18n():
    viewer = (ROOT / "static" / "wko5_viewer.html").read_text(encoding="utf-8")
    assert "pole_compare.js" in viewer and '"polecompare"].includes(c.kind)' in viewer
    assert "(!c.needs || c.needs_met)" in viewer and "drawPoleCompare(box, res)" in viewer
    js = (ROOT / "static" / "pole_compare.js").read_text(encoding="utf-8")
    keys = {k for k in __import__("re").findall(r't\("viewer\.(pole\.[a-z_]+)"', js)}
    keys |= {"pole.with", "pole.without"}               # built as `viewer.pole.${g}`
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "viewer.json").read_text(encoding="utf-8"))
        assert all(cat.get(k) for k in keys), (loc, [k for k in keys if not cat.get(k)])
        act = json.loads((ROOT / "static" / "i18n" / loc / "activity.json").read_text(encoding="utf-8"))
        for k in ("poles.more", "poles.more_with", "poles.more_without", "poles.more_sep", "poles.ready"):
            assert act.get(k), (loc, k)
    page = (ROOT / "static" / "activity.html").read_text(encoding="utf-8")
    assert "data-pole-hint" in page and "S.poleCompare = r.pole_compare" in page
