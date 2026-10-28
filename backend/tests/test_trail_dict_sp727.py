"""
SP-727 — the race calculator reads the trail dictionary (SP-709) instead of an Overpass server, and the
segment split uses its surface / difficulty classes:

1. the server keeps a local index of the published dictionary (engine/trail_dict_store.py): refresh
   from latest.json → manifest → bundle (sha256 checked), a failure keeps the index in use;
2. the course's ground (racepower/surface.py) comes from that index, with each way's speed class
   (road / forest_road / steps / trail / trail_t3);
3. per speed class × grade band a multiplier on top of the slope cost (trailhr.fit_kind_mult), fitted
   from the athlete's own runs (within-activity), shrunk to 1; only the split changes, the whole-race
   time stays (slope_times); leave-one-run-out (kind_backtest) decides whether it is on (kind_flag).

Nothing is downloaded: a fake `get` serves the published files from a temp folder.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import random

import pytest
from pytest import approx

from backend.engine import trail_dict_store as DS
from backend.engine import trailclass as TC
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import surface as SF
from backend.engine.racepower import trailhr as TH

LAT0, LON0 = 24.0, 121.0
M_PER_DEG = 111195.0
DIV = 153.0


def encode(coords):
    out, px, py = [], 0, 0
    for lon, lat in coords:
        x, y = round(lon * DS.COORD_SCALE), round(lat * DS.COORD_SCALE)
        out += (x - px, y - py)
        px, py = x, y
    return out


def record(wid, a_km, b_km, tags, east_m=8.0, town=True):
    """A dictionary record alongside the synthetic north-going course from a_km to b_km."""
    lon = LON0 + east_m / (M_PER_DEG * math.cos(math.radians(LAT0)))
    pts = [(lon, LAT0 + (a_km + (b_km - a_km) * i / 10) * 1000 / M_PER_DEG) for i in range(11)]
    cls = TC.classify(tags, town=town)
    return {"id": wid, "line": cls["line"], "surface": cls["surface"], "risk": cls["risk"],
            "tags": TC.kept_tags(tags), "c": encode(pts)}


# 0–3 km a T3 dirt trail, 3–4 km stone steps, 4–6 km an untagged footway in town, 6–8 km a road,
# 8–9 km a forest road, 9–10 km nothing near
RECORDS = [record(1, 0, 3, {"highway": "path", "sac_scale": "demanding_mountain_hiking"}),
           record(2, 3, 4, {"highway": "steps"}),
           record(3, 4, 6, {"highway": "footway"}),
           record(4, 6, 8, {"highway": "residential"}),
           record(5, 8, 9, {"highway": "track"}),
           record(6, 0, 10, {"highway": "path", "surface": "asphalt"}, east_m=400.0)]     # too far


def synth_gpx(km=10.0, step_m=20.0) -> bytes:
    n = int(round(km * 1000 / step_m))
    tp = "".join(f'<trkpt lat="{LAT0 + i * step_m / M_PER_DEG:.7f}" lon="{LON0}"><ele>100</ele></trkpt>'
                 for i in range(n + 1))
    return (f'<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<trk><name>synthetic</name><trkseg>{tp}</trkseg></trk></gpx>').encode("utf-8")


def bundle_bytes(records) -> bytes:
    return gzip.compress("".join(json.dumps(r) + "\n" for r in records).encode("utf-8"), mtime=0)


class Publisher:
    """The R2 bucket as a dict of path → bytes; `get` is trail_dict_store.http_get's stand-in."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []

    def publish(self, version, records, rules=TC.RULES_VERSION, sha=None):
        b = bundle_bytes(records)
        base = f"v/{version}/"
        man = {"version": version, "rules_version": rules, "built_at": "2026-10-20T02:56:00Z",
               "source": {"md5": "b3d6f9ae"},
               "bundle": {"path": "trail-dict.ndjson.gz", "bytes": len(b),
                          "sha256": sha or hashlib.sha256(b).hexdigest()}}
        self.files[base + "trail-dict.ndjson.gz"] = b
        self.files[base + "manifest.json"] = json.dumps(man).encode()
        self.files["latest.json"] = json.dumps({"version": version, "base": base,
                                                "manifest": base + "manifest.json", "rules_version": rules}).encode()

    def get(self, url, dest=None, max_bytes=DS.MAX_JSON_BYTES, timeout=None):
        rel = url.split("https://dict.test/", 1)[1]
        self.calls.append(rel)
        if rel not in self.files:
            raise DS.TrailDictError("HTTP 404")
        data = self.files[rel]
        if dest is None:
            return data
        with open(dest, "wb") as f:
            f.write(data)
        return b""


