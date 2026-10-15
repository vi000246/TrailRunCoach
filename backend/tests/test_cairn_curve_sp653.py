"""
SP-653: the 時間曲線 for 疊石 Cairn (`cairn-curve` v1, docs/spec/cairn-curve.md).

* Cairn's own segment statistics (rangeStats: haversine, 50 m moving average of the
  elevation, up / flat / down buckets by Δh) and its `munter-split` minutes, copied into
  engine/racepower/cairn_curve.py, against golden values printed by Cairn's code itself
  (inReachLiveTrack v2 app/src/lib/elevation.ts + pace.ts, bundled read-only, 2026-10-15);
* the curve built from a saved result: points = TRC's cumulative moving time, the
  munter-split fallback solved on Cairn's statistics (total within 1 %), start times,
  rest hint, no coordinates / stops / token;
* GET /api/v1/racepower/saved/{eid}/cairn-curve and the share snapshot's opt-in curve.

Synthetic data only; tmp DB and folders.
"""
from __future__ import annotations

import hashlib

import pytest

from backend.engine import event_gpx as EG
from backend.engine import race_calc_store as RC
from backend.engine.racepower import cairn_curve as CC
from backend.tests.test_event_gpx import synth_gpx

# ---- golden: Cairn's rangeStats / paceMinutes on a fixed track -------------------------
# The track (80 points, 3 without <ele>) and the numbers Cairn printed for it:
# directedSegmentStats(pts, from, to) → basis, paceMinutes(basis, munter-split 4/10 and 3.3/6.1).
RAW = [
    (24.0000719, 121.0000000, 1004.0), (24.0001971, 121.0000154, 1012.0), (24.0003725, 121.0000590, 1024.0),
    (24.0005935, 121.0001432, 1029.0), (24.0008533, 121.0002795, 1038.0), (24.0011436, 121.0004779, 1051.0),
    (24.0014546, 121.0007473, 1057.0), (24.0015059, 121.0008029, 1067.0), (24.0015852, 121.0009105, 1081.0),
    (24.0016824, 121.0010771, 1088.0), (24.0017862, 121.0013078, 1099.0), (24.0018844, 121.0016056, 1103.0),
    (24.0019648, 121.0019713, 1111.0), (24.0020147, 121.0024034, 1123.0), (24.0020158, 121.0024826, 1128.0),
    (24.0020037, 121.0026206, 1137.0), (24.0019668, 121.0028144, 1150.0), (24.0018937, 121.0030589, 1156.0),
    (24.0017739, 121.0033470, 1166.0), (24.0015981, 121.0036697, 1180.0), (24.0013582, 121.0040161, None),
    (24.0013085, 121.0040734, None), (24.0012120, 121.0041624, 1202.0), (24.0010621, 121.0042719, 1210.0),
    (24.0008542, 121.0043896, 1222.0), (24.0005852, 121.0045023, 1227.0), (24.0002543, 121.0045964, 1236.0),
    (23.9998626, 121.0046579, 1249.0), (23.9997907, 121.0046603, 1255.0), (23.9996652, 121.0046491, 1265.0),
    (23.9994886, 121.0046114, 1263.0), (23.9992654, 121.0045346, 1254.0), (23.9990019, 121.0044072, 1249.0),
    (23.9987062, 121.0042186, 1237.0), (23.9983880, 121.0039597, 1229.0), (23.9983352, 121.0039059, 1225.0),
    (23.9982529, 121.0038010, 1214.0), (23.9981511, 121.0036377, 1207.0), (23.9980411, 121.0034106, 1204.0),
    (23.9979346, 121.0031163, 1194.0), (23.9978441, 121.0027535, 1188.0), (23.9977823, 121.0023232, 1186.0),
    (23.9977790, 121.0022440, 1177.0), (23.9977872, 121.0021057, 1172.0), (23.9978188, 121.0019108, 1160.0),
    (23.9978851, 121.0016639, 1152.0), (23.9979969, 121.0013719, 1148.0), (23.9981636, 121.0010435, 1137.0),
    (23.9983939, 121.0006892, 1130.0), (23.9984419, 121.0006303, 1127.0), (23.9985360, 121.0005381, 1117.0),
    (23.9986827, 121.0004236, 1111.0), (23.9988873, 121.0002990, 1109.0), (23.9991530, 121.0001773, 1100.0),
    (23.9994811, 121.0000721, 1095.0), (23.9998710, 120.9999976, 1093.0), (23.9999428, 120.9999927, 1095.0),
    (24.0000686, 120.9999997, 1101.0), (24.0002461, 121.0000315, 1100.0), (24.0004713, 121.0001007, 1103.0),
    (24.0007382, 121.0002193, 1110.0), (24.0010390, 121.0003979, 1110.0), (24.0013643, 121.0006459, 1114.0),
    (24.0014185, 121.0006979, 1122.0), (24.0015037, 121.0008000, None), (24.0016099, 121.0009598, 1128.0),
    (24.0017262, 121.0011831, 1126.0), (24.0018408, 121.0014738, 1128.0), (24.0019413, 121.0018334, 1134.0),
    (24.0020150, 121.0022614, 1133.0), (24.0020204, 121.0023404, 1136.0), (24.0020160, 121.0024789, 1143.0),
    (24.0019899, 121.0026748, 1143.0), (24.0019304, 121.0029238, 1147.0), (24.0018268, 121.0032194, 1155.0),
    (24.0016692, 121.0035532, 1156.0), (24.0014489, 121.0039151, 1161.0), (24.0014025, 121.0039756, 1159.0),
    (24.0013110, 121.0040709, 1161.0), (24.0011675, 121.0041903, 1167.0),
]
GOLDEN = [
    ((0, 79), {'distanceM': 2042.2638448392127, 'ascentM': 271.7166666666666, 'descentM': 162.54999999999995, 'distUpM': 1135.4031371750923, 'distFlatM': 334.9910470634254, 'distDownM': 571.8696606006947}, (73.84411685320275, 95.93667722956954)),
    ((0, 30), {'distanceM': 764.1249606929291, 'ascentM': 206.33333333333337, 'descentM': 0.7999999999999545, 'distUpM': 583.6748778736845, 'distFlatM': 166.4487992697227, 'distDownM': 14.001283549521768}, (41.26383486314289, 50.468706378195655)),
    ((30, 55), {'distanceM': 688.1200122613408, 'ascentM': 1.349999999999909, 'descentM': 161.75, 'distUpM': 70.02653875560733, 'distFlatM': 60.22509645456051, 'distDownM': 557.8683770511731}, (14.821323456108797, 23.684561363361915)),
    ((55, 79), {'distanceM': 590.0188718849428, 'ascentM': 64.0333333333333, 'descentM': 0, 'distUpM': 481.7017205458006, 'distFlatM': 108.31715133914219, 'distDownM': 0}, (17.758958533951077, 21.783409488011976)),
    ((10, 25), {'distanceM': 397.60865889242933, 'ascentM': 97.25, 'descentM': 0, 'distUpM': 291.2472311234225, 'distFlatM': 106.36142776900684, 'distDownM': 0}, (19.86787784772854, 24.33502784494317)),
    ((60, 70), {'distanceM': 273.0260251084266, 'ascentM': 29.916666666666515, 'descentM': 0, 'distUpM': 238.90546843085417, 'distFlatM': 34.120556677572395, 'distDownM': 0}, (8.363543940841982, 10.218711303653112)),
]


