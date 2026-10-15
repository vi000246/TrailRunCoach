"""SP-219: 未來課表的預估 CTL／ATL／TSB and each day's planned TSS on the PMC charts
(engine/panels/plan_projection.py), the 圖表分析 range reaching into the future (api/wko5views.chart:
the data stops at today, a `plan_projection` chart draws on from the stored plan), and the
projection's planned TSS = the 課表's own estimate (api/plan_sessions.est_tss: a session with only
minutes counts too — plan_store.plan_summary used to count it as 0).

Synthetic data only: FakeDataset, an in-memory plan DB (test_plan_store.Env)."""
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

import pytest

from backend.api import plan_sessions as API
from backend.api import wko5views as WV
from backend.engine import plan_store as PS
from backend.engine.overview import project
from backend.engine.panels import plan_projection as PJ
from backend.engine.planning import Event, Phase, Plan
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.customviews import CustomViewError, REPO_VIEWS, load_custom_views, parse_view
from backend.engine.wko5expr.render_cache import RenderCache
from backend.tests.test_coros_workouts import run
from backend.tests.test_plan_store import API as PLAN_API, Env
from backend.tests.test_schedule_calendar import _extras
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)
T = TODAY.isoformat()
RATES = {"easy": 50.0, "long": 60.0, "quality": 70.0, "test": 75.0, "hike": 55.0, "strength": 30.0,
         "race": 50.0, "heat_passive": 0.0, "notice": 0.0}


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: TODAY)


