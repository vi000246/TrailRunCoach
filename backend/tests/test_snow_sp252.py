"""
SP-252: 百岳 積雪 — × 1.5 (有踏跡) / × 2–3 (無踏跡) on the climbing legs only (owner 2026-10-06,
docs/research/cold-environment.md §2.4); the 玉山 雪季措施 line only when snow is picked.
Expected values are recomputed independently (segment loops by hand), not read back.
"""
from __future__ import annotations

from pytest import approx

from backend.engine.racepower import calc as C
from backend.engine.racepower import course as CO
from backend.engine.racepower import env as ENV
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import hike as HK
from backend.engine.racepower import planner as PL
from backend.tests.test_baiyue_capacity import _synthetic_cap, _v1
from backend.tests.test_racepower_v2 import synthetic_track


def _course():
    # 8 km up 800 m, then 8 km down: one climbing and one descending segment
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    return CO.build_course(tr)


def _cap_plan(c, cap, **opts):
    return PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                        opts={"mode": "auto", "trip_kind": "solo", **opts}, validated={}, capacity=cap)


def _expected(c, cap, k):
    """Independent segment loop: plan_hike's v with the climbing legs' time × k (the hours into the
    day include the snow time)."""
    tot, h = 0.0, 0.0
    for sg in c["segments"]:
        t_c = 10.0 - 0.0065 * (sg["z_mean"] - 3400)
        H = 1 - ENV.heat_penalty_pct(t_c, 70) / 100
        t = sg["dist_m"] / (cap.v(sg["grade"], 9.0, 1.0, sg["z_mean"], h, 1) * H)
        if sg["cls"] in ("up", "steep_up"):
            t *= k
        tot += t
        h += t / 3600
    return tot


def test_snow_choices_and_climb_detection():
    assert HK.snow_range(None) is None and HK.snow_range("none") is None
    assert HK.snow_range("trodden") == (1.5, 1.5) and HK.snow_range("untrodden") == (2.0, 3.0)
    assert HK.is_climb({"cls": "up"}) and HK.is_climb({"cls": "steep_up"})
    assert not HK.is_climb({"cls": "flat", "grade": 0.01}) and not HK.is_climb({"cls": "down"})
    assert HK.is_climb({"grade": 0.05}) and not HK.is_climb({"grade": 0.01})


def test_climb_time_share_is_tobler_two_segments():
    up_h = 8 / GM.tobler_kmh(0.1)
    down_h = 8 / GM.tobler_kmh(-0.1)
    assert HK.climb_time_share(16, 800, 800) == approx(up_h / (up_h + down_h))
    assert HK.climb_time_share(10, 0, 0) == 0.0


def test_none_is_exactly_the_snow_free_plan():
    c, cap = _course(), _synthetic_cap()
    base = _cap_plan(c, cap)
    none = _cap_plan(c, cap, snow="none")
    assert none["summary"]["time_s"] == base["summary"]["time_s"]
    assert [s["t"] for s in none["segments"]] == [s["t"] for s in base["segments"]]
    assert none["summary"]["snow"] is None and base["summary"]["snow"] is None
    assert none["days"] == base["days"] and none["warnings"] == base["warnings"]


def test_trodden_is_x1_5_on_the_climb_only_and_the_eta_follows():
    c, cap = _course(), _synthetic_cap()
    assert [s["cls"] for s in c["segments"]] == ["up", "down"]
    base = _cap_plan(c, cap, start_time="05:00")
    snow = _cap_plan(c, cap, snow="trodden", start_time="05:00")
    assert snow["summary"]["time_s"] == approx(_expected(c, cap, 1.5), rel=1e-9)
    assert base["summary"]["time_s"] == approx(_expected(c, cap, 1.0), rel=1e-9)
    up_b, up_s = base["segments"][0], snow["segments"][0]
    assert up_s["t"] == approx(up_b["t"] * 1.5, rel=1e-9)
    # the descent: same speed model, only its hours-into-the-day start is later (f_time)
    dn_b, dn_s = base["segments"][1], snow["segments"][1]
    assert dn_s["t"] == approx(dn_b["t"] * cap.f_time(up_b["t"] / 3600) / cap.f_time(up_s["t"] / 3600), rel=1e-9)
    assert up_s["eta"] != up_b["eta"]                       # the summit ETA moves
    sn = snow["summary"]["snow"]
    assert sn["key"] == "trodden" and sn["time_s"] == snow["summary"]["time_s"] == sn["time_hi_s"]
    assert sn["base_s"] == approx(base["summary"]["time_s"], rel=1e-12)     # the snow-free plan's own time
    assert snow["summary"]["clock_s"] > base["summary"]["clock_s"]


