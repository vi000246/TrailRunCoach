"""
Integration of SP-252 (積雪 × on the climbing legs), SP-254 (night slowdown on the dark part) and
SP-260 (百岳 「部分」 適應 = the midpoint curve in capacity.A) on one 百岳 plan: each factor is
applied once, in its own place — acclimatisation in the capacity speed, snow on the climbing
legs' time, the night slowdown on the dark share of each segment after both — and the totals
stay the sum of the segments. Expected values are recomputed by hand, not read back.
"""
from __future__ import annotations

import copy

import pytest
from pytest import approx

from backend.engine.racepower import course as CO
from backend.engine.racepower import env as ENV
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import night as NI
from backend.engine.racepower import planner as PL
from backend.tests.test_baiyue_capacity import _synthetic_cap, _v1
from backend.tests.test_race_night import SUN, _plan as _api_plan
from backend.tests.test_racepower_v2 import client  # noqa: F401  (fixture)
from backend.tests.test_racepower_v2 import synthetic_track

DATE, START, PCT = "2099-01-10", "15:00", 10.0       # sunset 17:30: the climb ends in the dark


def _course():
    # 8 km up 800 m (2600 → 3400 m), then 8 km down: one climbing and one descending segment
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    return CO.build_course(tr)


def _hike(c, cap, **opts):
    return PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                        opts={"mode": "auto", "trip_kind": "solo", "start_time": START, **opts}, validated={},
                        capacity=cap)


def _by_hand(c, cap, accl, k_snow):
    """plan_hike's segment loop: capacity speed at the acclimatisation, × heat, the climbing
    legs' time × k_snow, the hours into the day including the snow time."""
    ts, h = [], 0.0
    for sg in c["segments"]:
        t_c = 10.0 - 0.0065 * (sg["z_mean"] - 3400)
        H = 1 - ENV.heat_penalty_pct(t_c, 70) / 100
        t = sg["dist_m"] / (cap.v(sg["grade"], 9.0, 1.0, sg["z_mean"], h, 1, accl) * H)
        if sg["cls"] in ("up", "steep_up"):
            t *= k_snow
        ts.append(t)
        h += t / 3600
    return ts


def test_snow_night_and_partial_acclimatisation_compose_once():
    c, cap = _course(), _synthetic_cap()
    assert [s["cls"] for s in c["segments"]] == ["up", "down"]

    # 部分適應: capacity only — between 未適應 and 已適應, and the hand loop reproduces it
    un, part, acc = (_hike(c, cap, acclimatisation=a) for a in ("unacclimatised", "partial", "acclimatised"))
    assert un["summary"]["time_s"] > part["summary"]["time_s"] > acc["summary"]["time_s"]
    assert [s["t"] for s in part["segments"]] == approx(_by_hand(c, cap, "partial", 1.0), rel=1e-9)

    # + 積雪 有踏跡: × 1.5 on the climb only (it starts at hour 0, so exactly × 1.5), on top of 部分
    snow = _hike(c, cap, acclimatisation="partial", snow="trodden")
    assert [s["t"] for s in snow["segments"]] == approx(_by_hand(c, cap, "partial", 1.5), rel=1e-9)
    assert snow["segments"][0]["t"] == approx(part["segments"][0]["t"] * 1.5, rel=1e-9)
    assert snow["summary"]["acclimatisation"] == "partial"
    assert snow["warnings"].count(PL.PARTIAL_NOTE) == 1
    sn0 = dict(snow["summary"]["snow"])
    assert sn0["base_s"] == approx(part["summary"]["time_s"], rel=1e-9)     # snow-free = the 部分 plan
    assert sn0["time_s"] == approx(snow["summary"]["time_s"], rel=1e-9)

    # + night slowdown 10 %: only the dark share of each segment, after snow and 部分 (not again)
    base = copy.deepcopy(snow)
    night = NI.apply(copy.deepcopy(snow), date=DATE, start_time=START, sun=SUN, slow_pct=PCT)
    segs = night["segments"]
    assert 0 < segs[0]["dark_share"] < 1 and segs[1]["dark_share"] == 1.0
    for a, b in zip(base["segments"], segs):
        assert b["t"] == approx(a["t"] * (1 + PCT / 100 * b["dark_share"]), abs=NI.TOL_S)
    # the climb: 部分 × snow × night, each once
    up_part = part["segments"][0]["t"]
    assert segs[0]["t"] == approx(up_part * 1.5 * (1 + PCT / 100 * segs[0]["dark_share"]), abs=NI.TOL_S)
    # the descent: no snow; its night factor is the whole 10 %
    assert segs[1]["t"] == approx(base["segments"][1]["t"] * 1.1, abs=NI.TOL_S)

    # totals = the sum of the segments, everywhere
    sm, ratio = night["summary"], night["summary"]["moving_ratio"]
    tot = sum(s["t"] for s in segs)
    added = tot - sum(s["t"] for s in base["segments"])
    assert added > 0 and night["night"]["added_s"] == approx(added)
    assert sm["time_s"] == approx(tot) and sm["capacity_time_s"] == approx(tot)
    assert segs[-1]["cum_s"] == approx(tot)
    assert sum(d["moving_h"] for d in night["days"]) * 3600 == approx(tot)
    assert sm["clock_s"] == approx(base["summary"]["clock_s"] + added / ratio)

    # the 積雪 line follows the night: its numbers match the plan, the snow difference is unchanged
    sn = sm["snow"]
    assert sn["time_s"] == approx(sm["time_s"]) and sn["clock_s"] == approx(sm["clock_s"])
    assert sn["time_s"] - sn["base_s"] == approx(sn0["time_s"] - sn0["base_s"])
    assert sn["time_hi_s"] == approx(sn0["time_hi_s"] + added)
    # 部分 not re-applied by the night pass
    assert night["summary"]["acclimatisation"] == "partial" and night["warnings"].count(PL.PARTIAL_NOTE) == 1


@pytest.mark.parametrize("snow", ["trodden", "untrodden"])
def test_api_snow_line_matches_the_plan_with_night_slowdown(client, snow):   # noqa: F811
    """/plan: 百岳 with 積雪 + night slowdown + 部分 — the 積雪 numbers are the plan's numbers, the
    night hint is in the attention line, segments add up."""
    p = _api_plan(client, "baiyue", sun=SUN, night_slow_pct=PCT, snow=snow, acclimatisation="partial")
    q = _api_plan(client, "baiyue", sun=SUN, snow=snow, acclimatisation="partial")
    sm, sn = p["summary"], p["summary"]["snow"]
    assert p["night"]["applied"] is True and p["night"]["added_s"] > 0
    assert sm["time_s"] == approx(sum(s["t"] for s in p["segments"]))
    assert sn["time_s"] == approx(sm["time_s"])
    assert sn["time_s"] - sn["base_s"] == approx(q["summary"]["snow"]["time_s"] - q["summary"]["snow"]["base_s"])
    assert sm["time_s"] == approx(q["summary"]["time_s"] + p["night"]["added_s"])
    assert p["attention"] and "night" in p["attention"]["kinds"]
