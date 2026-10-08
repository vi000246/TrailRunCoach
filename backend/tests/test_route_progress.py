"""Repeated segments and routes on synthetic tracks: geometry, matching,
direction, clustering, metrics against climbs.py / wko5_hr, incremental builds."""
import datetime as dt
import json
import math
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import numpy as np
import pytest

from backend.engine import routes as R
from backend.engine.algorithms import route_match as RM
from backend.engine.algorithms.climbs import _measure, detect_climbs
from backend.engine.algorithms.wko5_hr import hr_tss

LAT0, LON0 = 24.0, 121.0
KX = math.radians(1) * RM.R_EARTH * math.cos(math.radians(LAT0))
KY = math.radians(1) * RM.R_EARTH


def ll(x, y):
    return LAT0 + y / KY, LON0 + x / KX


def polyline(points, step=5.0):
    """Waypoints (m) -> points every `step` m along the path."""
    out = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        out += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(1, n + 1)]
    return out


def activity(path, elev_of=lambda s: 100.0, speed=3.0, hr=150.0, noise=(0.0, 0.0), start="2025-01-01T08:00",
             file=None, stop=None, sport="run", sport_type="trail running"):
    """A synthetic activity walking `path` (m waypoints) at `speed` m/s, 1 Hz.
    elev_of(s): elevation at path distance s. stop=(s_at, seconds) adds a halt."""
    pts = polyline(path, step=speed)
    t, lat, lon, d, e, h, sp = [], [], [], [], [], [], []
    s, ti = 0.0, 0.0
    for k, (x, y) in enumerate(pts):
        if k:
            s += math.hypot(x - pts[k - 1][0], y - pts[k - 1][1])
            ti += 1.0
        if stop and k and s >= stop[0] > s - speed:
            for _ in range(int(stop[1])):
                a, b = ll(x + noise[0], y + noise[1])
                t.append(ti); lat.append(a); lon.append(b); d.append(s / 1000); e.append(elev_of(s))
                h.append(hr - 20); sp.append(0.0)
                ti += 1.0
        a, b = ll(x + noise[0], y + noise[1])
        t.append(ti); lat.append(a); lon.append(b); d.append(s / 1000); e.append(elev_of(s))
        h.append(hr + 10 * math.sin(k / 30.0)); sp.append(speed * 3.6)
    meta = {"file": file or start, "start": start, "sport": sport, "sport_type": sport_type}
    return meta, dict(t=t, lat=lat, lon=lon, dist_km=d, elev=e, hr=h, speed=sp)


def track_of(meta, ch, lthr=165.0):
    return R.extract_track(meta, ch["t"], ch["lat"], ch["lon"], ch["dist_km"], ch["elev"], ch["hr"],
                           None, ch["speed"], None, lthr=lthr)


# out-and-back up a 2 km, 10 % hill preceded by 600 m of flat
HILL = [(0, 0), (600, 0), (600, 2000)]
def hill_elev(s):            # up to 2600 m along, then back down
    if s <= 600:
        return 100.0
    if s <= 2600:
        return 100.0 + (s - 600) * 0.10
    if s <= 4600:
        return 300.0 - (s - 2600) * 0.10
    return 100.0
OUT_BACK = HILL + list(reversed(HILL))[1:]


# --- geometry ---------------------------------------------------------------

def test_discrete_frechet_matches_hand_computed_values():
    p = np.array([[0, 0], [1, 0], [2, 0]], float)
    q = np.array([[0, 1], [1, 1], [2, 1]], float)
    assert RM.discrete_frechet(p, q) == pytest.approx(1.0)
    # walked backwards: the coupling must pair p0 with (2, 1) -> sqrt(5)
    assert RM.discrete_frechet(p, q[::-1]) == pytest.approx(math.sqrt(5))
    # Eiter & Mannila: a detour sets the distance even if both ends match
    r = np.array([[0, 0], [1, 3], [2, 0]], float)
    assert RM.discrete_frechet(p, r) == pytest.approx(3.0)


def test_overlap_ratio_is_one_way_and_mutual_is_two_way():
    long_ = np.array(polyline([(0, 0), (2000, 0)], 25))
    short = np.array(polyline([(0, 5), (1000, 5)], 25))
    assert RM.overlap_ratio(short, long_) == 1.0
    assert RM.overlap_ratio(long_, short) == pytest.approx(0.5, abs=0.03)
    assert RM.mutual_overlap(short, long_) < 0.8


def test_point_to_segment_distance_not_point_to_point():
    q = np.array([[0, 0], [100, 0]], float)             # two points 100 m apart
    assert RM.dist_to_path(np.array([[50, 10]], float), q)[0] == pytest.approx(10.0)


def test_resample_keeps_raw_samples_and_forced_indices():
    meta, ch = activity([(0, 0), (1000, 0)], speed=3.0)
    idx = RM.resample_indices(ch["lat"], ch["lon"], 25.0, keep={100})
    assert idx[0] == 0 and idx[-1] == len(ch["lat"]) - 1 and 100 in idx
    gaps = np.diff([ch["dist_km"][i] * 1000 for i in idx])
    assert gaps.max() <= 27.1


def test_invalid_fixes_are_skipped():
    assert not RM.valid_fix(0.0, 0.0) and not RM.valid_fix(None, 121.0)
    assert not RM.valid_fix(float("nan"), 121.0) and RM.valid_fix(24.0, 121.0)


# --- matching and direction ---------------------------------------------------

def _xy(ch):
    return RM.project(ch["lat"], ch["lon"], LAT0, LON0)


def test_find_efforts_same_direction_only():
    ref = np.array(polyline([(600, 0), (600, 2000)], 25))
    _, up = activity(HILL)
    assert len(RM.find_efforts(_xy(up), ref)) == 1
    _, down = activity(list(reversed(HILL)))
    assert RM.find_efforts(_xy(down), ref) == []