@pytest.fixture
def dict_root(tmp_path, monkeypatch):
    """A trail dictionary on this server (the synthetic RECORDS), refreshed from a fake bucket."""
    root = tmp_path / "trail-dict"
    monkeypatch.setattr(DS, "_default_root", lambda: root)
    monkeypatch.setenv(DS.URL_ENV, "https://dict.test")
    pub = Publisher()
    pub.publish("20261020-0256-b3d6f9ae", RECORDS)
    assert DS.refresh(get=pub.get)["status"] == "updated"
    return root


def close(p, want):
    return set(p) == set(want) and all(abs(p[k] - want[k]) <= 1 for k in want)


# ---- 1. the server's copy of the dictionary ---------------------------------------------------

def test_speed_class_from_the_dictionary_classes():
    k = lambda tags, town=None: DS.speed_kind(TC.line_class(tags), TC.surface_class(tags, town), tags)  # noqa: E731
    assert k({"highway": "path"}) == "trail"
    assert k({"highway": "path", "sac_scale": "mountain_hiking"}) == "trail"
    assert k({"highway": "path", "sac_scale": "demanding_mountain_hiking"}) == "trail_t3"
    assert k({"highway": "path", "trail_visibility": "bad"}) == "trail_t3"             # 艱難路線
    assert k({"highway": "path", "surface": "asphalt"}) == "road"                     # a paved park path
    assert k({"highway": "steps"}) == "steps"
    assert k({"highway": "track"}) == "forest_road" and k({"highway": "track", "surface": "concrete"}) == "road"
    assert k({"highway": "residential"}) == "road" and k({"highway": "cycleway"}) == "road"
    # a closed way runs like what it was (北插天山 CT103: access=no, a T3 path)
    assert k({"highway": "path", "access": "no", "sac_scale": "demanding_mountain_hiking"}) == "trail_t3"
    assert k({"highway": "track", "access": "no"}) == "forest_road"
    assert k({"highway": "corridor"}) is None


def test_refresh_downloads_checks_and_indexes_then_only_looks(tmp_path):
    pub = Publisher()
    pub.publish("20261020-0256-b3d6f9ae", RECORDS)
    root = tmp_path / "td"
    r = DS.refresh(get=pub.get, root=root, url="https://dict.test/")
    assert r == {"status": "updated", "version": "20261020-0256-b3d6f9ae", "ways": len(RECORDS)}
    st = DS.status(root)
    assert st["version"] == "20261020-0256-b3d6f9ae" and st["rules_version"] == TC.RULES_VERSION
    assert st["format"] == DS.FORMAT and st["ways"] == len(RECORDS)
    assert sorted(p.name for p in root.iterdir()) == ["20261020-0256-b3d6f9ae.sqlite", "current.json"]
    # the same version again: latest.json only, nothing downloaded
    pub.calls.clear()
    assert DS.refresh(get=pub.get, root=root, url="https://dict.test/")["status"] == "current"
    assert pub.calls == ["latest.json"]
    # a new version replaces the old index
    pub.publish("20261102-0300-c0ffee00", RECORDS[:3])
    assert DS.refresh(get=pub.get, root=root, url="https://dict.test/")["status"] == "updated"
    assert DS.current(root).version == "20261102-0300-c0ffee00" and DS.current(root).ways == 3
    assert not (root / "20261020-0256-b3d6f9ae.sqlite").exists()


