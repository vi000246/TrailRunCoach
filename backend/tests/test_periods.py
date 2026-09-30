"""Period-total charts: quarter bucket, period rewrite, look-back floors, lock
rule, and the category-axis JSON from the chart endpoint."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
from pathlib import Path

from backend.api.wko5views import _apply_period
from backend.engine.wko5expr import periods as PD
from backend.engine.wko5expr.dataset import date_to_day, day_to_date
from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.render import result_to_json
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)
VIEWER = Path(__file__).resolve().parents[1] / "static" / "wko5_viewer.html"


def _chart(title, *exprs, **kw):
    return {"title": title, "kind": "athlete", "series": [{"name": f"s{i}", "expression": e}
                                                           for i, e in enumerate(exprs)], **kw}


def test_startofquarter_and_quarter_totals():
    ws = [FakeWorkout(dt.datetime(2026, m, d, 7), metrics={"tss": 10.0})
          for m, d in ((1, 5), (3, 31), (4, 1), (9, 30))]
    ds = FakeDataset(ws, TODAY)
    ev = Evaluator(ds, date_to_day(dt.date(2026, 1, 1)), ds.today)
    for d, q in ((dt.date(2026, 3, 31), dt.date(2026, 1, 1)), (dt.date(2026, 4, 1), dt.date(2026, 4, 1)),
                 (dt.date(2026, 9, 30), dt.date(2026, 7, 1)), (dt.date(2026, 12, 31), dt.date(2026, 10, 1))):
        assert day_to_date(ev.evaluate(f"startofquarter({date_to_day(d)})")) == q
    js = result_to_json(ev.evaluate("sum(tss, startofquarter(date))"), ds, ev.begin, ev.end)
    vals = {p[0]: p[1] for p in js["points"] if p[1] is not None}
    assert vals == {"2026-01-01": 20.0, "2026-04-01": 10.0, "2026-07-01": 10.0}
    js = result_to_json(ev.evaluate('sum(tss, "quarter")'), ds, ev.begin, ev.end)
    assert {p[0] for p in js["points"] if p[1] is not None} == {"2026-01-01", "2026-04-01", "2026-07-01"}


def test_period_detection_and_rewrite():
    ch = _chart("每週移動時間", "sum(movingduration, startofweek(date))", "(,0)", period="week")
    assert PD.chart_period(ch) == "week"
    m = PD.with_period(ch, "month")
    assert m["series"][0]["expression"] == "sum(movingduration, startofmonth(date))"
    assert m["title"] == "每月移動時間" and m["period"] == "month"
    assert PD.with_period(ch, "quarter")["title"] == "每季移動時間"
    ch3 = _chart("每週爬升", "sum(climbing, startofweek(date))", period="week")
    ch3["series"][0]["name"] = "週爬升"
    assert PD.with_period(ch3, "month")["series"][0]["name"] == "月爬升"
    assert PD.rename("每週", "week", "year") == "每年" and PD.rename("路跑", "week", "year") == "路跑"
    assert ch["series"][0]["expression"].endswith("startofweek(date))")          # original untouched
    # nested aggregate and count: only the group-by bucket moves; other trunc(date) stays
    e = "sum(sum(if(heartrate < aethr, deltatime)), startofweek(date))"
    assert PD.rewrite(e, "year") == "sum(sum(if(heartrate < aethr, deltatime)), startofyear(date))"
    assert PD.rewrite("sum(tss, trunc(date))", "month") == "sum(tss, startofmonth(date))"
    assert PD.rewrite("if(trunc(date) <= trunc(today), tss)", "month") == "if(trunc(date) <= trunc(today), tss)"
    # WKO5 charts carry no period key: detected from the expression
    assert PD.chart_period(_chart("Weekly TSS", "sum(tss, startofweek(date))")) == "week"
    assert PD.chart_period(_chart("PMC", "tl(tss, ctlconstant)")) is None
    assert PD.chart_period(_chart("Weekly Time & Distance", 'sum(if(sport="run", distance), "week")')) == "week"
    assert PD.chart_period(_chart("周跑量與月跑量", 'sum(duration,"week")', 'sum(duration,"month")')) is None


def test_lock_rule():
    assert PD.period_locked(_chart("每週時數變化", "sum(d, startofweek(date)) - shift(sum(d, startofweek(date)), 7)"))
    assert PD.period_locked(_chart("每週訓練時數", "sum(duration, startofweek(date))", "tl(duration, 28)*7"))
    assert not PD.period_locked(_chart("每週爬升", "sum(climbing, startofweek(date))", "(,2)"))


def test_look_back_floor_follows_the_chosen_period():
    ch = _chart("每週移動時間", "sum(movingduration, startofweek(date))", period="week")
    assert PD.min_days(ch, "week") == 0 and PD.min_days(ch, "day") == 0
    assert (PD.min_days(ch, "month"), PD.min_days(ch, "quarter"), PD.min_days(ch, "year")) == (365, 730, 1825)
    ch2 = _chart("每月 TSS", "sum(tss, startofmonth(date))", period="month", min_days=500)
    assert PD.min_days(ch2, "month") == 500                                       # own floor: default period only
    assert PD.min_days(ch2, "quarter") == 730


def test_apply_period_endpoint_logic():
    e = date_to_day(TODAY)
    b = e - 89                                                                   # 90 days selected
    ch = _chart("每週移動時間", "sum(movingduration, startofweek(date))", period="week")
    c2, b2, info = _apply_period(ch, b, e, "month", custom=True)
    assert c2["title"] == "每月移動時間" and info["x_period"] == "month" and info["period_toggle"]
    assert e - b2 + 1 >= 365 and day_to_date(b2).day == 1 and info["range_note"] == "顯示近 12 個月"
    assert info["buckets"][0] == day_to_date(b2).isoformat() and info["buckets"][-1] == "2026-09-01"
    assert len(info["buckets"]) == 12                                            # 2025-10 … 2026-09
    # week: no floor, begin moved back to a Monday
    c3, b3, info3 = _apply_period(ch, b, e, None, custom=True)
    assert info3["x_period"] == "week" and day_to_date(b3).weekday() == 0 and info3["range_note"] is None
    # locked: the toggle is hidden and a requested period is ignored
    lk = _chart("每週時數變化", "sum(d, startofweek(date)) - shift(sum(d, startofweek(date)), 7)", period="week")
    c4, _, info4 = _apply_period(lk, b, e, "month", custom=True)
    assert info4["x_period"] == "week" and not info4["period_toggle"] and c4 is lk
    # imported WKO5 chart: category axis only, no toggle, range untouched
    wk = _chart("Weekly TSS", "sum(tss, startofweek(date))")
    c5, b5, info5 = _apply_period(wk, b, e, "month", custom=False)
    assert info5["x_period"] == "week" and not info5["period_toggle"] and b5 == b and c5 is wk
    assert _apply_period(_chart("PMC", "tl(tss, 42)"), b, e, "month", custom=True) == (
        _chart("PMC", "tl(tss, 42)"), b, None)


def test_buckets():
    b = date_to_day(dt.date(2026, 2, 15))
    e = date_to_day(dt.date(2026, 9, 30))
    assert PD.buckets(b, e, "quarter") == ["2026-01-01", "2026-04-01", "2026-07-01"]
    assert PD.buckets(b, e, "year") == ["2026-01-01"]
    assert PD.buckets(date_to_day(dt.date(2026, 9, 23)), e, "week") == ["2026-09-21", "2026-09-28"]


def test_viewer_draws_categories_and_toggle():
    html = VIEWER.read_text(encoding="utf-8")
    assert 'type: "category", data: cats.map((b) => bucketLabel(b, cat))' in html
    assert "function bucketLabel(" in html and "Q${" in html
    assert "res.period_toggle" in html and "&period=${period}" in html and "wko5viewer.period" in html