def test_find_efforts_counts_every_repeat_in_one_activity():
    ref = np.array(polyline([(600, 0), (600, 2000)], 25))
    _, reps = activity(OUT_BACK + OUT_BACK[1:])       # up and down twice
    got = RM.find_efforts(_xy(reps), ref)
    assert len(got) == 2
    assert got[0][1] < got[1][0]


def test_find_efforts_tolerates_gps_offset_but_not_another_trail():
    ref = np.array(polyline([(600, 0), (600, 2000)], 25))
    _, off = activity(HILL, noise=(15.0, 0.0))
    assert len(RM.find_efforts(_xy(off), ref)) == 1
    _, other = activity([(0, 0), (600, 0), (700, 1000), (600, 2000)])   # a 100 m detour mid-way
    assert RM.find_efforts(_xy(other), ref) == []


def test_common_runs_same_direction():
    a = np.array(polyline([(0, 0), (0, 1000), (1500, 1000), (1500, 3000)], 25))
    b = np.array(polyline([(-800, 1000), (1500, 1000), (1500, -500)], 25))
    runs = RM.common_runs(a, b)
    assert len(runs) == 1
    i0, i1 = runs[0][:2]
    assert RM.path_length(a[i0:i1 + 1])[-1] == pytest.approx(1500, abs=60)
    assert RM.common_runs(a, b[::-1]) == []


def test_along_residual_detects_reversed_direction():
    ref = np.array(polyline([(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)], 25))
    same = ref + [5.0, 0.0]
    rev = ref[::-1] + [5.0, 0.0]
    assert (RM.along_residual(ref, same)[1] <= 60).mean() > 0.95
    assert (RM.along_residual(ref, rev)[1] <= 60).mean() < 0.5


# --- metrics agree with climbs.py and wko5_hr -----------------------------------

def test_effort_metrics_equal_climbs_measure_and_hr_tss():
    meta, ch = activity(HILL, elev_of=hill_elev, stop=(1500, 120))
    tr = R.Track(track_of(meta, ch))
    c = tr.raw["climbs"][0]
    raw_a, raw_b = tr.raw["idx"][c[0]], tr.raw["idx"][c[1]]
    moving = [s is not None and s > R.moving_threshold("run") for s in ch["speed"]]
    ref = _measure(ch["t"], ch["dist_km"], ch["elev"], ch["hr"], raw_a, raw_b, moving)
    m = R.effort_metrics(tr, c[0], c[1], "up")
    assert m["moving_s"] == pytest.approx(ref.duration_s, abs=0.5)
    assert m["elapsed_s"] == pytest.approx(ref.duration_s + 120, abs=1.0)   # the stop
    assert m["vam"] == pytest.approx(ref.vam_m_per_h, abs=1)
    assert m["avg_hr"] == pytest.approx(ref.avg_hr, abs=0.05)
    assert m["hr_per_100m"] == pytest.approx(ref.hr_per_100m, abs=1)
    assert m["hr_vam"] == pytest.approx(ref.avg_hr / ref.vam_m_per_h * 1000, abs=0.1)
    sl = slice(raw_a + 1, raw_b + 1)
    part = hr_tss(ch["t"][sl], ch["hr"][sl], 165.0, start=ch["t"][raw_a])[0]
    assert m["hrtss"] == pytest.approx(part, abs=0.01)
    whole = hr_tss(ch["t"], ch["hr"], 165.0)[0]
    assert tr.raw["hrtss_total"] == pytest.approx(whole, abs=0.01)
    assert m["hrtss_share"] == pytest.approx(part / whole, abs=1e-3)


def test_descents_are_found_on_negated_elevation():
    meta, ch = activity(OUT_BACK, elev_of=hill_elev)
    raw = track_of(meta, ch)
    assert len(raw["climbs"]) == 1 and len(raw["descents"]) == 1
    tr = R.Track(raw)
    a, b = raw["descents"][0]
    m = R.effort_metrics(tr, a, b, "down")
    assert m["vam"] is None and m["descent_rate"] > 0 and m["gain_m"] == pytest.approx(-200, abs=2)


# --- clustering / detection ---------------------------------------------------

def _tracks(*acts):
    return {m["file"]: R.Track(track_of(m, c)) for m, c in acts}


def test_repeated_climb_and_descent_become_two_segments():
    acts = [activity(OUT_BACK, elev_of=hill_elev, start=f"2025-0{k + 1}-01T08:00", noise=(k * 4.0, 0.0),
                     speed=3.0 + 0.2 * k) for k in range(3)]
    far = activity([(5000, 5000), (5600, 5000), (5600, 7000)], start="2025-05-01T08:00",
                   elev_of=lambda s: 100 + max(0, s - 600) * 0.1)
    idx = R.detect(_tracks(*acts, far))
    kinds = sorted(s["kind"] for s in idx["segments"])
    assert kinds.count("climb") == 1 and kinds.count("descent") == 1
    for s in idx["segments"]:
        if s["kind"] in ("climb", "descent"):
            assert len(s["efforts"]) == 3
            assert {e["file"] for e in s["efforts"]} == {a[0]["file"] for a in acts}
    # the lone far climb is kept as a pending reference, not a segment
    assert any(p["ref_file"] == far[0]["file"] for p in idx["pending"])


def test_climbs_with_slightly_different_endpoints_cluster_into_one():
    a = activity(HILL, elev_of=hill_elev, start="2025-01-01T08:00")
    b = activity([(0, 0), (600, 0), (600, 1960)], start="2025-02-01T08:00",
                 elev_of=lambda s: 100 if s <= 600 else 100 + (s - 600) * 0.1)
    idx = R.detect(_tracks(a, b))
    climbs = [s for s in idx["segments"] + idx["pending"] if s["kind"] == "climb"]
    assert len(climbs) == 1


LOOP = [(0, 0), (1500, 0), (1500, 1500), (0, 1500), (0, 0)]


