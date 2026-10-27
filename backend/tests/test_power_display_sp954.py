"""SP-954: pages show power by THAT day's power state (owner 2026-10-27, SP-950 問答 Q2-3;
docs/research/no-power-meter-audit.md 「拍板的規則」).

A single activity: a trusted Stryd power and the CP fresh on its day → full power; trusted power but the
CP stale that day → the power line / average / NP only (no % CP, no power zones), noted 「當時 CP 已 N 天
沒測」; no trusted power (none, watch) or no power meter now → heart rate / pace only. A charts range: the
power points only on the days the CP was fresh, the other stretches shaded 「CP 過期」/「沒有功率」; a
power-only chart only when the range has any valid power. Synthetic data only (wko5_fakes).
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from backend.engine import power_display as PWD
from backend.engine import power_source as PS
from backend.tests.test_activity_power_sp952 import THRESHOLD, _at, _DS, _plan, _run

FRESH = dt.date(2026, 9, 20)          # 19 days after the CP test of 2026-09-01
STALE = dt.date(2026, 10, 25)         # 54 days after: stale (> 42)


class _WatchDS(_DS):
    """Runs titled 「watch」 carry watch (wrist) power: never trusted."""

    def power_source(self, w):
        if self.channel(w.idx, "power") is not None and "watch" in (w.entry.title or ""):
            return PS.WATCH
        return super().power_source(w)


def _ds(*runs, source="stryd", cls=_DS):
    return cls(list(runs), _plan(source=source))


# ---- 1. one activity ---------------------------------------------------------------------------

def test_an_activity_shows_full_power_only_while_the_cp_was_fresh_that_day():
    ds = _ds(_run(_at(FRESH), THRESHOLD, seed=1), _run(_at(STALE), THRESHOLD, seed=1))
    wf, ws = ds.workouts
    assert PWD.activity_view(ds, wf) == {"mode": "full", "days": None}
    # stale that day: the power line / average / NP stay, and how long the CP had not been tested
    assert PWD.activity_view(ds, ws) == {"mode": "stale", "days": 54}


def test_no_trusted_power_or_no_power_meter_is_heart_rate_and_pace_only():
    nopw = _run(_at(FRESH), THRESHOLD, seed=2, power=False)
    watch = _run(_at(FRESH + dt.timedelta(days=1)), THRESHOLD, seed=3, title="watch run")
    ds = _ds(nopw, watch, cls=_WatchDS)
    assert [PWD.activity_view(ds, w)["mode"] for w in ds.workouts] == ["none", "none"]
    # 沒有功率計 now: even a Stryd run on a fresh day shows no power (③: no power anywhere)
    ds3 = _ds(_run(_at(FRESH), THRESHOLD, seed=1), source="none")
    assert PWD.activity_view(ds3, ds3.workouts[0])["mode"] == "none"
    # …and switching the equipment back to Stryd brings it back
    ds1 = _ds(_run(_at(FRESH), THRESHOLD, seed=1), source="stryd")
    assert PWD.activity_view(ds1, ds1.workouts[0])["mode"] == "full"


def test_a_dataset_that_cannot_tell_shows_everything():
    from backend.tests.wko5_fakes import FakeDataset
    ds = FakeDataset([_run(_at(STALE), THRESHOLD, seed=1)], STALE)
    assert PWD.activity_view(ds, ds.workouts[0]) == {"mode": "full", "days": None}


def test_which_single_activity_cards_need_full_power_that_day():
    import json as _j
    from backend.engine.wko5expr.customviews import REPO_VIEWS
    v = _j.loads((REPO_VIEWS / "workout.json").read_text("utf-8"))
    off = {c.get("section") or c.get("chart") for d in v["dashboards"] for c in d["charts"] if PWD.day_power_only(c)}
    # power zones, the CP test, W′ and the per-rep power cards; never the HR / pace ones, and the 間歇判讀
    # verdict stays (SP-952 makes it by heart rate / pace on a day without power)
    assert {"powerzones", "cp_test", "wprime_battery", "interval_reps", "interval_power", "intervals"} <= off
    assert not off & {"interval_verdict", "summary", "hrpower", "hrzones", "aerobic", "climbs", "threshold_trace"}


def test_the_views_list_marks_those_cards_for_the_viewer():
    from backend.api import wko5views as V
    views = V.list_views()
    single = next(v for v in views if any(c.get("kind") == "workout" for d in v["dashboards"] for c in d["charts"]))
    marked = {c["title"] for d in single["dashboards"] for c in d["charts"] if c.get("pwday")}
    assert "功率區間時間" in marked and "間歇判讀" not in marked


def test_a_stale_activity_card_drops_its_percent_cp_column():
    res = {"series": [{"name": "VAM", "data": {}}, {"name": "%CP", "data": {}}, {"name": "% CP 平均", "data": {}},
                      {"name": "心率", "data": {}}]}
    assert [s["name"] for s in PWD.strip_cp(res)["series"]] == ["VAM", "心率"]
    plain = {"series": [{"name": "心率"}]}
    assert PWD.strip_cp(plain) is plain
    # 本次重點's tiles: no training effect (power against the PD model) and no 「CP …」 under 平均功率 on a stale
    # day; no power tile at all without power
    cards = {"cards": [{"id": "hr", "sub": "AeT 150"}, {"id": "power", "sub": "CP 255", "power": True},
                       {"id": "tis", "power": True}]}
    assert PWD.strip_cp(cards, "stale")["cards"] == [{"id": "hr", "sub": "AeT 150"}, {"id": "power", "power": True}]
    assert PWD.strip_cp(cards, "none")["cards"] == [{"id": "hr", "sub": "AeT 150"}]


# ---- 2. a charts range ---------------------------------------------------------------------------

def _range_ds():
    days = [dt.date(2026, 9, 5), dt.date(2026, 9, 20),                    # fresh (CP test 09-01)
            dt.date(2026, 10, 20), dt.date(2026, 10, 22),                 # stale (> 42 days)
            dt.date(2026, 10, 24)]                                       # no power that day
    runs = [_run(_at(d), THRESHOLD, seed=i, power=(i < 4)) for i, d in enumerate(days)]
    return _DS(runs, _plan()), days


def test_a_range_has_one_state_per_run_day_and_shaded_stretches():
    from backend.engine.wko5expr.dataset import date_to_day
    ds, days = _range_ds()
    st = PWD.View(ds).day_states(date_to_day(days[0]), date_to_day(days[-1]))
    assert st == [("2026-09-05", "ok"), ("2026-09-20", "ok"), ("2026-10-20", "stale"), ("2026-10-22", "stale"),
                  ("2026-10-24", "nopower")]
    # consecutive days without valid power are one stretch (a run without power inside a stale-CP stretch doesn't
    # cut it in pieces): 「CP 過期」 when any of them had a Stryd power on a stale CP
    sp = PWD.spans(st)
    assert [(s["from"], s["to"], s["kind"], s["label"]) for s in sp] == [("2026-10-20", "2026-10-24", "stale", "CP 過期")]
    only = PWD.spans([("2026-09-05", "nopower"), ("2026-09-06", "nopower"), ("2026-09-20", "ok"),
                      ("2026-09-25", "stale")])
    assert [(s["from"], s["to"], s["kind"], s["label"]) for s in only] == [
        ("2026-09-05", "2026-09-19", "nopower", "沒有功率"), ("2026-09-25", "2026-09-25", "stale", "CP 過期")]
    assert PWD.range_valid(st) and not PWD.range_valid(st[2:])


def test_power_points_are_blank_on_the_days_without_valid_power():
    st = [("2026-09-05", "ok"), ("2026-10-20", "stale"), ("2026-10-24", "nopower")]
    pts = [["2026-09-05T07:00:00", 1.2], ["2026-10-20T07:00:00", 1.3], ["2026-10-24T07:00:00", 1.1]]
    res = {"series": [
        {"name": "Pw:HR", "expression": "pwhr", "data": {"kind": "points", "x": "datetime", "points": pts}},
        {"name": "Pa:HR", "expression": "pahr", "data": {"kind": "points", "x": "datetime", "points": pts}},
        {"name": "CP", "expression": "cp", "data": {"kind": "hline", "y": 250}}], "empty": None}
    out = PWD.mask_power(res, st)
    assert [p[1] for p in out["series"][0]["data"]["points"]] == [1.2, None, None]
    assert [p[1] for p in out["series"][1]["data"]["points"]] == [1.2, 1.3, 1.1]      # pace: untouched
    assert [(s["from"], s["kind"]) for s in out["power_spans"]] == [("2026-10-20", "stale")] and not out["empty"]
    # every power point blank: the chart says why instead of an empty plot
    only = {"series": [res["series"][0]], "empty": None}
    assert PWD.mask_power(only, st[1:])["empty"] == PWD.NO_VALID
    # a chart without a dated power series is unchanged
    assert PWD.mask_power({"series": [res["series"][1]]}, st) == {"series": [res["series"][1]]}


def test_the_targets_table_drops_watts_without_power_in_effect():
    T = {"cp": 250, "rows": [{"id": "threshold", "primary": "功率", "power": [237, 252], "power_pct": [0.95, 1.01]},
                             {"id": "easy", "primary": "心率", "power": [150, 200], "power_pct": [0.6, 0.8]}]}
    assert PWD.targets_by_state(T, {"state": "power", "power": True}) is T
    for st in ("stale", "none"):
        out = PWD.targets_by_state(T, {"state": st, "power": False})
        assert out["power_off"] and all(r["power"] == [None, None] for r in out["rows"])
        assert [r["primary"] for r in out["rows"]] == ["心率／配速", "心率"]


# ---- 3. the API ------------------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch, tmp_path):
    from backend.api import wko5views as V
    from backend.engine import activity_tags as AT
    monkeypatch.setattr(AT, "_default_db", lambda: tmp_path / "tags.db")
    holder = {}
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: holder["ds"])
    return V, holder


def test_the_kind_endpoint_and_the_activity_list_carry_each_activitys_power_view(api):
    V, holder = api
    ds = holder["ds"] = _ds(_run(_at(FRESH), THRESHOLD, seed=1), _run(_at(STALE), THRESHOLD, seed=1),
                            _run(_at(STALE + dt.timedelta(days=1)), THRESHOLD, seed=2, power=False))
    assert V.get_kind(0)["pw"] == {"mode": "full", "days": None}
    assert V.get_kind(1)["pw"] == {"mode": "stale", "days": 54}
    assert V.get_kind(2)["pw"]["mode"] == "none"
    rows = V.workouts(begin="2026-09-01", end="2026-10-27")
    assert {r["index"]: r["pw"]["mode"] for r in rows} == {0: "full", 1: "stale", 2: "none"}
    assert len(ds.workouts) == 3


def test_the_activity_list_hides_the_average_power_of_an_activity_without_trusted_power(api):
    import asyncio
    V, holder = api
    holder["ds"] = _ds(_run(_at(STALE), THRESHOLD, seed=1), _run(_at(STALE + dt.timedelta(days=1)), THRESHOLD,
                                                                  seed=3, title="watch"), cls=_WatchDS)
    out = asyncio.run(V.activities_stats())
    rows = sorted(out.values(), key=lambda r: r["pw"])
    assert [r["pw"] for r in rows] == ["none", "stale"]
    assert rows[0]["avg_power"] is None


def test_the_compare_page_has_no_power_line_for_an_activity_without_power(monkeypatch, tmp_path):
    from backend.api import activity_compare as AC
    from backend.engine import activity_tags as AT
    monkeypatch.setattr(AT, "_default_db", lambda: tmp_path / "tags.db")
    monkeypatch.setattr(AC, "same_course", lambda wa, wb: {})
    ds = _ds(_run(_at(STALE), THRESHOLD, seed=1), _run(_at(STALE + dt.timedelta(days=1)), THRESHOLD, seed=2,
                                                       power=False))
    monkeypatch.setattr(AC, "_ds", lambda: ds)
    out = AC.compare(0, 1)
    # a stale day keeps its power line and average (no % CP on this page); a run without power has none
    assert out["a"]["pw"]["mode"] == "stale" and out["summary"]["a"]["avg_power"] is not None
    assert out["b"]["pw"]["mode"] == "none" and out["summary"]["b"]["avg_power"] is None
    ds3 = _ds(_run(_at(STALE), THRESHOLD, seed=1), _run(_at(STALE), THRESHOLD, seed=2), source="none")
    monkeypatch.setattr(AC, "_ds", lambda: ds3)
    out = AC.compare(0, 1)
    assert out["summary"]["a"]["avg_power"] is None and not any(v is not None for v in out["overlay"]["a"]["power"])


def test_the_30_min_test_is_an_lthr_test_without_a_stryd():
    from backend.engine import zone_events as ZE
    assert ZE.test_label("tt30") == "30 分鐘獨跑測試（LTHR＋CP 檢查）"           # with a Stryd: unchanged
    assert ZE.test_label("tt30", meter=False) == "30 分鐘獨跑測試（LTHR）"
    assert "CP" not in ZE.test_condition("tt30", meter=False) and "CP" in ZE.test_condition("tt30")
    sg = [ZE._suggestion("cool_season", ["tt30", "aet"], "天氣轉涼了", "…", STALE),
          ZE._suggestion("break", ["cp", "tt30", "aet"], "停跑", "…", STALE)]
    ds3 = _ds(_run(_at(STALE), THRESHOLD, seed=1), source="none")
    out = ZE.without_cp_tests(sg, ds3, STALE)
    assert [s["tests"] for s in out] == [["tt30", "aet"], ["tt30", "aet"]]
    for s in out:
        assert s["labels"]["tt30"] == "30 分鐘獨跑測試（LTHR）"
        assert not any("CP" in c for c in s["conditions"]), s["conditions"]
    # a Stryd again: everything as it was
    ds1 = _ds(_run(_at(STALE), THRESHOLD, seed=1), source="stryd")
    assert ZE.without_cp_tests(sg, ds1, STALE) == sg


def test_the_suggestion_box_and_the_test_indicator_read_those_labels():
    from backend.engine import suggestions as SG
    sg = {"tests": ["tt30"], "labels": {"tt30": "30 分鐘獨跑測試（LTHR）"}, "conditions": [], "title": "t", "text": "x",
          "id": "cool_season", "detected": "2026-10-20", "earliest": None}
    rows = SG.zone_rows({"suggestions": [sg]}, set(), lambda k, s: False, lambda k, e: [])
    assert rows and "30 分鐘獨跑測試（LTHR）" in json.dumps(rows, ensure_ascii=False)
    assert "LTHR＋CP" not in json.dumps(rows, ensure_ascii=False)


def test_route_efforts_drop_power_of_activities_without_it(monkeypatch):
    from backend.api import routes as R
    ds = _ds(_run(_at(STALE), THRESHOLD, seed=1), _run(_at(STALE), THRESHOLD, seed=2, power=False))
    monkeypatch.setattr(R, "_ds", lambda: ds)
    assert R._power_off({0, 1, None}) == {1}
    e = {"id": "x", "avg_power": 230.0, "max_p30": 260.0, "power_vam": 300.0, "avg_hr": 150}
    assert R._no_power(e) == {"id": "x", "avg_power": None, "max_p30": None, "power_vam": None, "avg_hr": 150}