def test_a_bad_download_keeps_the_index_in_use(tmp_path):
    pub = Publisher()
    pub.publish("20261020-0256-b3d6f9ae", RECORDS)
    root = tmp_path / "td"
    DS.refresh(get=pub.get, root=root, url="https://dict.test/")
    pub.publish("20261102-0300-c0ffee00", RECORDS, sha="0" * 64)            # the bundle does not match
    r = DS.refresh(get=pub.get, root=root, url="https://dict.test/")
    assert r["status"] == "failed" and "sha256" in r["error"]
    assert DS.current(root).version == "20261020-0256-b3d6f9ae"
    assert sorted(p.name for p in root.iterdir()) == ["20261020-0256-b3d6f9ae.sqlite", "current.json"]
    # a pointer out of the bucket is refused before anything is fetched
    pub.files["latest.json"] = json.dumps({"version": "x1", "base": "../../etc/"}).encode()
    assert DS.refresh(get=pub.get, root=root, url="https://dict.test/")["status"] == "failed"
    assert DS.current(root).version == "20261020-0256-b3d6f9ae"
    # no URL: nothing to do
    assert DS.refresh(get=pub.get, root=root, url=None)["status"] == "off"


def test_a_look_is_due_hourly_without_an_index_and_daily_with_one(tmp_path, monkeypatch):
    root = tmp_path / "td"
    monkeypatch.setattr(DS, "_default_root", lambda: root)
    assert not DS.due(now=1e9)                                                 # no URL: never
    monkeypatch.setenv(DS.URL_ENV, "https://dict.test")
    assert DS.due(now=1e9)
    pub = Publisher()                                                          # nothing published yet
    assert DS.refresh(get=pub.get, now=lambda: 1e9)["status"] == "failed"
    assert not DS.due(now=1e9 + 1800) and DS.due(now=1e9 + 3600)
    pub.publish("20261020-0256-b3d6f9ae", RECORDS)
    assert DS.refresh(get=pub.get, now=lambda: 2e9)["status"] == "updated"
    assert not DS.due(now=2e9 + 3600 * 23) and DS.due(now=2e9 + DS.CHECK_EVERY_S)


def test_ways_near_takes_the_cells_around_the_points(dict_root):
    idx = DS.current()
    lat = [LAT0 + k * 1000 / M_PER_DEG for k in (1.0, 7.0)]
    ways = idx.ways_near(lat, [LON0, LON0], 60.0)
    assert {w["id"] for w in ways} >= {1, 4}
    w1 = next(w for w in ways if w["id"] == 1)
    assert w1["class"] == "dirt" and w1["kind"] == "trail_t3" and w1["sac"] == "demanding_mountain_hiking"
    assert w1["geometry"][0]["lat"] == approx(LAT0, abs=2e-5)
    assert idx.ways_near([LAT0 + 2.0], [LON0 + 2.0], 60.0) == []              # far from every way


# ---- 2. the course's ground from the dictionary ------------------------------------------------

def test_the_course_ground_and_speed_classes_come_from_the_dictionary(dict_root):
    prof = SF.analyse(GPX.parse(synth_gpx(), "t.gpx"))
    assert prof["v"] == SF.VERSION and prof["src"] == "dict" and prof["dict"] == "20261020-0256-b3d6f9ae"
    assert prof["rules"] == TC.RULES_VERSION
    assert [s["c"] for s in prof["segs"]] == ["dirt", "steps", "unknown", "paved", "gravel", "unknown"]
    assert [s["c"] for s in prof["kinds"]] == ["trail_t3", "steps", "trail", "road", "forest_road", "none"]
    e = SF.effective(prof)
    assert close(e["pct"], {"dirt": 30, "made": 40, "unsure": 30})
    sh = SF.kind_shares(prof["kinds"], 2.5, 3.5)
    assert sh == approx({"trail_t3": 0.5, "steps": 0.5}, abs=0.03)
    assert SF.kind_shares(prof["kinds"], 9.2, 9.8) == {}                     # nothing near: no class


def test_no_dictionary_means_no_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(DS, "_default_root", lambda: tmp_path / "none")
    with pytest.raises(SF.SurfaceError) as ei:
        SF.analyse(GPX.parse(synth_gpx(), "t.gpx"))
    assert ei.value.code == "off"
    assert "TRC_TRAIL_DICT_URL" in SF.unknown_message("off")
    monkeypatch.setenv(DS.URL_ENV, "https://dict.test")
    monkeypatch.setattr(DS, "building", lambda: True)
    with pytest.raises(SF.SurfaceError) as ei:
        SF.analyse(GPX.parse(synth_gpx(), "t.gpx"))
    assert ei.value.code == "building"