def test_reversed_loop_is_the_same_route_marked_reversed():
    a = activity(LOOP, start="2025-01-01T08:00")
    b = activity(LOOP, start="2025-02-01T08:00", noise=(6.0, 3.0))
    c = activity(list(reversed(LOOP)), start="2025-03-01T08:00")
    idx = R.detect(_tracks(a, b, c))
    assert len(idx["routes"]) == 1
    r = idx["routes"][0]
    assert r["members"] == [a[0]["file"], b[0]["file"], c[0]["file"]]
    assert r["dirs"] == {a[0]["file"]: "same", b[0]["file"]: "same", c[0]["file"]: "reversed"}
    assert r["id"] == R.seg_id("route", a[0]["file"], 0, R.Track(track_of(*a)).raw["idx"][-1])


def test_loop_started_at_another_corner_is_the_same_route():
    # the same square, started at the opposite corner: start / end 2.1 km from
    # the first run's — the old rule (ends within 200 m) made it a second route
    other = [(1500, 1500), (0, 1500), (0, 0), (1500, 0), (1500, 1500)]
    a = activity(LOOP, start="2025-01-01T08:00")
    b = activity(other, start="2025-02-01T08:00", noise=(5.0, 0.0))
    c = activity(LOOP, start="2025-03-01T08:00", noise=(0.0, 5.0))
    idx = R.detect(_tracks(a, b, c))
    assert [len(r["members"]) for r in idx["routes"]] == [3]
    assert set(idx["routes"][0]["dirs"].values()) == {"same"}


def test_out_and_back_both_ways_is_one_route_same_direction():
    line = [(0, 0), (2500, 0), (2500, 800)]
    ob = line + list(reversed(line))[1:]
    a = activity(ob, start="2025-01-01T08:00")
    b = activity(ob, start="2025-02-01T08:00", noise=(4.0, 4.0))
    # walked from the far end: start and end 2.6 km from the others'
    far = list(reversed(line)) + line[1:]
    c = activity(far, start="2025-03-01T08:00")
    idx = R.detect(_tracks(a, b, c))
    assert [len(r["members"]) for r in idx["routes"]] == [3]
    # each leg lies on the other: an out-and-back has no wrong way round
    assert idx["routes"][0]["dirs"][a[0]["file"]] == "same" and idx["routes"][0]["dirs"][b[0]["file"]] == "same"


def test_runs_with_their_own_spurs_cluster_on_the_common_part():
    # the first run starts 600 m away (another car park): with the first run as
    # the reference and ends within 200 m, the old rule split these into two routes
    spur = [(-600, 0)] + LOOP
    a = activity(spur, start="2025-01-01T08:00")
    b = activity(LOOP, start="2025-02-01T08:00", noise=(4.0, 0.0))
    c = activity(LOOP, start="2025-03-01T08:00", noise=(0.0, 4.0))
    d = activity(LOOP + [(0, -400)], start="2025-04-01T08:00", noise=(3.0, 3.0))   # a cool-down spur
    idx = R.detect(_tracks(a, b, c, d))
    assert [len(r["members"]) for r in idx["routes"]] == [4]
    r = idx["routes"][0]
    # canonical = the part more than half of the runs share: the 6 km loop, no spurs
    assert r["length_m"] == pytest.approx(6000, abs=80)


def test_a_run_over_part_of_a_route_is_linked_not_a_new_route():
    a = activity(LOOP, start="2025-01-01T08:00")
    b = activity(LOOP, start="2025-02-01T08:00", noise=(5.0, 0.0))
    half = activity([(0, 0), (1500, 0), (1500, 1500)], start="2025-03-01T08:00", noise=(0.0, 5.0))
    idx = R.detect(_tracks(a, b, half))
    assert len(idx["routes"]) == 1
    r = idx["routes"][0]
    assert half[0]["file"] not in r["members"]
    assert [p["file"] for p in r["partials"]] == [half[0]["file"]] and r["partials"][0]["share"] >= 0.8
    # ... and two runs of that part are a sub-route of the loop, not a top-level route
    half2 = activity([(0, 0), (1500, 0), (1500, 1500)], start="2025-04-01T08:00", noise=(4.0, 4.0))
    idx = R.detect(_tracks(a, b, half, half2))
    loop = next(x for x in idx["routes"] if a[0]["file"] in x["members"])
    sub = next(x for x in idx["routes"] if half[0]["file"] in x["members"])
    assert sub["parent"] == loop["id"] and loop["parent"] is None and loop["partials"] == []


def test_route_ids_carry_over_and_merged_routes_become_aliases():
    a = activity([(-600, 0)] + LOOP, start="2025-01-01T08:00", file="ra")
    b = activity(LOOP, start="2025-02-01T08:00", noise=(4.0, 0.0), file="rb")
    c = activity(LOOP, start="2025-03-01T08:00", noise=(0.0, 4.0), file="rc")
    tracks = _tracks(a, b, c)
    # an old index with two routes for these runs (as the old rule built them)
    old = [{"id": "rOLD1", "ref_file": "rb", "members": ["rb", "rc"]},
           {"id": "rOLD2", "ref_file": "ra", "members": ["ra", "rx"]}]
    routes = R.cluster_routes(tracks, old)
    assert len(routes) == 1
    assert routes[0]["id"] == "rOLD1" and routes[0]["ref_file"] == "rb" and routes[0]["aliases"] == ["rOLD2"]


def test_direction_shares_are_start_free():
    sq = np.array(polyline(LOOP, 25))
    shifted = np.roll(sq[:-1], 30, axis=0)            # the same loop from another point
    f, r = RM.direction_shares(sq, shifted + 6.0)
    assert f > 0.95 and r < 0.2
    f, r = RM.direction_shares(sq, shifted[::-1] + 6.0)
    assert r > 0.95 and f < 0.2


def _seg(sid, kind, pts, files):
    lat, lon = zip(*(ll(x, y) for x, y in polyline(pts, 25)))
    xy = RM.project(lat, lon, lat[0], lon[0])
    return R.Segment(id=sid, kind=kind, family="foot", ref_file=files[0], ref_i0=0, ref_i1=len(lat) - 1,
                     lat=list(lat), lon=list(lon), length_m=float(RM.path_length(xy)[-1]), gain_m=200.0,
                     created="2025", efforts=[{"file": f, "i0": 0, "i1": 1} for f in files])