def _pts():
    return [(la, lo, e) for la, lo, e in RAW]


def test_cairn_range_stats_match_the_golden_values():
    pts = _pts()
    ele = CC.smooth_elevations(pts)
    for (a, b), want, _mins in GOLDEN:
        got = CC.range_basis(pts, ele, a, b)
        for k, v in want.items():
            assert got[k] == pytest.approx(v, abs=1e-6), (a, b, k)


def test_cairn_munter_split_minutes_match_the_golden_values():
    """TRC computes the same Cairn minutes as Cairn's paceMinutes (the ticket: ±1 min; here ±1e-6)."""
    pts = _pts()
    ele = CC.smooth_elevations(pts)
    for (a, b), _want, (m1, m2) in GOLDEN:
        basis = CC.range_basis(pts, ele, a, b)
        assert CC.munter_split_min(basis, 4, 10) == pytest.approx(m1, abs=1e-6)
        assert CC.munter_split_min(basis, 3.3, 6.1) == pytest.approx(m2, abs=1e-6)


def test_hand_computed_munter_split():
    """1 km up 100 m + 1 km down 200 m + 1 km flat, EPH 4 / 10: (1+1)/4·60 + (1+2)/10·60 + 1/7·60."""
    b = {"distUpM": 1000, "ascentM": 100, "distDownM": 1000, "descentM": 200, "distFlatM": 1000}
    assert CC.munter_split_min(b, 4, 10) == pytest.approx(30 + 18 + 60 / 7)