def day(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def sess(d, kind="easy", minutes=60, tss=0.0, state="active", **kw):
    return {"uid": f"{kind}{d}{minutes}", "day": d, "kind": kind, "minutes": minutes, "tss": tss, "state": state,
            "origin": "custom", "edited": True, **kw}


# ---------------------------------------------------------------------------
# plan_summary: a session with only minutes counts with the 課表's estimate
# ---------------------------------------------------------------------------

def test_plan_summary_counts_a_minutes_only_session_with_the_estimate():
    ss = [sess(day(1), "easy", 60, 0.0), sess(day(2), "long", 120, 96.0),
          sess(day(3), "easy", 30, 0.0, state="deleted")]
    est = lambda s: API.est_tss(s, RATES)                    # noqa: E731
    s = PS.plan_summary(ss, "2026-09-28", T, 30.0, 32.0, 42.0, 7.0, "2026-10-04", est=est)
    p = {r["date"]: r["tss"] for r in s["projection"]}
    assert p[day(1)] == pytest.approx(50.0) and p[day(2)] == pytest.approx(96.0) and p[day(3)] == 0.0
    assert s["tss"] == pytest.approx(146.0)                   # the week's target counts it too
    # without `est`: the stored TSS, as before (callers that pass none are unchanged)
    old = PS.plan_summary(ss, "2026-09-28", T, 30.0, 32.0, 42.0, 7.0, "2026-10-04")
    assert {r["date"]: r["tss"] for r in old["projection"]}[day(1)] == 0.0


def test_planned_by_day_is_the_open_sessions_after_today():
    ss = [sess(T, "easy", 60), sess(day(1), "easy", 60), sess(day(1), "strength", 30, 10.0),
          sess(day(2), "long", 90, state="done"), sess(day(3), "notice", 0), sess(day(4), "race", 0),
          sess(day(5), "easy", 45, state="missed")]
    got = PS.planned_by_day(ss, T, lambda s: API.est_tss(s, RATES))
    assert got == {day(1): pytest.approx(60.0), day(4): 0.0}    # the race row (0 min) is still a planned day


def test_api_projection_equals_the_calendar_day_by_day(monkeypatch):
    """The overview's projection (GET /sessions summary) and the 課表 calendar's 預估 TSS agree on
    every later day, a session with only minutes (no TSS stored) included."""
    _extras(monkeypatch)
    with Env(monkeypatch) as e:
        e.c.get(f"{PLAN_API}/sessions")
        r = e.c.post(f"{PLAN_API}/sessions", json={"day": "2026-10-03", "kind": "easy", "minutes": 30})
        assert r.status_code == 200 and not r.json()["tss"]                 # minutes only
        per_day = _calendar_days(e, "2026-10-01", "2026-10-18")
        assert per_day["2026-10-03"] > 0
        proj = {x["date"]: x["tss"] for x in e.c.get(f"{PLAN_API}/sessions").json()["summary"]["projection"]}
        for d, v in per_day.items():
            assert proj[d] == pytest.approx(v, abs=0.1), d


def _calendar_days(e, start, end) -> dict:
    """{day: Σ 預估 TSS the 課表 calendar shows} of the open sessions after today."""
    cal = e.c.get(f"{PLAN_API}/calendar?start={start}&end={end}").json()
    out: dict = defaultdict(float)
    for s in cal["sessions"]:
        if s["state"] == "active" and s["day"] > cal["today"]:
            out[s["day"]] += s["tss_est"]
    return dict(out)


# ---------------------------------------------------------------------------
# plan_projection.inputs: the plan side
# ---------------------------------------------------------------------------

def test_inputs_use_the_estimate_and_stop_at_the_last_planned_day():
    rows = [sess(day(2), "easy", 60, 0.0), sess(day(2), "strength", 30, 12.0), sess(day(5), "long", 120, 0.0),
            sess(day(3), "easy", 60, 40.0, state="deleted"), sess(day(1), "easy", 60, 40.0, state="done"),
            sess(day(4), "notice", 0), sess(day(30), "long", 120, 90.0)]
    ev = [Event(id="a", name="山賽", date=day(5), priority="A"), Event(id="x", name="太遠", date=day(30))]
    p = PJ.inputs(TODAY, TODAY + dt.timedelta(days=20), rows=rows, rates=RATES, events=ev)
    assert p["today"] == T and p["until"] == day(5)               # the session on day 30 is after the range end
    assert [d for d, _ in p["days"]] == [day(i) for i in range(1, 6)]
    tss = dict(p["days"])
    assert tss[day(1)] == 0.0 and tss[day(2)] == pytest.approx(50.0 + 12.0) and tss[day(5)] == pytest.approx(120.0)
    assert [r["name"] for r in p["races"]] == ["山賽"] and p["races"][0]["grade"] == "A"


def test_inputs_none_without_a_planned_session_after_today():
    assert PJ.inputs(TODAY, TODAY + dt.timedelta(days=20), rows=[sess(T), sess(day(-3))], rates=RATES,
                     events=[]) is None


# ---------------------------------------------------------------------------
# plan_projection.apply: the series
# ---------------------------------------------------------------------------

def _line(name, expr, pts, color="#2a78d6"):
    return {"name": name, "type": "line", "y_axis": "TSSPERDAY", "color": color, "line_style": "solid",
            "line_width": "thick", "expression": expr, "unit": {"id": "TSSPERDAY", "kind": "number"},
            "x_unit": {"id": "NONE"}, "data": {"kind": "points", "x": "date", "points": pts}}


def _pmc_res():
    return {"begin": day(-1), "end": T, "empty": None, "series": [
        _line("CTL 體能", "ctl", [[day(-1), 40.0], [T, 41.0]]),
        _line("ATL 疲勞", "atl", [[day(-1), 50.0], [T, 48.0]], "#eb6834"),
        {**_line("TSB 有效訓練", "if(tsb / shift(ctl, 1) < -0.1, tsb)", [[day(-1), -9.0], [T, -10.0]], "#1baf7a"),
         "type": "bar"}]}


def _proj(days, races=()):
    return {"today": T, "until": days[-1][0], "days": [list(x) for x in days], "races": list(races)}


DAYS = [(day(1), 60.0), (day(2), 0.0), (day(3), 120.0)]


def test_pmc_lines_go_on_from_today_dashed_and_stop_with_the_plan():
    res = PJ.apply(_pmc_res(), _proj(DAYS), "pmc", 42.0, 7.0, day(20))
    assert res["end"] == day(20)                                      # the axis reaches the asked end
    pj = {s["name"]: s for s in res["series"] if s.get("role") == "projection"}
    assert set(pj) == {"CTL 體能・預估", "ATL 疲勞・預估", "負荷平衡（Load balance）・預估"}
    rows = project(41.0, 48.0, [x for _, x in DAYS], 42.0, 7.0)
    ctl = pj["CTL 體能・預估"]
    assert ctl["line_style"] == "dash" and ctl["color"] == "#2a78d6" and ctl["y_axis"] == "TSSPERDAY"
    assert ctl["data"]["points"][0] == [T, 41.0]                      # starts where the solid line ends
    assert [p[0] for p in ctl["data"]["points"]] == [T, day(1), day(2), day(3)]   # no line after the plan
    assert [p[1] for p in ctl["data"]["points"][1:]] == pytest.approx([r["ctl"] for r in rows])
    assert [p[1] for p in pj["ATL 疲勞・預估"]["data"]["points"][1:]] == pytest.approx([r["atl"] for r in rows])
    tsb = pj["負荷平衡（Load balance）・預估"]["data"]["points"]
    assert tsb[0] == [T, pytest.approx(40.0 - 50.0)]                 # today's TSB = yesterday's CTL − ATL
    assert [p[1] for p in tsb[1:]] == pytest.approx([r["tsb"] for r in rows])
    assert res["projection"]["today"] == T and res["projection"]["until"] == day(3)
    assert res["range_note"]
    # the chart's own series are untouched
    assert [s["name"] for s in res["series"][:3]] == ["CTL 體能", "ATL 疲勞", "TSB 有效訓練"]


def test_race_day_shows_projected_ctl_tsb_and_the_grade_range():
    races = [{"date": day(2), "name": "山賽", "grade": "A"}, {"date": day(3), "name": "路跑", "grade": "B"},
             {"date": day(1), "name": "練習賽", "grade": "C"}]
    res = PJ.apply(_pmc_res(), _proj(DAYS, races), "pmc", 42.0, 7.0, day(20))
    rows = dict(zip([d for d, _ in DAYS], project(41.0, 48.0, [x for _, x in DAYS], 42.0, 7.0)))
    got = {r["name"]: r for r in res["projection"]["races"]}
    assert got["山賽"]["band"] == [10.0, 20.0] and got["路跑"]["band"] == [-5.0, 5.0] and got["練習賽"]["band"] is None
    for r in got.values():
        assert r["ctl"] == pytest.approx(rows[r["date"]]["ctl"]) and r["tsb"] == pytest.approx(rows[r["date"]]["tsb"])
        assert r["text"] and r["color"]
    assert got["山賽"]["band_text"] and got["練習賽"]["band_text"] is None


def test_tss_mode_draws_outlined_bars_of_the_planned_tss():
    res = {"begin": day(-1), "end": T, "empty": None, "series": [
        {**_line("推進 CTL～+30", "if(…)", [[day(-1), 60.0], [T, None]], "#1baf7a"), "type": "stackedbar",
         "y_axis": "TSS", "unit": {"id": "TSS"}},
        _line("CTL 體能", "tl(tss, ctlconstant)", [[day(-1), 40.0], [T, 41.0]]),
        _line("ATL 疲勞", "tl(tss, atlconstant)", [[day(-1), 50.0], [T, 48.0]], "#eb6834")]}
    out = PJ.apply(res, _proj(DAYS), "tss", 42.0, 7.0, day(20))
    bars = next(s for s in out["series"] if s.get("role") == "projection" and s["type"] == "bar")
    assert bars["bar_style"] == "hollow" and bars["y_axis"] == "TSS" and bars["unit"] == {"id": "TSS"}
    assert bars["data"]["points"] == [[d, x] for d, x in DAYS]
    lines = [s for s in out["series"] if s.get("role") == "projection" and s["type"] == "line"]
    assert [s["line_style"] for s in lines] == ["dash", "dash"] and len(lines) == 2   # no TSB line on this chart
    assert out["projection"]["races"] == []


def test_a_week_bucket_chart_gets_no_projection():
    res = {**_pmc_res(), "x_period": "week", "buckets": [day(-6)]}
    assert PJ.apply(res, _proj(DAYS), "pmc", 42.0, 7.0, day(20)) == res


def test_without_ctl_lines_or_with_an_empty_chart_nothing_is_added():
    pct = {"begin": day(-1), "end": T, "empty": None, "series": [
        {**_line("低於 CTL", "if(…)", [[T, 0.5]]), "type": "stackedbar", "y_axis": "PERCENT"}]}
    assert PJ.apply(pct, _proj(DAYS), "tss", 42.0, 7.0, day(20)) == pct            # % CTL variant: no CTL to go on
    empty = {**_pmc_res(), "empty": "這段期間沒有資料。"}
    assert PJ.apply(empty, _proj(DAYS), "pmc", 42.0, 7.0, day(20)) == empty


# ---------------------------------------------------------------------------
# the chart endpoint: a range into the future
# ---------------------------------------------------------------------------

PMC = {"kind": "athlete", "id": "pmc", "title": "PMC", "plan_projection": "pmc", "series": [
    {"name": "CTL 體能", "type": "line", "y_axis": "TSSPERDAY", "color": "#2a78d6", "expression": "ctl"},
    {"name": "ATL 疲勞", "type": "line", "y_axis": "TSSPERDAY", "color": "#eb6834", "expression": "atl"},
    {"name": "TSB", "type": "bar", "y_axis": "TSSPERDAY", "color": "#1baf7a", "expression": "tsb"}]}
DAILY = {"kind": "athlete", "id": "daily", "title": "每日 TSS", "plan_projection": "tss", "series": [
    {"name": "TSS", "type": "stackedbar", "y_axis": "TSS", "color": "#1baf7a",
     "expression": "if(sum(tss, trunc(date)) > 0, sum(tss, trunc(date)))"},
    {"name": "CTL 體能", "type": "line", "y_axis": "TSS", "color": "#2a78d6", "expression": "tl(tss, ctlconstant)"},
    {"name": "ATL 疲勞", "type": "line", "y_axis": "TSS", "color": "#eb6834", "expression": "tl(tss, atlconstant)"}]}
PLAIN = {"kind": "athlete", "id": "plain", "title": "TSS 合計", "series": [
    {"name": "CTL", "type": "line", "y_axis": "TSSPERDAY", "expression": "ctl"}]}
VIEW = {"source": "custom", "name": "v", "dashboards": [{"title": "d", "charts": [PMC, DAILY, PLAIN]}]}


def _ds(today=TODAY):
    ws = [FakeWorkout(dt.datetime.combine(today - dt.timedelta(days=k), dt.time(7)), sport_type="Trail Running",
                      metrics={"tss": 40.0 + k % 5 * 10, "distance": 10.0, "duration": 3600.0}) for k in range(0, 120, 2)]
    d = FakeDataset(ws, today)
    d.config = EngineConfig()
    d.dir = None
    for w in d.workouts:
        w.entry.file = f"fake/{w.entry.start:%Y%m%d%H%M}.fit"
    return d


@pytest.fixture
def api(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    holder = {"ds": _ds(), "rows": [sess(day(1), "easy", 60, 0.0), sess(day(3), "long", 120, 90.0),
                                    sess(day(6), "race", 0)],
              "events": [Event(id="a", name="山賽", date=day(6), priority="A")]}
    monkeypatch.setattr(WV, "_dataset", lambda parity=None, source=None: holder["ds"])
    monkeypatch.setattr(WV, "_view", lambda name, parity=None: VIEW)
    monkeypatch.setattr(WV, "RENDER_CACHE", RenderCache(tmp_path / "render"))
    monkeypatch.setattr(PJ, "session_rows", lambda: holder["rows"])
    monkeypatch.setattr(PJ, "plan_rates", lambda rows: RATES)
    monkeypatch.setattr(PJ, "plan_events", lambda: holder["events"])
    app = FastAPI()
    app.include_router(WV.router)
    yield TestClient(app), holder


def _chart(client, c, begin=TODAY - dt.timedelta(days=41), end=TODAY + dt.timedelta(days=20), sports=None):
    q = f"begin={begin}&end={end}" + (f"&sports={sports}" if sports else "")
    r = client.get(f"/api/v1/app/views/v/dashboards/0/charts/{c}?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def _own(j, name):
    return next(s for s in j["series"] if s["name"] == name and s.get("role") != "projection")


def _pj(j, name):
    return next(s for s in j["series"] if s["name"] == name and s.get("role") == "projection")


def test_a_future_range_stops_the_data_at_today_and_draws_the_plan_on(api):
    client, holder = api
    j = _chart(client, 0)
    ctl = _own(j, "CTL 體能")["data"]["points"]
    assert ctl[-1][0] == T and all(p[0] <= T for p in ctl)            # no falling line after today
    assert j["end"] == day(20) and j["projection"]["today"] == T and j["projection"]["until"] == day(6)
    pctl = _pj(j, "CTL 體能・預估")
    assert pctl["data"]["points"][0] == ctl[-1] and pctl["line_style"] == "dash"
    assert pctl["data"]["points"][-1][0] == day(6)                    # stops at the last planned session
    rows = project(ctl[-1][1], _own(j, "ATL 疲勞")["data"]["points"][-1][1],
                   [50.0, 0.0, 90.0, 0.0, 0.0, 0.0], 42.0, 7.0)       # the easy run: 60 min × 50 TSS / h
    assert [p[1] for p in pctl["data"]["points"][1:]] == pytest.approx([r["ctl"] for r in rows])
    race = j["projection"]["races"][0]
    assert race["name"] == "山賽" and race["band"] == [10.0, 20.0]
    assert race["tsb"] == pytest.approx(rows[-1]["tsb"]) and race["ctl"] == pytest.approx(rows[-1]["ctl"])


def test_daily_tss_chart_gets_outlined_bars_equal_to_the_planned_tss(api):
    client, holder = api
    j = _chart(client, 1)
    bars = _pj(j, "預估訓練負荷（Training load）")
    assert bars["bar_style"] == "hollow"
    assert dict(bars["data"]["points"]) == {day(1): 50.0, day(2): 0.0, day(3): 90.0, day(4): 0.0, day(5): 0.0,
                                            day(6): 0.0}
    assert all(p[0] <= T for p in _own(j, "TSS")["data"]["points"])
    # a day-bucket chart (periods.py) is drawn on its buckets: they reach the end asked too
    assert j["x_period"] == "day" and j["buckets"][-1] == day(20) and day(1) in j["buckets"]


def test_a_range_to_today_is_drawn_as_before(api):
    client, holder = api
    j = _chart(client, 0, end=TODAY)
    assert "projection" not in j and not any(s.get("role") == "projection" for s in j["series"])
    assert j["end"] == T


def test_a_chart_without_projection_ends_today_on_a_future_range(api):
    client, holder = api
    j = _chart(client, 2)
    assert j["end"] == T and "projection" not in j
    assert j["series"][0]["data"]["points"][-1][0] == T
    assert j == _chart(client, 2, end=TODAY)                           # same drawing (and cache entry)


def test_an_activity_type_filter_draws_no_projection(api):
    client, holder = api
    j = _chart(client, 0, sports="trail")
    assert not any(s.get("role") == "projection" for s in j["series"]) and j["end"] == T
    assert j.get("range_note")                                         # says why


def test_editing_the_plan_changes_the_projection_on_the_next_load(api):
    client, holder = api
    before = dict(_pj(_chart(client, 1), "預估訓練負荷（Training load）")["data"]["points"])
    holder["rows"] = [sess(day(2), "easy", 60, 0.0), sess(day(3), "long", 120, 90.0),   # moved 1 → 2
                      sess(day(4), "easy", 30, 0.0), sess(day(6), "race", 0)]             # added one
    after = dict(_pj(_chart(client, 1), "預估訓練負荷（Training load）")["data"]["points"])
    assert before[day(1)] == 50.0 and after[day(1)] == 0.0 and after[day(2)] == 50.0 and after[day(4)] == 25.0
    holder["rows"] = [s for s in holder["rows"] if s["day"] != day(3)]                   # deleted
    assert dict(_pj(_chart(client, 1), "預估訓練負荷（Training load）")["data"]["points"])[day(3)] == 0.0


def test_the_charts_bars_equal_the_calendars_estimate_day_by_day(monkeypatch, tmp_path):
    """End to end: the stored plan of the 課表 (in-memory DB), its calendar's 預估 TSS per day, and the
    每日 TSS chart's outlined bars — with the real rate rule (plan_rates on the plan inputs)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    _extras(monkeypatch)
    with Env(monkeypatch) as e:
        e.c.get(f"{PLAN_API}/sessions")
        e.c.post(f"{PLAN_API}/sessions", json={"day": "2026-10-03", "kind": "easy", "minutes": 30})
        holder = {}

        def rows():
            return [s for s in run(PS.load(e.db)) if s["state"] not in ("deleted", "superseded")]
        monkeypatch.setattr(WV, "_dataset", lambda parity=None, source=None: _ds())
        monkeypatch.setattr(WV, "_view", lambda name, parity=None: VIEW)
        monkeypatch.setattr(WV, "RENDER_CACHE", RenderCache(tmp_path / "render"))
        monkeypatch.setattr(PJ, "session_rows", lambda: holder["rows"])
        monkeypatch.setattr(PJ, "plan_events", lambda: [])
        app = FastAPI()
        app.include_router(WV.router)
        c = TestClient(app)

        def check():
            holder["rows"] = rows()
            cal = _calendar_days(e, "2026-10-01", "2026-10-18")
            bars = dict(_pj(_chart(c, 1, end=dt.date(2026, 10, 18)), "預估訓練負荷（Training load）")["data"]["points"])
            assert cal and {d for d, v in bars.items() if v} == {d for d, v in cal.items() if v}
            for d, v in cal.items():
                assert bars[d] == pytest.approx(v, abs=0.1), d
            return bars
        b0 = check()
        assert b0["2026-10-03"] > 0                                    # the minutes-only session
        long = next(s for s in e.c.get(f"{PLAN_API}/sessions").json()["sessions"] if s["day"] == "2026-10-04")
        e.c.patch(f"{PLAN_API}/sessions/{long['uid']}", json={"day": "2026-10-03"})          # drag
        b1 = check()
        assert b1["2026-10-04"] == 0.0 and b1["2026-10-03"] > b0["2026-10-03"]
        e.c.delete(f"{PLAN_API}/sessions/{long['uid']}")                                      # delete
        b2 = check()
        assert b2["2026-10-03"] == pytest.approx(b0["2026-10-03"])


# ---------------------------------------------------------------------------
# the viewer's future presets: 到階段結束 / 到目標賽
# ---------------------------------------------------------------------------

def test_range_options_are_the_phase_end_and_the_next_target_race():
    plan = Plan(events=[Event(id="c", name="小賽", date=day(10), priority="C"),
                        Event(id="b", name="路跑", date=day(20), priority="B"),
                        Event(id="a", name="山賽", date=day(40), priority="A"),
                        Event(id="old", name="過去", date=day(-5), priority="A")],
                phases=[Phase(kind="base", start=day(-30), end=day(25), auto=False),
                        Phase(kind="taper", start=day(26), end=day(39), auto=False)])
    o = PJ.range_options(plan, TODAY)
    assert o["today"] == T and o["phase"]["end"] == day(25) and o["phase"]["label"]
    assert o["race"] == {"date": day(40), "name": "山賽", "grade": "A"}      # the next A race first
    no_a = PJ.range_options(Plan(events=[Event(id="b", name="路跑", date=day(20), priority="B")]), TODAY)
    assert no_a["race"]["name"] == "路跑"                                    # else the next B race
    none = PJ.range_options(Plan(phases=[Phase(kind="base", start=day(-30), end=T, auto=False)]), TODAY)
    assert none["phase"] is None and none["race"] is None                  # nothing after today


def test_plan_range_endpoint(api, monkeypatch):
    client, holder = api
    monkeypatch.setattr(PJ, "load_plan", lambda: Plan(events=holder["events"]))
    r = client.get("/api/v1/app/plan-range")
    assert r.status_code == 200 and r.json()["race"]["date"] == day(6) and r.json()["today"] == T


# ---------------------------------------------------------------------------
# views and the viewer
# ---------------------------------------------------------------------------

def _parse(c):
    return parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [c]}]})["dashboards"][0]["charts"][0]


def test_the_view_key_is_validated():
    assert _parse({**PMC})["plan_projection"] == "pmc"
    for bad in ({**PMC, "plan_projection": "yes"}, {**PMC, "kind": "workout"}):
        with pytest.raises(CustomViewError):
            _parse(bad)


def test_the_bundled_pmc_and_daily_tss_charts_turn_it_on():
    v = load_custom_views([REPO_VIEWS])["我的訓練"]
    charts = {c["id"]: c for d in v["dashboards"] for c in d["charts"]}
    assert charts["pmc-all"]["plan_projection"] == "pmc" and charts["daily-tss"]["plan_projection"] == "tss"
    for cid in ("pmc-all", "daily-tss"):
        assert "預估" in charts[cid]["description"]
    sc = json.loads((Path(REPO_VIEWS) / "i18n" / "en.json").read_text("utf-8"))
    for cid in ("pmc-all", "daily-tss"):
        assert "projected" in sc["training"]["charts"][cid]["description"].lower()


def test_the_viewer_offers_future_presets_and_draws_the_projection():
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "charts.html").read_text(encoding="utf-8")
    assert 'data-d="phase"' in html and 'data-d="race"' in html and "/plan-range" in html
    assert 'FUTURE_PRESETS = ["phase", "race"]' in html
    assert 'bar_style === "hollow"' in html and "res.projection" in html
    for loc in ("zh-TW", "en"):
        cat = json.loads((root / "i18n" / loc / "viewer.json").read_text(encoding="utf-8"))
        for k in ("pphase", "prace", "pphase_tip", "prace_tip", "proj.today", "proj.est"):
            assert cat.get(k), (loc, k)