def test_climb_inside_a_longer_climb_with_the_same_activities_is_dropped():
    long_ = _seg("s1", "climb", [(0, 0), (0, 1500)], ["a", "b"])
    inner = _seg("s2", "climb", [(0, 500), (0, 1500)], ["a", "b"])
    more = _seg("s3", "climb", [(0, 500), (0, 1500)], ["a", "b", "c"])
    out = R._finish([long_, inner], [], {})
    assert [s["id"] for s in out["segments"]] == ["s1"]
    # done by an extra activity, the inner climb is the common part of more
    # activities, and the longer one adds nobody: only the common part is listed
    out = R._finish([long_, more], [], {})
    assert sorted(s["id"] for s in out["segments"]) == ["s3"]
    assert "s1" in {s["id"] for s in out["pending"]}
    # a longer climb done by someone the inner one misses carries information: both kept
    other = _seg("s4", "climb", [(0, 0), (0, 1500)], ["a", "b", "d"])
    out = R._finish([other, more], [], {})
    assert sorted(s["id"] for s in out["segments"]) == ["s3", "s4"]


def test_a_stretch_that_is_a_route_is_folded_into_it():
    route = {"id": "rL", "kind": "route", "family": "foot", "members": ["a", "b", "c"], "partials": [],
             "parent": None}
    route["lat"], route["lon"] = map(list, zip(*(ll(x, y) for x, y in polyline(LOOP, 25))))
    st = _seg("sS", "stretch", [(0, 0), (1500, 0), (1500, 1200)], ["a", "b", "c"])
    away = _seg("sA", "stretch", [(0, 0), (1500, 0), (1500, 1200)], ["a", "x", "y", "z"])   # mostly other runs
    out = R._finish([st, away], [route], {})
    assert [s["id"] for s in out["segments"]] == ["sA"]
    assert out["routes"][0]["stretches"] == ["sS"]


def test_a_chain_of_stretches_is_merged_into_its_common_part():
    path = [(0, 0), (3000, 0)]
    acts = [activity([(-500, 300), (0, 0)] + path, start="2025-01-01T08:00", file="c0"),
            activity([(-500, -300), (0, 0)] + path, start="2025-02-01T08:00", file="c1", noise=(0, 4.0)),
            activity(path + [(3500, 300)], start="2025-03-01T08:00", file="c2", noise=(0, -4.0))]
    tracks = _tracks(*acts)
    host = tracks["c0"]
    X = RM.project(host.lat, host.lon, LAT0, LON0)              # the test's metres
    on = np.flatnonzero(np.abs(X[:, 1]) < 3)                   # c0's points on the shared path
    k = lambda m: int(on[np.argmin(np.abs(X[on, 0] - m))])     # c0's point at path metre m
    pieces = []
    for sid_end in ((0, 1600), (1500, 3000)):
        s = R._new_segment("stretch", host, k(sid_end[0]), min(len(host) - 1, k(sid_end[1])))
        for f in ("c0", "c1", "c2"):
            s.efforts += R.match_segment(s, tracks[f])
        pieces.append(s)
    assert all({e["file"] for e in p.efforts} == {"c0", "c1", "c2"} for p in pieces)
    out = R._finish(pieces, [], tracks)
    assert len(out["segments"]) == 1
    m = out["segments"][0]
    assert m["derived"] and sorted(m["merged_from"]) == sorted(p.id for p in pieces)
    assert m["length_m"] == pytest.approx(3000, abs=60)
    assert {e["file"] for e in m["efforts"]} == {"c0", "c1", "c2"}
    assert {p.id for p in pieces} <= {s["id"] for s in out["pending"]}
    # derived stretches are never references: a later build rebuilds them
    assert all(not s.get("derived") for s in out["pending"])


# --- thumbnails ---------------------------------------------------------------

def _thumb_points(t):
    return np.array([[float(v) for v in p.split(",")] for p in t["d"][1:].split("L")])


def test_thumbnail_keeps_the_shape_north_up_at_taiwan_latitude():
    # an L: 1 km east, then 1 km north, at 24 °N
    lat, lon = zip(*(ll(x, y) for x, y in polyline([(0, 0), (1000, 0), (1000, 1000)], 10)))
    t = RM.thumbnail(lat, lon, w=64, h=40, pad=3)
    p = _thumb_points(t)
    assert len(p) == 3                                   # corners kept, the straight legs simplified
    east, north = p[1] - p[0], p[2] - p[1]
    # equal legs on the ground are equal on screen: x is scaled by cos(lat).
    # Plotting raw degrees would make the east leg 1/cos(24°) = 1.095 x longer
    assert abs(east[0]) == pytest.approx(abs(north[1]), rel=0.005)
    assert east[0] > 0 and abs(east[1]) < 0.05          # east = right
    assert north[1] < 0 and abs(north[0]) < 0.05        # north = up (SVG y grows down)
    # one scale for both axes: the 1 x 1 km box fills the 34 px height, centred in the width
    assert north[1] == pytest.approx(-(40 - 2 * 3), abs=0.05)
    assert (p[:, 0].min() + p[:, 0].max()) / 2 == pytest.approx(32, abs=0.05)
    assert t["start"] == [round(p[0, 0], 1), round(p[0, 1], 1)]


def test_thumbnail_aspect_of_a_wide_loop_and_detail_within_a_pixel():
    loop = [(0, 0), (3000, 0), (3000, 1000), (0, 1000), (0, 0)]
    lat, lon = zip(*(ll(x, y) for x, y in polyline(loop, 10)))
    t = RM.thumbnail(lat, lon, w=64, h=40, pad=3)
    p = _thumb_points(t)
    wpx, hpx = np.ptp(p[:, 0]), np.ptp(p[:, 1])
    assert wpx / hpx == pytest.approx(3.0, rel=0.01)    # 3 km x 1 km stays 3 : 1
    assert wpx == pytest.approx(58, abs=0.05)           # the wide side fills the width
    # every GPS point lies within half a pixel of the drawn line
    xy = RM.project(lat, lon, (min(lat) + max(lat)) / 2, (min(lon) + max(lon)) / 2)
    s = 58 / np.ptp(xy[:, 0])
    raw = np.column_stack(((xy[:, 0] - xy[:, 0].min()) * s + 3, (xy[:, 1].max() - xy[:, 1]) * s + (40 - np.ptp(xy[:, 1]) * s) / 2))
    assert RM.dist_to_path(raw, p).max() < 0.5
    assert RM.thumbnail([24.0], [121.0]) is None