def test_ranges_add_up_to_the_whole():
    """Smoothing once over the whole track keeps the statistics additive (Cairn 慣例 13)."""
    pts = _pts()
    ele = CC.smooth_elevations(pts)
    whole = CC.range_basis(pts, ele, 0, 79)
    parts = [CC.range_basis(pts, ele, a, b) for a, b in ((0, 30), (30, 55), (55, 79))]
    for k in ("distanceM", "ascentM", "descentM", "distUpM", "distFlatM", "distDownM"):
        assert sum(p[k] for p in parts) == pytest.approx(whole[k])


def test_gpx_points_like_cairn():
    """Only <trkpt> (no <rtept>), namespaced or not, <ele> from a direct child, missing ele = None."""
    gpx = (b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><rte><rtept lat="1" lon="1"/></rte>'
           b'<trk><trkseg><trkpt lat="24.1" lon="121.2"><ele>100.5</ele></trkpt>'
           b'<trkpt lat="24.2" lon="121.3"/></trkseg><trkseg><trkpt lat=" 24.3 " lon="121.4"><ele> 7 </ele></trkpt>'
           b'<trkpt lat="x" lon="121"><ele>1</ele></trkpt></trkseg></trk></gpx>')
    assert CC.gpx_points(gpx) == [(24.1, 121.2, 100.5), (24.2, 121.3, None), (24.3, 121.4, 7.0)]
    with pytest.raises(CC.CurveError):
        CC.gpx_points(b"not xml")


# ---- the curve ------------------------------------------------------------------------

LEGS = [(6, 1000), (4, -400), (2, 200)]          # 12 km ↑1200 ↓400 (test_event_gpx's ONE)
GPX = synth_gpx(LEGS)


def _segments(times, days=None, km=12.0):
    """TRC-like segments, equal km each, the given moving seconds."""
    n = len(times)
    out, cum = [], 0.0
    for i, t in enumerate(times):
        cum += t
        s = {"i": i, "start_km": km * i / n, "end_km": km * (i + 1) / n, "dist_m": km * 1000 / n, "t": t, "cum_s": cum,
             "eta": "07:00", "power": 200, "lat": 24.0, "lon": 121.0}
        if days:
            s["day"] = days[i]
        out.append(s)
    return out


def _trail_result(times=None):
    times = times or [1500, 1800, 2100, 1200, 900, 1000, 1100, 1300]
    return {"type": "trail", "course_id": hashlib.sha1(GPX).hexdigest(), "course_source": "gpx",
            "segments": _segments(times),
            "summary": {"km": 12.0, "time_s": float(sum(times)), "total_method": "trail_hr", "mode": "auto",
                        "nonmoving": {"total_s": 900.0, "p25_s": 600.0, "p75_s": 1200.0, "method": "rate"}},
            "profile": {"km": [0, 12], "z": [1000, 1800], "lat": [24, 24.1], "lon": [121, 121]}}


