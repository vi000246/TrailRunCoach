"""Time in zone over a period (panels/period_zones.py): per-day thresholds,
moving time only, sport filter, power policy, periods, phase targets and the
weekly groups. Synthetic data only (no WKO5 folder, no real DB)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine.panels import activity_charts as A
from backend.engine.panels import period_zones as PZ
from backend.engine.wko5expr.dataset import date_to_day, day_to_date
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)          # a Wednesday


def _act(day, hr, p=None, v=None, sport="run", tags=("running",), sport_type="running"):
    hr = np.asarray(hr, dtype=float)
    t = np.arange(len(hr), dtype=float)
    v = np.full(len(t), 10.0) if v is None else np.asarray(v, dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": list(v),
          "elapseddistance": list(np.cumsum(v) / 3600.0)}
    if p is not None:
        ch["power"] = list(np.asarray(p, dtype=float))
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport=sport, tags=list(tags),
                       sport_type=sport_type, channels=ch, metrics={"duration": float(t[-1])})


class DatedDS(FakeDataset):
    """LTHR / CP that change on a date; optional plan phases; watch power for some files."""

    def __init__(self, acts, lthr=((dt.date(2000, 1, 1), 160.0),), cp=((dt.date(2000, 1, 1), 250.0),),
                 phase=None, watch=()):
        super().__init__(acts, TODAY, settings={})
        self._lthr, self._cp, self._watch = lthr, cp, set(watch)
        if phase:
            from backend.engine.planning import Phase, Plan
            self.plan = Plan(phases=[Phase(kind=k, start=s, end=e, auto=False) for k, s, e in phase])

    @staticmethod
    def _on(rows, day):
        v = None
        for d, x in rows:
            if d <= day_to_date(day):
                v = x
        return v

    def sport_setting(self, kind, w):
        if w.sport != "run":
            return None                      # like the real Dataset: run thresholds only
        return self._on(self._lthr, w.day) if kind == "thr" else None

    def cp(self, w):
        return self._on(self._cp, w.day) if w.sport == "run" else None

    def power_ok(self, w):
        return w.entry.file not in self._watch


def _rows(res):
    return {r["id"]: r["seconds"] for r in res["rows"]}


def _q(**k):
    return {"zperiod": "range", **k}


B, E = date_to_day(dt.date(2026, 9, 1)), date_to_day(TODAY)


def test_each_activity_uses_the_thresholds_of_its_own_day():
    # 600 s at 145 bpm on two days; LTHR 150 before 9-15, 170 from 9-15
    ds = DatedDS([_act(dt.date(2026, 9, 10), [145] * 600), _act(dt.date(2026, 9, 20), [145] * 600)],
                 lthr=((dt.date(2000, 1, 1), 150.0), (dt.date(2026, 9, 15), 170.0)))
    res = PZ.compute(ds, B, E, _q(zkind="hr", zmodel="frielhr"))
    # 145 / 150 = 0.967 -> Friel 4; 145 / 170 = 0.853 -> Friel 2
    s = _rows(res)
    assert s["4"] == 600 and s["2"] == 600 and res["total_s"] == 1200
    used = [t["text"] for t in res["thresholds"]]
    assert len(used) == 2 and "150" in used[0] and "170" in used[1]
    assert all(r["range"] is None for r in res["rows"])         # two threshold sets: no single bpm range
    assert res["rows"][1]["pct"] == "85–90% LTHR"


def test_matches_the_single_activity_card_and_counts_moving_time_only():
    hr = np.repeat([130.0, 140.0, 150.0, 158.0, 163.0], 600)
    ds = DatedDS([_act(dt.date(2026, 9, 10), hr)])
    card = next(m for m in A.zone_times(ds, ds.workouts[0], "hr")["models"] if m["id"] == "frielhr")
    res = PZ.compute(ds, B, E, _q(zkind="hr"))
    assert [r["seconds"] for r in res["rows"]] == [r["seconds"] for r in card["rows"]]
    assert res["rows"][0]["range"] == "< 136 bpm"                  # one threshold set: absolute ranges
    # a 300-s stop (0.5 km/h) is not moving: left out
    v = np.full(len(hr), 10.0)
    v[:300] = 0.5
    ds2 = DatedDS([_act(dt.date(2026, 9, 10), hr, v=v)])
    assert PZ.compute(ds2, B, E, _q(zkind="hr"))["total_s"] == len(hr) - 300


def test_sport_filter_road_and_trail_by_default_hike_on_request():
    d = dt.date(2026, 9, 10)
    ds = DatedDS([_act(d, [130] * 100),
                  _act(d, [130] * 200, tags=("runningtrail",), sport_type="trail running"),
                  _act(d, [130] * 400, sport="walk", tags=("hiking",), sport_type="hiking"),
                  _act(d, [130] * 800, sport="walk", tags=(), sport_type="walking")])
    res = PZ.compute(ds, B, E, _q())
    assert res["total_s"] == 300 and res["sports"] == ["road", "trail"]   # 路跑 + 越野跑 by default, no hike
    assert PZ.compute(ds, B, E, _q(zsports="road"))["total_s"] == 100
    assert PZ.compute(ds, B, E, _q(zsports="trail"))["total_s"] == 200
    res = PZ.compute(ds, B, E, _q(zsports="road,trail,hike"))
    assert res["total_s"] == 700 and res["n_used"] == 3            # the hike uses the run LTHR; walking never counts
    assert res["sports"] == ["road", "trail", "hike"]
    assert PZ.compute(ds, B, E, _q(zsports="bogus"))["sports"] == ["road", "trail"]
    assert PZ.parse_sports(None) == PZ.DEFAULT_SPORTS == ("road", "trail")
    assert "hike" not in PZ.DEFAULT_SPORTS


def test_power_models_skip_watch_power_and_default_to_palladino():
    d = dt.date(2026, 9, 10)
    p = np.repeat([150.0, 210.0], 300)                             # CP 250: 60 % and 84 %
    ds = DatedDS([_act(d, [140] * 600, p=p), _act(d, [140] * 600, p=p)], watch={"fake/1.wko4"})
    res = PZ.compute(ds, B, E, _q(zkind="power"))
    assert res["model"]["id"] == "palladino"
    s = _rows(res)
    assert s["1A"] == 300 and s["2"] == 300 and res["total_s"] == 600
    assert res["skipped"] == [{"reason": "手錶推估功率不採用", "n": 1}]
    assert [m["id"] for m in res["models"]["power"]] == ["palladino", "ilevels", "stryd", "palladino3", "coggan"]
    assert [m["id"] for m in res["models"]["hr"]] == ["frielhr", "classichr", "seiler3"]   # no %HRmax


def test_quick_pick_periods():
    ds = DatedDS([])
    r = lambda k, **x: PZ.resolve_period(ds, B, E, k, **x)
    assert (r("week")["begin"], r("week")["end"]) == ("2026-09-28", "2026-09-30")
    assert (r("lastweek")["begin"], r("lastweek")["end"]) == ("2026-09-21", "2026-09-27")
    assert (r("4w")["begin"], r("4w")["end"]) == ("2026-09-07", "2026-09-30")
    assert r("range")["begin"] == "2026-09-01"
    assert r("custom", zbegin="2026-09-05", zend="2026-09-12")["days"] == 8
    assert r("custom")["key"] == "range"
    assert r("phase")["key"] == "range" and r("phase")["note"]     # no plan
    ds2 = DatedDS([], phase=[("base", "2026-08-01", "2026-10-31")])
    p = PZ.resolve_period(ds2, B, E, "phase")
    assert (p["begin"], p["end"], p["label"]) == ("2026-08-01", "2026-09-30", "本期（基礎期）")


@pytest.mark.parametrize("low_s, want", [(3420, "good"), (3000, "watch"), (2500, "warning"), (2000, "serious")])
def test_base_phase_verdict_on_the_three_zone_summary(low_s, want):
    # 3600 s: low (< AeT = 0.89 × 160 = 142.4) vs moderate (150 bpm)
    hr = [130.0] * low_s + [150.0] * (3600 - low_s)
    ds = DatedDS([_act(dt.date(2026, 9, 10), hr)], phase=[("base", "2026-08-01", "2026-10-31")])
    res = PZ.compute(ds, B, E, _q(zkind="hr", zmodel="classichr"))
    assert res["summary"]["rows"][0]["seconds"] == low_s
    assert res["target"]["low_target"] == 0.90 and res["target"]["low_floor"] == 0.75
    v = res["verdict"]
    assert v["level"] == want
    if want in ("warning", "serious"):
        assert "中強度" in v["text"] and v["icon"] == "⚠"
    if want == "good":
        assert v["icon"] == "✓" and "分配得當" in v["text"]


def test_taper_without_high_intensity_and_too_little_data():
    ds = DatedDS([_act(dt.date(2026, 9, 10), [130.0] * 3600)], phase=[("taper", "2026-09-01", "2026-10-10")])
    assert "減量期" in PZ.compute(ds, B, E, _q())["verdict"]["text"]
    short = DatedDS([_act(dt.date(2026, 9, 10), [130.0] * 600)])
    assert PZ.compute(short, B, E, _q())["verdict"]["level"] == "none"


def test_weekly_groups_add_up_and_carry_their_week_phase():
    acts = [_act(dt.date(2026, 9, 2), [130] * 3600), _act(dt.date(2026, 9, 9), [150] * 3600),
            _act(dt.date(2026, 9, 10), [130] * 1800)]
    ds = DatedDS(acts, phase=[("base", "2026-08-01", "2026-09-06"), ("specific", "2026-09-07", "2026-10-31")])
    res = PZ.compute(ds, B, E, {"zkind": "hr"}, view="weekly")
    assert res["group_by"] == "week"
    g = res["groups"]
    assert [x["start"] for x in g] == ["2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    assert sum(sum(x["seconds"]) for x in g) == res["total_s"] == 9000
    assert g[0]["phase"]["kind"] == "base" and g[0]["low_target"] == 0.90 and g[0]["verdict"]["level"] == "good"
    assert g[1]["phase"]["kind"] == "specific" and g[1]["low_share"] == pytest.approx(1 / 3)
    assert g[1]["verdict"]["level"] == "serious"
    assert g[2]["verdict"]["level"] == "none" and g[2]["n"] == 0
    assert [z["id"] for z in res["zones"]] == ["1", "2", "3", "4", "5a", "5b", "5c"]
    # a long range is grouped by month; zgroup forces it
    long = PZ.compute(ds, date_to_day(dt.date(2025, 9, 1)), E, {}, view="weekly")
    assert long["group_by"] == "month" and long["groups"][0]["start"] == "2025-09-01"
    assert PZ.compute(ds, B, E, {"zgroup": "month"}, view="weekly")["group_by"] == "month"


def test_histogram_is_memoised_per_file():
    ds = DatedDS([_act(dt.date(2026, 9, 10), [140] * 100)])
    calls = []
    real = A.grid
    try:
        A.grid = lambda d, w: calls.append(1) or real(d, w)
        PZ.compute(ds, B, E, _q())
        PZ.compute(ds, B, E, _q(zmodel="classichr"))
    finally:
        A.grid = real
    assert len(calls) == 1


def test_view_wiring_and_api_render():
    from backend.api.wko5views import _render
    from backend.engine.wko5expr.customviews import CustomViewError, REPO_VIEWS, load_custom_views, parse_view
    v = load_custom_views([REPO_VIEWS])["我的訓練"]
    dash = next(d for d in v["dashboards"] if d["title"] == "強度")
    first = dash["charts"][:2]
    assert [c["kind"] for c in first] == ["periodzones", "periodzones"]
    assert [c["view"] for c in first] == ["total", "weekly"]
    with pytest.raises(CustomViewError):
        parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [
            {"title": "a", "kind": "periodzones", "view": "nope"}]}]})
    ds = DatedDS([_act(dt.date(2026, 9, 29), [140] * 600)])
    res = _render(first[0], ds, B, E, None, None, params={"zperiod": "week"})
    assert res["kind"] == "periodzones" and res["view"] == "total" and res["total_s"] == 600
    assert res["period"]["label"] == "本週" and res["title"] == first[0]["title"]
    wk = _render(first[1], ds, B, E, None, None, params={})
    assert wk["view"] == "weekly" and wk["total_s"] == 600