def test_shared_flat_stretch_becomes_a_segment():
    a = activity([(0, 0), (0, 1000), (1500, 1000), (1500, 3000)], start="2025-01-01T08:00")
    b = activity([(-800, 1000), (1500, 1000), (1500, -500)], start="2025-02-01T08:00")
    idx = R.detect(_tracks(a, b))
    st = [s for s in idx["segments"] if s["kind"] == "stretch"]
    assert len(st) == 1
    assert st[0]["length_m"] == pytest.approx(1500, abs=80)
    assert {e["file"] for e in st[0]["efforts"]} == {a[0]["file"], b[0]["file"]}


# --- builder: incremental == full, ids stable ---------------------------------

def _builder(tmp_path, acts):
    files = {}
    for m, c in acts:
        p = tmp_path / "src" / (m["file"].replace(":", "-") + ".bin")
        p.parent.mkdir(exist_ok=True)
        p.write_text(m["file"])
        files[m["file"]] = (p, m, c)
    wl = [(f, p, {**m, "lthr": 165.0}) for f, (p, m, c) in files.items()]
    reader = lambda f, p, meta: track_of(files[f][1], files[f][2])
    return wl, reader


def _summary(idx):
    return sorted((s["kind"], s["id"], tuple(sorted((e["file"], e["i0"]) for e in s["efforts"])))
                  for s in idx["segments"])


def test_incremental_build_matches_full_and_keeps_ids(tmp_path):
    acts = [activity(OUT_BACK, elev_of=hill_elev, start=f"2025-0{k + 1}-01T08:00", noise=(k * 3.0, 0.0),
                     file=f"f{k}") for k in range(3)]
    b1 = R.Builder(R.RouteStore(tmp_path / "inc"))
    wl, reader = _builder(tmp_path, acts[:2])
    first = b1.build(wl, reader)
    ids_before = {s["id"] for s in first["segments"]}
    assert ids_before and all(len(s["efforts"]) == 2 for s in first["segments"])
    wl3, reader3 = _builder(tmp_path, acts)
    inc = b1.build(wl3, reader3)
    assert ids_before <= {s["id"] for s in inc["segments"]}
    assert all(len(s["efforts"]) == 3 for s in inc["segments"] if s["kind"] in ("climb", "descent"))
    full = R.Builder(R.RouteStore(tmp_path / "full")).build(wl3, reader3, full=True)
    assert _summary(inc) == _summary(full)
    members = lambda ix: sorted(tuple(r["members"]) for r in ix["routes"])
    assert members(inc) == members(full) == [("f0", "f1", "f2")]
    assert {r["id"] for r in first["routes"]} <= {r["id"] for r in inc["routes"]}     # the route keeps its id
    # enrich ran: metrics and names are in the saved index
    e = inc["segments"][0]["efforts"][0]
    assert e["elapsed_s"] > 0 and inc["segments"][0]["auto_name"]
    # nothing changed -> the index is reused as is
    assert b1.build(wl3, reader3) == R.RouteStore(tmp_path / "inc").load_index()
    # a removed activity loses its efforts
    wl2, reader2 = _builder(tmp_path, acts[1:])
    after = b1.build(wl2, reader2)
    assert all("f0" not in {x["file"] for x in s["efforts"]} for s in after["segments"])


def test_incremental_new_activity_creates_segment_with_older_track(tmp_path):
    a = activity(HILL, elev_of=hill_elev, start="2025-01-01T08:00", file="a")
    b = activity(HILL, elev_of=hill_elev, start="2025-02-01T08:00", file="b", noise=(5.0, 0.0))
    bld = R.Builder(R.RouteStore(tmp_path / "s"))
    wl, rd = _builder(tmp_path, [a])
    assert bld.build(wl, rd)["segments"] == []
    wl, rd = _builder(tmp_path, [a, b])
    idx = bld.build(wl, rd)
    climbs = [s for s in idx["segments"] if s["kind"] == "climb"]
    assert len(climbs) == 1 and {e["file"] for e in climbs[0]["efforts"]} == {"a", "b"}
    assert climbs[0]["ref_file"] == "a"


def test_api_compare_gap_ends_at_the_elapsed_difference(tmp_path, monkeypatch):
    from backend.api import routes as API
    acts = [activity(OUT_BACK, elev_of=hill_elev, start=f"2025-0{k + 1}-01T08:00", noise=(k * 4.0, 0.0),
                     speed=3.0 - 0.4 * k, file=f"g{k}") for k in range(2)]
    store = R.RouteStore(tmp_path / "api")
    bld = R.Builder(store)
    monkeypatch.setattr(API, "STORE", store)
    monkeypatch.setattr(API, "BUILDER", bld)
    monkeypatch.setattr(API, "_ensure_fresh", lambda: None)
    monkeypatch.setattr(API, "_file_to_idx", lambda: {})
    API._CACHE.update(mtime=None, view=None)
    wl, rd = _builder(tmp_path, acts)
    bld.build(wl, rd)
    lst = API.list_routes()
    climb = next(r for r in lst["rows"] if r["kind"] == "climb")
    assert climb["n_efforts"] == 2
    d = API.detail(climb["id"])
    a, b = d["efforts"]
    assert a["rank"] == 1 and b["rank"] == 2 and b["delta_best_s"] == b["elapsed_s"] - a["elapsed_s"]
    c = API.compare(climb["id"], a["id"], b["id"])
    assert c["gap_s"][0] == pytest.approx(0, abs=1)
    assert c["gap_s"][-1] == pytest.approx(b["elapsed_s"] - a["elapsed_s"], abs=2)
    assert all(x is not None for x in c["a"]["t_s"])
    API.rename(climb["id"], API.RenameBody(name="測試坡"))
    assert API.detail(climb["id"])["name"] == "測試坡"


