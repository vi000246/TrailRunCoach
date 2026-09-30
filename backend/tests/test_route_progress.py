"""Repeated segments and routes on synthetic tracks: geometry, matching,
direction, clustering, metrics against climbs.py / wko5_hr, incremental builds."""
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


def test_route_needs_same_direction():
    loop = [(0, 0), (1500, 0), (1500, 1500), (0, 1500), (0, 0)]
    a = activity(loop, start="2025-01-01T08:00")
    b = activity(loop, start="2025-02-01T08:00", noise=(6.0, 3.0))
    c = activity(list(reversed(loop)), start="2025-03-01T08:00")
    idx = R.detect(_tracks(a, b, c))
    assert len(idx["routes"]) == 1
    assert idx["routes"][0]["members"] == [a[0]["file"], b[0]["file"]]
    assert idx["routes"][0]["id"] == R.seg_id("route", a[0]["file"], 0,
                                              R.Track(track_of(*a)).raw["idx"][-1])


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
    # done by an extra activity, the inner climb carries information: kept
    out = R._finish([long_, more], [], {})
    assert sorted(s["id"] for s in out["segments"]) == ["s1", "s3"]


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