# ---- 3. the surface multipliers on the split ---------------------------------------------------

TRUE_K = {"road": (0.80, 0.95, 1.0), "steps": (1.5, 1.6, 1.3), "trail": (1.10, 1.02, 1.0),
          "forest_road": (0.9, 0.97, 1.0), "trail_t3": (1.2, 1.05, 1.0)}
SLOPE_FIT = {"applied": True, "cost": list(TH.SLOPE["default"])}


def _krun(rnd, n=14, effect=True):
    segs, ts, shares = [], [], []
    pace = rnd.uniform(400, 700)
    for _ in range(n):
        g = rnd.uniform(-0.25, 0.25)
        d = rnd.uniform(400, 1500)
        k = rnd.choice(list(TRUE_K))
        sh = {k: 1.0} if rnd.random() > 0.1 else {}
        m = TH.kind_mult({"applied": True, "mult": {kk: list(v) for kk, v in TRUE_K.items()}}, sh, g) if effect else 1.0
        segs.append({"grade": g, "dist_m": d, "gain_m": max(0.0, g) * d})
        ts.append((d / 1000 + max(0.0, g) * d / DIV) * pace * TH.slope_cost(SLOPE_FIT, g) * m
                  * rnd.lognormvariate(0, 0.06))
        shares.append(sh)
    return TH.kind_row(segs, ts, shares)


def test_kind_multiplier_is_linear_between_the_bands_and_shares_mix():
    fit = {"applied": True, "mult": {"road": [0.8, 1.0, 1.2], "steps": [1.5, 1.5, 1.5]}}
    assert TH.kind_mult(fit, {"road": 1.0}, -0.05) == approx(0.9)
    assert TH.kind_mult(fit, {"road": 1.0}, -0.3) == approx(0.8)
    assert TH.kind_mult(fit, {"road": 0.5, "steps": 0.25}, 0.0) == approx(0.5 + 0.375 + 0.25)
    assert TH.kind_mult(fit, {}, 0.0) == 1.0 and TH.kind_mult(None, {"road": 1.0}, 0.0) == 1.0
    assert TH.kind_mult({**fit, "applied": False}, {"road": 1.0}, -0.1) == 1.0


def test_fit_follows_the_athletes_runs_and_few_runs_change_nothing():
    rnd = random.Random(3)
    rows = [_krun(rnd) for _ in range(40)]
    fit = TH.fit_kind_mult(rows, SLOPE_FIT, DIV)
    assert fit["applied"] and fit["n_runs"] == 40
    m = fit["mult"]
    # relative to each run: roads faster than trails on the descents, steps slowest everywhere
    assert m["road"][0] < m["trail"][0] < m["trail_t3"][0]
    assert m["steps"][1] > 1.2 and m["steps"][1] > m["road"][1]
    assert all(0.5 < x < 2.0 for v in m.values() for x in v)
    few = TH.fit_kind_mult(rows[:2], SLOPE_FIT, DIV)
    assert not few["applied"] and all(x == 1.0 for v in few["mult"].values() for x in v)
    # shrinkage: a class seen for a few minutes stays near 1
    one = TH.kind_row([{"grade": 0.0, "dist_m": 100, "gain_m": 0}, {"grade": 0.0, "dist_m": 5000, "gain_m": 0}],
                      [120.0, 1500.0], [{"steps": 1.0}, {"trail": 1.0}])
    fit3 = TH.fit_kind_mult([one] * 3, SLOPE_FIT, DIV)
    assert 1.0 < fit3["mult"]["steps"][1] < fit3["own"]["steps"][1]


def test_the_split_keeps_the_total_and_moves_time_by_surface():
    segs = [{"grade": -0.1, "dist_m": 1000, "gain_m": 0, "kinds": {"road": 1.0}},
            {"grade": -0.1, "dist_m": 1000, "gain_m": 0, "kinds": {"trail": 1.0}}]
    ts = [500.0, 500.0]
    kfit = {"applied": True, "mult": {"road": [0.8, 1.0, 1.0], "trail": [1.2, 1.0, 1.0]}}
    plain = TH.slope_times(segs, ts, SLOPE_FIT, DIV)
    withk = TH.slope_times(segs, ts, SLOPE_FIT, DIV, kinds=kfit)
    assert sum(withk) == approx(sum(ts)) and plain[0] == approx(plain[1])
    assert withk[1] / withk[0] == approx(1.2 / 0.8)
    assert TH.slope_times(segs, ts, SLOPE_FIT, DIV, kinds={**kfit, "applied": False}) == approx(plain)


