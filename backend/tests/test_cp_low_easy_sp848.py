"""SP-848: easy runs persistently above the easy zone by power → the suggestion box says the CP may be set low.

The easy flat road runs of the last 6 weeks picked by HR (avg HR ≤ AeT + 3) with a power IF (NP ÷ that day's
CP): ≥ 5 of them, median IF > 0.85 (推估) and no CP test dated in those 6 weeks → a zone_test row
`zone:cp_low_easy` suggesting a CP test; 稍後提醒 / 不要 as every zone row (SP-507). Synthetic only."""
import datetime as dt
from types import SimpleNamespace

from backend.engine import intensity_calib as IC
from backend.engine import suggestions as SG
from backend.engine import zone_events as ZE
from backend.engine.planning import Plan, Threshold

TODAY = dt.date(2026, 10, 20)
DS = SimpleNamespace(workouts=[])


def _ifs(vals):
    return [((TODAY - dt.timedelta(days=3 * k + 1)).isoformat(), v) for k, v in enumerate(vals)]


HIGH = _ifs([0.86, 0.87, 0.88, 0.89, 0.90, 0.87])                     # median 0.875


def _sug(plan=None, ifs=HIGH):
    out = ZE.suggestions(DS, plan or Plan(), TODAY, acts=[], brk={}, points=[], easy_ifs=ifs)
    return next((s for s in out["suggestions"] if s["id"] == "cp_low_easy"), None), out["checks"]["cp_low_easy"]


def test_easy_runs_above_the_easy_zone_suggest_a_cp_test():
    s, chk = _sug()
    assert s is not None and s["tests"] == ["cp"] and chk["fired"] and chk["n"] == 6
    assert "CP 可能偏低" in s["title"] and "0.88" in s["text"] and "0.85" in s["text"] and "推估" in s["text"]
    assert "推估" in s["source"] and s.get("priority") != "low"


def test_not_fired_below_the_line_with_too_few_runs_or_after_a_cp_test():
    assert _sug(ifs=_ifs([0.80, 0.82, 0.84, 0.83, 0.86, 0.85]))[0] is None          # median 0.835
    assert _sug(ifs=_ifs([0.90, 0.92, 0.91, 0.95]))[0] is None                      # 4 < 5 runs
    assert _sug(ifs=[])[0] is None
    s, chk = _sug(plan=Plan(thresholds=[Threshold("2026-10-01", cp=240)]))          # a CP test 19 days ago
    assert s is None and chk["tested"]
    assert _sug(plan=Plan(thresholds=[Threshold("2026-08-01", cp=240)]))[0] is not None   # before the 6 weeks


def test_the_box_row_and_its_snooze_and_no():
    s, _chk = _sug()
    rows = SG.zone_rows({"suggestions": [s], "events": []}, set(), lambda k, since: False,
                        lambda k, earliest: [{"day": "2026-10-22"}])
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "zone:cp_low_easy" and r["type"] == "zone_test" and [t["key"] for t in r["tests"]] == ["cp"]
    now = dt.datetime(2026, 10, 20, 9)
    dis = SG.record({}, r["id"], SG.SNOOZED, now, tests=SG.test_kinds(r))
    assert dis[r["id"]]["tests"] == ["cp"] and dis[r["id"]]["until"] == "2026-10-27"
    assert SG.visible(rows, dis, now) == []                                          # 稍後提醒: away for 7 days
    assert SG.visible(rows, dis, now + dt.timedelta(days=7)) == rows                 # then back while still computed
    dis = SG.record({}, r["id"], "declined", now)
    assert SG.visible(rows, dis, now + dt.timedelta(days=30)) == []                  # 不要
    # the week's CP test already offered: not twice
    assert SG.zone_rows({"suggestions": [s], "events": []}, {"cp"}, lambda k, since: False, lambda k, e: []) == []


def test_easy_power_ifs_takes_power_ifs_of_easy_runs_only(monkeypatch):
    base = {"km": 8.0, "gain": 40.0, "minutes": 50.0, "hr": 140.0, "aet": 145.0, "date": "2026-10-10"}
    rows = [{**base, "if": 0.88, "src": "power"},
            {**base, "if": 0.90, "src": "hrtss"},                                    # an HR IF says nothing of the CP
            {**base, "if": 0.89, "src": "power", "hr": 155.0},                       # not easy by HR
            {**base, "if": 0.87, "src": "power", "gain": 400.0}]                     # hilly
    seen = {}
    monkeypatch.setattr(IC, "easy_rows", lambda ds, today, weeks=IC.WINDOW_WEEKS: seen.setdefault("w", weeks) and rows)
    assert ZE.easy_power_ifs(SimpleNamespace(memo={}), TODAY) == [("2026-10-10", 0.88)]
    assert seen["w"] == 6
