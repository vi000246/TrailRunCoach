"""
SP-260: the 百岳 calculator's 海拔適應 — the personal capacity model takes 「部分」 as the midpoint
of 已適應 and 未適應 (capacity.A, as env._alt_factor), the default follows the nights slept above
2,750 m in the 14 days before the trip (≥ 2 → 部分, 推估; activities + the 課表 calendar's records),
已適應 only when picked, no 部分 on a route under 3,000 m (the page). Synthetic only.
"""
import datetime as dt
import json
from pathlib import Path

import pytest
from pytest import approx

from backend.engine import altitude as AL
from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.tests.test_baiyue_capacity import _synthetic_cap, _v1
from backend.tests.test_racepower_v2 import synthetic_track

TODAY = dt.date(2026, 10, 5)
STATIC = Path(__file__).resolve().parents[1] / "static"


def test_capacity_partial_is_between_acclimatised_and_unacclimatised():
    cap = _synthetic_cap()
    for z in (2800.0, 3300.0, 3950.0):
        un, acc, part = cap.A(z, "unacclimatised"), cap.A(z, "acclimatised"), cap.A(z, "partial")
        assert un < part < acc and part == approx((un + acc) / 2)
    assert cap.A(300, "partial") == 1.0 and cap.A(None, "partial") == 1.0


def _plan(accl):
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    inp = {"hiking": {"days": [{"ep_per_h": x, "days": 1, "solo": False} for x in (2.4, 2.8, 3.0, 3.2, 3.5, 3.9)]}}
    opts = {"mode": "auto", "trip_kind": "solo"} | ({"acclimatisation": accl} if accl else {})
    return PL.plan_hike(v1=_v1(), course=CO.build_course(tr), hike_speed=GM.fit_hike_speed([]), inp=inp,
                        opts=opts, validated={}, capacity=_synthetic_cap())


def test_the_capacity_plan_uses_partial_and_says_what_it_is():
    un, part, acc = _plan("unacclimatised"), _plan("partial"), _plan("acclimatised")
    assert un["summary"]["time_s"] > part["summary"]["time_s"] > acc["summary"]["time_s"]
    assert part["summary"]["acclimatisation"] == "partial"
    assert PL.PARTIAL_NOTE in part["warnings"] and "中點" in PL.PARTIAL_NOTE and "推估" in PL.PARTIAL_NOTE
    assert not any("已改用未適應" in w for w in part["warnings"])
    assert _plan(None)["summary"]["time_s"] == un["summary"]["time_s"]          # nothing sent: 未適應, as before


def test_the_default_follows_the_nights():
    start = TODAY + dt.timedelta(days=6)
    D = lambda n: TODAY - dt.timedelta(days=n)                                  # noqa: E731
    two = {D(4): 3100.0, D(3): 3000.0, D(2): 2900.0}                            # 2 nights from activities
    assert AL.acclimatisation_default(two, TODAY, start)["default"] == "partial"
    one = {D(4): 3100.0, D(3): 3000.0}
    r = AL.acclimatisation_default(one, TODAY, start)
    assert r["default"] == "unacclimatised" and r["nights"] == 1
    # + a recorded night (SP-259): 2 → 部分
    r = AL.acclimatisation_default(one, TODAY, start, {D(2): 3150.0})
    assert r["default"] == "partial" and r["nights"] == 2 and r["manual"] == 1
    # never 已適應 by default, however many nights
    many = {D(n): 3300.0 for n in range(1, 13)}
    assert AL.acclimatisation_default(many, TODAY, start)["default"] == "partial"
    # no date / a past date: the 14 days up to today; a trip 30 days out: nothing counted yet
    assert AL.acclimatisation_default(two, TODAY, None)["start"] == TODAY.isoformat()
    assert AL.acclimatisation_default(two, TODAY, D(10))["default"] == "partial"
    assert AL.acclimatisation_default(two, TODAY, TODAY + dt.timedelta(days=30))["default"] == "unacclimatised"


def test_api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import racepower as RP
    D = lambda n: TODAY - dt.timedelta(days=n)                                  # noqa: E731
    monkeypatch.setattr(RP, "today_local", lambda *a, **k: TODAY)
    monkeypatch.setattr(RP, "_dataset", lambda: object())
    monkeypatch.setattr(AL, "day_altitudes", lambda ds, today, days=AL.HISTORY_DAYS: {D(4): 3100.0, D(3): 3000.0})
    monkeypatch.setattr(AL, "load_nights", lambda user_id=1: {D(2): 3150.0})
    app = FastAPI()
    app.include_router(RP.router)
    c = TestClient(app)
    r = c.get("/api/v1/racepower/altitude-acclimatisation", params={"date": "2026-10-11"}).json()
    assert r == {"default": "partial", "nights": 2, "manual": 1, "since": "2026-09-27", "start": "2026-10-11"}
    monkeypatch.setattr(AL, "load_nights", lambda user_id=1: {})
    assert c.get("/api/v1/racepower/altitude-acclimatisation").json()["default"] == "unacclimatised"


def test_the_page_defaults_hides_partial_under_3000_and_explains_it():
    s = (STATIC / "racepower.html").read_text("utf-8")
    for el in ("/altitude-acclimatisation?", "function acclDefault()", "function lowRoute()", 'id="accl-line"',
               '#accl [data-a="partial"]', "z < 3000", "z_max: c.totals.z_max", "S.accl !== acclDefault()"):
        assert el in s, el
    # the user's choice wins: the default only when nothing is picked
    assert "function accl() { const a = S.accl || acclDefault();" in s
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert all(cat.get(k) for k in ("accl.tip", "accl.default_partial", "accl.default_none")), loc
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    assert "部分適應＝已適應與未適應的中點，是推估" in zh["accl.tip"] and "3,000 m" in zh["accl.tip"]


@pytest.mark.parametrize("nights, want", [(0, "unacclimatised"), (1, "unacclimatised"), (2, "partial"), (5, "partial")])
def test_accl_default_rule(nights, want):
    assert AL.accl_default(nights) == want