def test_leave_one_run_out_smaller_split_error_same_total():
    rnd = random.Random(11)
    rows = {i: _krun(rnd) for i in range(25)}
    bt = TH.kind_backtest(rows, divisor=DIV)
    assert bt["n"] == 25 and bt["total_same"]
    assert bt["cum_kind"] < bt["cum_plain"] and bt["better"]
    # no surface effect in the runs: the multipliers stay near 1 and win nothing clear
    rnd = random.Random(12)
    flat = {i: _krun(rnd, effect=False) for i in range(25)}
    bt0 = TH.kind_backtest(flat, divisor=DIV)
    assert bt0["total_same"] and abs(bt0["cum_kind"] - bt0["cum_plain"]) < 0.004


def test_the_back_test_switch(tmp_path):
    from backend.engine.racepower import backtest as BT
    p = tmp_path / "bt.json"
    assert not BT.kind_flag(p)
    p.write_text(json.dumps({"trail_hr": {"surface_kind": {"better": True, "n": BT.KIND_MIN_N - 1}}}))
    assert not BT.kind_flag(p)
    p.write_text(json.dumps({"trail_hr": {"surface_kind": {"better": True, "n": BT.KIND_MIN_N}}}))
    assert BT.kind_flag(p)
    p.write_text(json.dumps({"trail_hr": {"surface_kind": {"better": False, "n": 40}}}))
    assert not BT.kind_flag(p)


def test_planner_applies_the_surface_only_when_switched_on():
    from backend.engine.racepower import course as CO
    from backend.engine.racepower import grade_model as GM
    from backend.engine.racepower import planner as PL
    from backend.tests.test_racepower_v2 import RE0, fake_v1, synthetic_track
    tr = synthetic_track({"len": 20000, "z": lambda x: 200 + 150 * math.sin(x / 2500.0)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 20.0, c["totals"]["gain_m"])
    kfit = {"applied": True, "n_runs": 12, "mult": {"road": [0.75, 0.95, 1.0], "trail": [1.25, 1.05, 1.0]}}
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9,
         "slope_cost": SLOPE_FIT, "surface_kind": kfit}
    # the course: up 0–3.5 km, down 4.5–11.1 km, up 12.1–19 km — the first descent on a road, the rest trail
    kinds = [{"a": 0.0, "b": 4.5, "c": "trail"}, {"a": 4.5, "b": 11.1, "c": "road"}, {"a": 11.1, "b": 20.0, "c": "trail"}]

    def plan(**o):
        return PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(RE0), effort_validated=False,
                           opts={"mode": "auto", **o}, validated={"trail": True}, trail_hr=m)
    off = plan()
    off2 = plan(kinds=kinds, kind_apply=False)
    on = plan(kinds=kinds, kind_apply=True)
    assert [s["t"] for s in off2["segments"]] == approx([s["t"] for s in off["segments"]])
    assert off["summary"]["kind_split"] is None
    assert on["summary"]["time_s"] == approx(off["summary"]["time_s"], abs=1.0)
    assert on["summary"]["kind_split"]["applied"] and on["summary"]["kind_split"]["n_runs"] == 12
    t_on, t_off = on["segments"], off["segments"]
    road = [i for i, s in enumerate(t_on) if s["start_km"] >= 4.4 and s["end_km"] <= 11.2]
    trail = [i for i in range(len(t_on)) if i not in road]
    assert road and t_on[road[0]]["grade"] < -0.03
    assert sum(t_on[i]["t"] for i in road) < sum(t_off[i]["t"] for i in road)       # the road descent faster
    assert sum(t_on[i]["t"] for i in trail) > sum(t_off[i]["t"] for i in trail)     # the trail gets that time
    assert any("路面" in w and "步道字典" in w for w in on["warnings"])