def test_api_route_rows_thumbnails_directions_and_aliases(tmp_path, monkeypatch):
    from backend.api import routes as API
    acts = [activity(LOOP, start="2025-01-01T08:00", file="L0"),
            activity(LOOP, start="2025-02-01T08:00", noise=(5.0, 0.0), file="L1", speed=3.3),
            activity(list(reversed(LOOP)), start="2025-03-01T08:00", file="L2"),
            activity([(0, 0), (1500, 0), (1500, 1500)], start="2025-04-01T08:00", file="L3")]
    store = R.RouteStore(tmp_path / "api2")
    bld = R.Builder(store)
    monkeypatch.setattr(API, "STORE", store)
    monkeypatch.setattr(API, "BUILDER", bld)
    monkeypatch.setattr(API, "_ensure_fresh", lambda: None)
    monkeypatch.setattr(API, "_file_to_idx", lambda: {"L3": 7})
    monkeypatch.setattr(API, "_phase_labels", lambda days: [None] * len(days))
    API._CACHE.update(mtime=None, view=None)
    wl, rd = _builder(tmp_path, acts)
    idx = bld.build(wl, rd)
    rid = idx["routes"][0]["id"]
    # an alias written into the index (a route merged away by an earlier build)
    idx["routes"][0]["aliases"] = ["rGONE"]
    store.save_index(idx)
    API._CACHE.update(mtime=None, view=None)
    lst = API.list_routes(kind="route")
    assert [r["id"] for r in lst["rows"]] == [rid]
    row = lst["rows"][0]
    assert row["thumb"]["d"].startswith("M") and row["n_reversed"] == 1 and row["n_partials"] == 1
    assert lst["counts"]["route"] == 1
    d = API.detail("rGONE")                                # the old id opens the route
    assert d["id"] == rid and [p["workout"] for p in d["partials"]] == [7]
    by_file = {e["file"]: e for e in d["efforts"]}
    # ranks per direction: the reversed run is first of its own direction
    assert by_file["L2"]["dir"] == "reversed" and by_file["L2"]["rank"] == 1
    assert sorted(by_file[f]["rank"] for f in ("L0", "L1")) == [1, 2]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ex:
        API.compare(rid, by_file["L0"]["id"], by_file["L2"]["id"])
    assert ex.value.status_code == 400
    c = API.compare(rid, by_file["L0"]["id"], by_file["L1"]["id"])
    assert c["gap_s"][-1] == pytest.approx(by_file["L1"]["moving_s"] - by_file["L0"]["moving_s"], abs=3)


def test_names_survive_and_are_keyed_by_id(tmp_path):
    st = R.RouteStore(tmp_path)
    st.set_name("s123", "  七星山主峰  ".strip())
    assert st.names() == {"s123": "七星山主峰"}
    st.set_name("s123", "")
    assert st.names() == {}


def test_auto_name_uses_a_peak_within_1km():
    pk = R.peaks()[0]
    lat = [pk["lat"] - 0.02, pk["lat"] + 0.003]
    lon = [pk["lon"], pk["lon"]]
    assert R.auto_name("climb", lat, lon, 3200, 420).startswith(pk["name"])
    assert R.auto_name("climb", [0.5, 0.6], [0.5, 0.6], 3200, 420) == "爬坡 3.2 km ↑420 m"


# --- per-effort peaks and historical weather, checked independently -----------
# backend/scripts/verify_route_weather_hr.py recomputes both with plain loops
# and a direct archive query; here it runs against the API with the network mocked.

from backend.engine import route_weather as RW
from backend.scripts import verify_route_weather_hr as V