def _baiyue_result():
    times = [3000, 3600, 4000, 2500, 2000, 2400]
    days = [1, 1, 1, 2, 2, 2]
    segs = _segments(times, days)
    return {"type": "baiyue", "course_id": hashlib.sha1(GPX).hexdigest(), "course_source": "gpx", "segments": segs,
            "summary": {"km": 12.0, "time_s": float(sum(times)), "total_method": "capacity", "mode": "auto",
                        "day_starts": ["05:00", "04:30"], "moving_ratio": 0.8},
            "days": [{"day": 1, "moving_h": 10600 / 3600, "clock_h": 10600 / 3600 / 0.8},
                     {"day": 2, "moving_h": 6900 / 3600, "clock_h": 6900 / 3600 / 0.8}]}


def _keys(v, acc=None):
    acc = set() if acc is None else acc
    if isinstance(v, dict):
        for k, x in v.items():
            acc.add(k)
            _keys(x, acc)
    elif isinstance(v, list):
        for x in v:
            _keys(x, acc)
    return acc


def test_curve_shape_and_identity():
    res = _trail_result()
    c = CC.build(res, GPX, start_times=["07:00"], computed_at="2026-10-15T08:00:00")
    assert c["v"] == 1 and c["source"] == "trailruncoach"
    assert c["gpx_sha256"] == hashlib.sha256(GPX).hexdigest()
    pts = CC.gpx_points(GPX)
    total = sum(CC.haversine_m(*pts[i - 1][:2], *pts[i][:2]) for i in range(1, len(pts)))
    assert c["total_m"] == pytest.approx(total, abs=0.1)              # measured the Cairn way
    assert c["points"][0] == [0, 0]
    assert c["points"][-1][0] == pytest.approx(c["total_m"], abs=0.1)
    ms = [p[0] for p in c["points"]]
    assert all(b > a for a, b in zip(ms, ms[1:]))                     # strictly increasing distance
    ts = [p[1] for p in c["points"]]
    assert all(b >= a for a, b in zip(ts, ts[1:]))
    assert c["start_times"] == ["07:00"] and c["day_splits_km"] == []
    assert c["computed_at"] == "2026-10-15T08:00:00" and c["model"] == "trail/trail_hr"
    assert c["fallback"]["kind"] == "munter-split"


def test_any_two_points_differ_by_the_segments_between():
    """Acceptance ①: T(b) − T(a) = the sum of TRC's segment times in between."""
    res = _trail_result()
    c = CC.build(res, GPX, start_times=["07:00"])
    t = [s["t"] for s in res["segments"]]
    assert len(c["points"]) == len(t) + 1
    for a in range(len(c["points"])):
        for b in range(a, len(c["points"])):
            assert c["points"][b][1] - c["points"][a][1] == pytest.approx(sum(t[a:b]), abs=0.15)


def test_no_coordinates_stops_or_token():
    res = _trail_result()
    c = CC.build(res, GPX, start_times=["07:00"])
    keys = _keys(c)
    assert not keys & {"lat", "lon", "lng", "profile", "stops", "eta", "token", "power", "segments", "wpts"}
    assert set(c) == {"v", "source", "gpx_sha256", "total_m", "day_splits_km", "start_times", "points", "fallback",
                      "fallback_check", "rest_hint", "computed_at", "model"}


def test_fallback_total_within_one_percent_and_days_reported():
    """Acceptance ②: the whole route under the fallback ≈ TRC's moving time (≤ 1 %)."""
    for res in (_trail_result(), _baiyue_result()):
        c = CC.build(res, GPX, start_times=["06:00"])
        fb = c["fallback"]
        pts = CC.gpx_points(GPX)
        ele = CC.smooth_elevations(pts)
        whole = CC.range_basis(pts, ele, 0, len(pts) - 1)
        mins = CC.munter_split_min(whole, fb["upEph"], fb["downEph"])
        trc = sum(s["t"] for s in res["segments"])
        assert abs(mins * 60 - trc) / trc <= 0.01
        chk = c["fallback_check"]
        assert abs(chk["total_err_pct"]) <= 1.0 and chk["days"] and all("err_pct" in d for d in chk["days"])