def test_untrodden_shows_x2_and_x3():
    c, cap = _course(), _synthetic_cap()
    snow = _cap_plan(c, cap, snow="untrodden")
    sn = snow["summary"]["snow"]
    assert sn["lo"] == 2.0 and sn["hi"] == 3.0
    assert snow["summary"]["time_s"] == approx(_expected(c, cap, 2.0), rel=1e-9)
    up2 = snow["segments"][0]["t"]
    assert sn["time_hi_s"] == approx(snow["summary"]["time_s"] + up2 / 2.0, rel=1e-9)   # the climb at × 3
    assert sn["clock_hi_s"] > sn["clock_s"]


def test_group_time_and_target_mode_include_the_snow():
    c, cap = _course(), _synthetic_cap()
    inp = {"hiking": {"days": [{"ep_per_h": x, "days": 1, "solo": False} for x in (2.4, 2.8, 3.0, 3.2, 3.5, 3.9)]}}

    def run(**o):
        return PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp=inp,
                            opts={"mode": "auto", "trip_kind": "group", **o}, validated={}, capacity=cap)
    b, s = run(), run(snow="trodden")
    f = s["summary"]["capacity_time_s"] / b["summary"]["capacity_time_s"]
    assert f > 1.0
    assert s["summary"]["group_time_s"]["p50"] == approx(b["summary"]["group_time_s"]["p50"] * f, rel=1e-9)
    # a target time: the same goal needs a higher speed multiple on snow
    tb, ts = run(mode="time", target_time_s=20000), run(mode="time", target_time_s=20000, snow="trodden")
    assert ts["summary"]["speed_factor"] == approx(tb["summary"]["speed_factor"] * f, rel=1e-9)


def test_v1_path_and_manual_course():
    c = _course()
    hs = GM.fit_hike_speed([])
    run = lambda **o: PL.plan_hike(v1=_v1(), course=c, hike_speed=hs, inp={"hiking": {"days": []}},  # noqa: E731
                                   opts={"mode": "auto", **o}, validated={"hike": True})
    b, s = run(), run(snow="trodden")
    assert b["summary"]["total_method"] == "v2"
    assert s["segments"][0]["t"] == approx(b["segments"][0]["t"] * 1.5, rel=1e-9)
    assert s["segments"][1]["t"] == approx(b["segments"][1]["t"], rel=1e-9)
    assert run(snow="none")["summary"]["time_s"] == b["summary"]["time_s"]
    # a manual course: the climbing share of the time from Tobler's two-segment split (推估)
    man = {"source": "manual", "totals": {"km": 16.0, "gain_m": 800.0, "loss_m": 800.0}, "segments": []}
    cap = _synthetic_cap()
    mb = PL.plan_hike(v1=_v1(), course=man, hike_speed=hs, inp={"hiking": {"days": []}},
                      opts={"mode": "auto"}, validated={}, capacity=cap)
    ms = PL.plan_hike(v1=_v1(), course=man, hike_speed=hs, inp={"hiking": {"days": []}},
                      opts={"mode": "auto", "snow": "untrodden"}, validated={}, capacity=cap)
    sh = HK.climb_time_share(16, 800, 800)
    assert ms["summary"]["time_s"] == approx(mb["summary"]["time_s"] * (1 + sh), rel=1e-9)
    assert ms["summary"]["snow"]["time_hi_s"] == approx(mb["summary"]["time_s"] * (1 + 2 * sh), rel=1e-9)
    assert ms["days"][0]["moving_h"] == approx(mb["days"][0]["moving_h"] * (1 + sh), rel=1e-9)


def test_yushan_season_line_only_with_snow_on_a_yushan_route():
    assert HK.is_yushan("玉山") and HK.is_yushan("玉山主峰單攻") and HK.is_yushan("玉山北峰")
    assert not HK.is_yushan("南玉山") and not HK.is_yushan("雪山") and not HK.is_yushan(None)

    def out(snow):
        return {"summary": {"snow": None if snow is None else {"key": snow, "season": None}}, "days": [{"day": 1}]}
    o = out("trodden")
    C.snow_season(C.PlanIn(type="baiyue", snow="trodden", peak="玉山"), {"name": None}, o)
    assert o["summary"]["snow"]["season"] == {"url": HK.YUSHAN_SNOW_URL, "single_day": True}
    o = out("trodden")                       # the GPX's name when the 地點 is empty
    C.snow_season(C.PlanIn(type="baiyue", snow="trodden"), {"name": "玉山主峰.gpx"}, o)
    assert o["summary"]["snow"]["season"]["url"] == HK.YUSHAN_SNOW_URL
    o = out("trodden")                       # 雪霸 is not covered (owner 2026-10-06)
    C.snow_season(C.PlanIn(type="baiyue", snow="trodden", peak="雪山"), {"name": None}, o)
    assert o["summary"]["snow"]["season"] is None
    o = out(None)                            # no snow picked: no line, whatever the date
    C.snow_season(C.PlanIn(type="baiyue", peak="玉山", date="2027-01-15"), {"name": None}, o)
    assert o["summary"]["snow"] is None