def _with_power(ch, seed=1):
    rng = np.random.default_rng(seed)
    n = len(ch["t"])
    p = 220 + 60 * np.sin(np.arange(n) / 17.0) + rng.normal(0, 25, n)
    p[n // 3: n // 3 + 40] += 180                       # a 40 s surge
    pw = [float(x) for x in p]
    for k in range(n // 2, n // 2 + 8):                 # a power dropout
        pw[k] = None
    return {**ch, "power": pw}


def test_max_hr_and_30s_power_equal_brute_force_over_raw_samples():
    meta, ch = activity(OUT_BACK, elev_of=hill_elev, stop=(1500, 90))
    ch = _with_power(ch)
    ch["hr"] = [h + (25 if k == 777 else 0) for k, h in enumerate(ch["hr"])]    # a one-sample spike
    raw = R.extract_track(meta, ch["t"], ch["lat"], ch["lon"], ch["dist_km"], ch["elev"], ch["hr"],
                          ch["power"], ch["speed"], None, lthr=165.0)
    tr = R.Track(raw)
    rng = np.random.default_rng(7)
    ranges = [(c[0], c[1]) for c in raw["climbs"] + raw["descents"]] + [(0, len(tr) - 1)]
    ranges += [tuple(sorted(rng.choice(len(tr), 2, replace=False))) for _ in range(40)]
    for i0, i1 in ranges:
        a, b = raw["idx"][i0], raw["idx"][i1]
        pk = R.effort_peaks(tr, int(i0), int(i1))
        mh = V.max_hr_plain(ch["hr"], a, b)
        assert pk["max_hr"] == (None if mh is None else round(mh)), (i0, i1)
        mp = V.max_p30_plain(ch["t"], ch["power"], a, b)
        assert pk["max_p30"] == (None if mp is None else round(mp)), (i0, i1)
    # the spike is inside the whole-activity range
    assert R.effort_peaks(tr, 0, len(tr) - 1)["max_hr"] == round(max(ch["hr"][raw["idx"][0] + 1:]))
    # no power channel: no 30 s peak
    tr2 = R.Track(track_of(meta, {k: v for k, v in ch.items() if k != "power"}))
    assert R.effort_peaks(tr2, 0, len(tr2) - 1)["max_p30"] is None


class FakeArchive:
    """Open-Meteo archive stand-in: a day of hourly values that depend on the
    queried point and hour, so a wrong cell, day or hour shows up as a wrong value."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, params, timeout):
        self.calls.append(dict(params))
        if self.fail:
            raise RuntimeError("offline")
        assert url == RW.WX.OM_ARCHIVE and params["start_date"] == params["end_date"]
        day = params["start_date"]
        lats = [float(x) for x in str(params["latitude"]).split(",")]
        lons = [float(x) for x in str(params["longitude"]).split(",")]
        els = [float(x) for x in str(params["elevation"]).split(",")] if "elevation" in params else [40.0] * len(lats)
        assert len(lats) == len(lons) == len(els)
        hours = [f"{day}T{h:02d}:00" for h in range(24)]
        d = int(day[-2:])
        out = [{"latitude": la, "longitude": lo, "elevation": el, "timezone": "Asia/Taipei",
                "hourly": {"time": hours,
                           "temperature_2m": [round(18 + 0.7 * h + d * 0.1 + (la - 24) * 30 + (lo - 121) * 20
                                                    - 0.0065 * el, 1) for h in range(24)],
                           "relative_humidity_2m": [60 + h for h in range(24)],
                           "dew_point_2m": [12.0] * 24}} for la, lo, el in zip(lats, lons, els)]
        return out if len(out) > 1 else out[0]          # the real API: an object for one point


def _api_on(tmp_path, monkeypatch, acts, get):
    from backend.api import routes as API
    store = R.RouteStore(tmp_path / "wx")
    bld = R.Builder(store, weather_get=get)
    monkeypatch.setattr(API, "STORE", store)
    monkeypatch.setattr(API, "BUILDER", bld)
    monkeypatch.setattr(API, "_ensure_fresh", lambda: None)
    monkeypatch.setattr(API, "_file_to_idx", lambda: {})
    monkeypatch.setattr(API, "_phase_labels", lambda days: [None] * len(days))
    API._CACHE.update(mtime=None, view=None)
    wl, rd = _builder(tmp_path, acts)
    return API, bld, wl, rd


def test_weather_and_max_hr_match_an_independent_recomputation(tmp_path, monkeypatch):
    # three climbs of one hill: two on the same morning (one batch), one late
    # in the evening that runs past midnight (two days)
    acts = [activity(OUT_BACK, elev_of=hill_elev, start=s, noise=(k * 3.0, 0.0), file=f"w{k}",
                     speed=3.0 - 0.3 * k)
            for k, s in enumerate(["2025-07-01T06:10", "2025-07-01T09:40", "2025-07-02T23:45"])]
    fake = FakeArchive()
    API, bld, wl, rd = _api_on(tmp_path, monkeypatch, acts, fake)
    idx = bld.build(wl, rd)
    # one call per (day, cell): 07-01, 07-02 and 07-03 (the late climb crosses midnight),
    # each carrying every effort point of that day (climb, descent, route)
    assert sorted(c["start_date"] for c in fake.calls) == ["2025-07-01", "2025-07-02", "2025-07-03"]
    assert all(len(c["latitude"].split(",")) >= 2 for c in fake.calls) and all("elevation" in c for c in fake.calls)
    assert idx["weather"]["calls"] == 3 and idx["weather"]["failed"] == 0
    assert idx["weather"]["attribution"] == RW.WX.ATTRIBUTION
    climb = next(r for r in API.list_routes()["rows"] if r["kind"] == "climb")
    d = API.detail(climb["id"])
    assert d["weather"]["calls"] == 3 and len(d["efforts"]) == 3
    raw = {m["file"]: c for m, c in acts}
    for e in d["efforts"]:
        ch = raw[e["file"]]
        a, b = e["raw_i0"], e["raw_i1"]
        # max HR straight from the raw samples
        assert e["max_hr"] == round(V.max_hr_plain(ch["hr"], a, b))
        # a direct archive query for this effort, formulas written out again
        w = e["wx"]
        direct = FakeArchive()
        mine = V.weather_plain(direct, w["lat"], w["lon"], e["start"], ch["t"][a], ch["t"][b], w["elev_m"])
        assert V.compare_weather(w, mine) == [], e["start"]
        # the point is the effort's own: its raw samples' mean to 0.01° / 10 m
        la = np.mean(ch["lat"][a:b + 1]); lo = np.mean(ch["lon"][a:b + 1]); el = np.mean(ch["elev"][a:b + 1])
        assert abs(la - w["lat"]) <= 0.0051 and abs(lo - w["lon"]) <= 0.0051 and abs(el - w["elev_m"]) <= 6
        assert w["archive_elev_m"] == w["elev_m"]      # asked for at the effort's height
    # the late activity's descent / route runs past midnight and has weather from both days
    past = [e for s in idx["segments"] + idx["routes"] for e in s["efforts"]
            if e["wx"] and e["wx"]["window"][0][:10] == "2025-07-02" and e["wx"]["window"][1][:10] == "2025-07-03"]
    assert past
    for e in past:
        ch = raw[e["file"]]
        mine = V.weather_plain(FakeArchive(), e["wx"]["lat"], e["wx"]["lon"], e["start"], ch["t"][e["raw_i0"]],
                               ch["t"][e["raw_i1"]], e["wx"]["elev_m"])
        assert V.compare_weather(e["wx"], mine) == []
    # a full rebuild comes entirely from the disk cache
    fake.calls.clear()
    again = R.Builder(R.RouteStore(tmp_path / "wx"), weather_get=fake).build(wl, rd, full=True)
    assert fake.calls == [] and again["weather"]["cache_hits"] == 3 and again["weather"]["calls"] == 0


def test_offline_weather_never_blocks_the_build_and_is_retried(tmp_path, monkeypatch):
    acts = [activity(OUT_BACK, elev_of=hill_elev, start=f"2025-0{k + 1}-0{k + 1}T08:00", noise=(k * 3.0, 0.0),
                     file=f"o{k}") for k in range(8)]
    off = FakeArchive(fail=True)
    API, bld, wl, rd = _api_on(tmp_path, monkeypatch, acts, off)
    idx = bld.build(wl, rd)
    assert idx["segments"] and all(e["wx"] is None for s in idx["segments"] for e in s["efforts"])
    w = idx["weather"]
    # the breaker stops after MAX_CONSEC_FAIL failures (in batches of WORKERS)
    assert len(off.calls) < 8 and w["failed"] == len(off.calls) and w["skipped"] == 8 - len(off.calls)
    assert all(e["max_hr"] is not None for s in idx["segments"] for e in s["efforts"])
    assert R.RouteStore(tmp_path / "wx").load_index()["weather"]["failed"] > 0   # saved
    # back online, no file changed: the next build fills the gaps
    on = FakeArchive()
    bld.weather_get = on
    idx2 = bld.build(wl, rd)
    assert len(on.calls) == 8 and idx2["weather"]["failed"] == 0
    assert all(e["wx"] is not None for s in idx2["segments"] for e in s["efforts"])


def _detect_summary_for_seed_check():
    """Several activities sharing flat stretches in different combinations."""
    acts = [activity([(0, 0), (0, 1000), (1500, 1000), (1500, 3000)], start="2025-01-01T08:00", file="h0"),
            activity([(-800, 1000), (1500, 1000), (1500, -500)], start="2025-02-01T08:00", file="h1"),
            activity([(0, 200), (0, 1000), (1500, 1000), (1500, 2200)], start="2025-03-01T08:00", file="h2"),
            activity([(-400, 1000), (1500, 1000), (1500, 2600)], start="2025-04-01T08:00", file="h3"),
            activity(OUT_BACK, elev_of=hill_elev, start="2025-05-01T08:00", file="h4"),
            activity(OUT_BACK, elev_of=hill_elev, start="2025-06-01T08:00", file="h5", noise=(4.0, 0.0))]
    return _summary(R.detect(_tracks(*acts)))


def test_detection_does_not_depend_on_the_hash_seed():
    import subprocess
    code = ("import sys, json; sys.path.insert(0, 'backend/tests'); import test_route_progress as T; "
            "print(json.dumps(T._detect_summary_for_seed_check()))")
    root = os.path.join(os.path.dirname(__file__), "../..")
    outs = []
    for seed in ("1", "2", "3"):
        r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}, timeout=120)
        assert r.returncode == 0, r.stderr[-800:]
        outs.append(r.stdout.strip().splitlines()[-1])
    assert outs[0] == outs[1] == outs[2] and json.loads(outs[0])


def test_version_bump_keeps_old_ids_through_carry_over(tmp_path):
    acts = [activity(OUT_BACK, elev_of=hill_elev, start=f"2025-0{k + 1}-01T08:00", noise=(k * 3.0, 0.0),
                     file=f"v{k}") for k in range(2)]
    store = R.RouteStore(tmp_path / "vb")
    wl, rd = _builder(tmp_path, acts)
    idx = R.Builder(store).build(wl, rd)
    climb = next(s for s in idx["segments"] if s["kind"] == "climb")
    # an index written by the previous version, where this climb had another id
    old = json.loads(store.index_path.read_text("utf-8"))
    old["version"] = R.ALGO_VERSION - 1
    for s in old["segments"]:
        if s["id"] == climb["id"]:
            s["id"] = "sOLDID"
    store.index_path.write_text(json.dumps(old), "utf-8")
    store.save_manifest({})                     # every file re-parsed, as after the bump
    assert store.load_index() is None
    new = R.Builder(store).build(wl, rd)
    assert "sOLDID" in {s["id"] for s in new["segments"]}


def test_hadley_sum_and_hot_flag():
    js = {"elevation": 10.0, "hourly": {"time": ["2025-07-01T13:00", "2025-07-01T14:00"],
                                        "temperature_2m": [33.0, 33.0], "relative_humidity_2m": [70, 70]}}
    a = dt.datetime(2025, 7, 1, 13, 10)
    c = RW.conditions([js], a, a + dt.timedelta(minutes=20))
    from backend.engine.racepower.env import dew_point, heat_penalty_pct
    dw = dew_point(33.0, 70)
    assert c["hadley"] == round(33 * 1.8 + 32 + dw["dew_f"]) and c["hot"] is True
    assert c["heat_pct"] == round(heat_penalty_pct(33.0, 70), 1)
    mild = {**js, "hourly": {**js["hourly"], "temperature_2m": [24.0, 24.0], "relative_humidity_2m": [80, 80]}}
    assert RW.conditions([mild], a, a)["hot"] is False                       # 75 °F + ~67 °F < 150
    # no archive rows near the effort: no weather
    assert RW.conditions([js], a + dt.timedelta(hours=5), a + dt.timedelta(hours=6)) is None


def test_a_new_point_in_a_cached_day_costs_one_call_for_that_point_only(tmp_path):
    fake = FakeArchive()
    cell, day = (24.125, 121.125), dt.date(2025, 7, 1)
    p1, p2 = RW.point_of(24.01, 121.0, 200.0), RW.point_of(24.02, 121.01, 350.0)
    f = RW.Fetcher(tmp_path, fake)
    got = f.fetch_all({(cell, day): {p1}})
    assert f.stats["calls"] == 1 and (cell, day, p1) in got
    f2 = RW.Fetcher(tmp_path, fake)
    got2 = f2.fetch_all({(cell, day): {p1, p2}})
    assert f2.stats["calls"] == 1 and fake.calls[-1]["latitude"] == "24.02" and fake.calls[-1]["elevation"] == "350"
    assert got2[(cell, day, p1)] == got[(cell, day, p1)]
    f3 = RW.Fetcher(tmp_path, fake)
    f3.fetch_all({(cell, day): {p1, p2}})
    assert f3.stats["calls"] == 0 and f3.stats["cache_hits"] == 1