def test_fallback_recovers_known_parameters():
    """TRC times made by Cairn's own munter-split (3.5 / 7) → the solver gives 3.5 / 7 back."""
    pts = CC.gpx_points(GPX)
    ele = CC.smooth_elevations(pts)
    cum = CC.cum_distance(pts)
    n = 12
    bounds = [CC.index_at_m(cum, cum[-1] * i / n) for i in range(n + 1)]
    times = [CC.munter_split_min(CC.range_basis(pts, ele, bounds[i], bounds[i + 1]), 3.5, 7) * 60 for i in range(n)]
    c = CC.build(_trail_result(times), GPX, start_times=[None])
    assert c["fallback"]["upEph"] == pytest.approx(3.5, rel=0.02)
    assert c["fallback"]["downEph"] == pytest.approx(7, rel=0.05)


def test_multi_day_splits_start_times_and_rest_hint():
    res = _baiyue_result()
    c = CC.build(res, GPX, start_times=CC.start_times_of(res, None))
    assert c["start_times"] == ["05:00", "04:30"]
    assert c["day_splits_km"] == [pytest.approx(6.0, abs=0.05)]
    rh = c["rest_hint"]
    assert rh["basis"] == "moving_ratio"
    assert [d["day"] for d in rh["days"]] == [1, 2]
    assert rh["days"][0]["rest_s"] == pytest.approx(10600 / 0.8 - 10600, abs=1)
    # the trail race: the non-moving prediction, one day
    t = CC.build(_trail_result(), GPX, start_times=["07:00"])
    assert t["rest_hint"]["basis"] == "nonmoving:rate"
    assert t["rest_hint"]["days"] == [{"day": 1, "rest_s": 900, "range_s": [600, 1200]}]
    # no estimate → null (Cairn works without it)
    r = _trail_result()
    r["summary"].pop("nonmoving")
    assert CC.build(r, GPX, start_times=["07:00"])["rest_hint"] is None


def test_start_times_of_a_one_day_plan():
    assert CC.start_times_of(_trail_result(), "07:30") == ["07:30"]
    assert CC.start_times_of(_trail_result(), None) == [None]


def test_refuses_what_is_not_the_same_route():
    res = _trail_result()
    with pytest.raises(CC.CurveError) as e:
        CC.build(res, synth_gpx([(13, 1000)]), start_times=[None])     # 13 km vs TRC's 12 km: > 1 %
    assert e.value.code == "GPX_MISMATCH"
    with pytest.raises(CC.CurveError) as e:
        CC.build({**res, "segments": []}, GPX, start_times=[None])
    assert e.value.code == "NO_SEGMENTS"
    rte = b'<gpx><rte><rtept lat="24" lon="121"/><rtept lat="24.1" lon="121"/></rte></gpx>'
    with pytest.raises(CC.CurveError) as e:
        CC.build(res, rte, start_times=[None])
    assert e.value.code == "NO_TRACK"


# ---- from a saved calculation ------------------------------------------------------------

@pytest.fixture
def stores(tmp_path, monkeypatch):
    db, root = tmp_path / "app.db", tmp_path / "gpx"
    monkeypatch.setattr(EG, "_default_db", lambda: db)
    monkeypatch.setattr(EG, "ROOT", root)
    monkeypatch.setattr(RC, "_default_db", lambda: db)
    EG._memo.clear()
    return db


def test_for_saved(stores):
    with pytest.raises(CC.CurveError) as e:
        CC.for_saved("ev1")
    assert e.value.code == "NO_RESULT" and e.value.status == 404
    RC.save("ev1", {"form": {"start": "06:30"}, "course": {"course_id": "x"}}, _trail_result())
    with pytest.raises(CC.CurveError) as e:
        CC.for_saved("ev1")                                            # no GPX stored with the event
    assert e.value.code == "NO_GPX" and e.value.status == 409
    EG.save("ev1", GPX, "route.gpx")
    c = CC.for_saved("ev1")
    assert c["gpx_sha256"] == hashlib.sha256(GPX).hexdigest() and c["start_times"] == ["06:30"]
    assert c["computed_at"] == RC.get("ev1")["saved_at"].replace(" ", "T")
    # the event's GPX replaced after the calculation: the saved result is for another file
    EG.save("ev1", synth_gpx([(6, 900), (4, -400), (2, 200)]), "route2.gpx")
    with pytest.raises(CC.CurveError) as e:
        CC.for_saved("ev1")
    assert e.value.code == "GPX_CHANGED" and e.value.status == 409
    # a manual course
    RC.save("ev2", {"form": {}}, {**_trail_result(), "course_id": None, "course_source": "manual"})
    with pytest.raises(CC.CurveError) as e:
        CC.for_saved("ev2")
    assert e.value.code == "NO_GPX"


def test_saved_curve_api(stores, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    app = FastAPI()
    app.include_router(RP.router)
    cl = TestClient(app)
    r = cl.get("/api/v1/racepower/saved/ev1/cairn-curve")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "NO_RESULT"
    RC.save("ev1", {"form": {"start": "06:30"}}, _trail_result())
    EG.save("ev1", GPX, "route.gpx")
    r = cl.get("/api/v1/racepower/saved/ev1/cairn-curve")
    assert r.status_code == 200 and r.json()["v"] == 1 and "attachment" not in r.headers.get("content-disposition", "")
    r = cl.get("/api/v1/racepower/saved/ev1/cairn-curve?download=1")
    assert r.status_code == 200 and "cairn-curve-ev1.json" in r.headers["content-disposition"]
    assert cl.get("/api/v1/racepower/saved/..%2Fx/cairn-curve").status_code in (400, 404)


# ---- the share snapshot: off unless ticked ---------------------------------------------

def test_share_snapshot_curve_only_when_ticked(stores, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    from backend.engine.racepower import share as SH
    monkeypatch.setattr(SH, "SHARES_DIR", tmp_path / "shares")
    monkeypatch.setattr(RP, "make_plan", lambda body: _trail_result())
    EG.save("ev1", GPX, "route.gpx")
    app = FastAPI()
    app.include_router(RP.router)
    app.include_router(RP.share_router)
    cl = TestClient(app)
    body = {"type": "trail", "start_time": "07:00", "course": {"course_id": hashlib.sha1(GPX).hexdigest(), "event_id": "ev1"}}
    sid = cl.post("/api/v1/racepower/share", json=body).json()["id"]
    snap = cl.get(f"/share/{sid}/data").json()
    assert "cairn_curve" not in snap
    sid = cl.post("/api/v1/racepower/share", json={**body, "include_cairn_curve": True}).json()["id"]
    snap = cl.get(f"/share/{sid}/data").json()
    cc = snap["cairn_curve"]
    assert cc["v"] == 1 and cc["gpx_sha256"] == hashlib.sha256(GPX).hexdigest() and cc["start_times"] == ["07:00"]
    # ticked, but the course is no stored GPX: said, not silently left out
    r = cl.post("/api/v1/racepower/share", json={**body, "course": {"manual": {"km": 12}}, "include_cairn_curve": True})
    assert r.status_code == 400


def test_share_snapshot_function_default_has_no_curve():
    from backend.engine.racepower import share as SH
    snap = SH.snapshot(_trail_result(), title="t")
    assert "cairn_curve" not in snap
    snap = SH.snapshot(_trail_result(), title="t", cairn_curve={"v": 1, "points": [[0, 0]]})
    assert snap["cairn_curve"]["v"] == 1


def test_module_needs_no_private_core():
    """The public mirror (SP-500) stubs the calculation core; this module must not import it."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(CC))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and ("wko5" in m or m.endswith("pacing") or "algorithms" in m) for m in mods), mods


def test_spec_doc_has_the_example_and_cairn_rules():
    """docs/spec/cairn-curve.md: a valid example and Cairn's rules (hash, 1 % length, backwards → fallback)."""
    import json
    import re
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[2] / "docs" / "spec" / "cairn-curve.md").read_text(encoding="utf-8")
    ex = json.loads(re.search(r"```json\n(.*?)```", doc, re.S).group(1))
    assert ex["v"] == 1 and ex["points"][0] == [0, 0] and ex["fallback"]["kind"] == "munter-split"
    assert set(ex) == set(CC.build(_trail_result(), GPX, start_times=[None]))
    for words in ("gpx_sha256", "1 %", "倒著走", "fallback", "rest_hint", "start_times"):
        assert words in doc
