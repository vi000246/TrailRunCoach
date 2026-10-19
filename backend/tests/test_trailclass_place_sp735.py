"""Rules v2 (SP-735): an untagged path out of town is dirt, in town it stays unknown; the place comes
from the dictionary's own roads (trailclass.TownGrid); a rules bump rebuilds from the previous
version's bundle without the OSM extract (tools/trail-dict/build.py --from).

Synthetic ways only; nothing is downloaded.
"""
import gzip
import json
import os
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "trail-dict")
sys.path.insert(0, os.path.abspath(TOOLS))

import build  # noqa: E402
import trail_dict as td  # noqa: E402

from backend.engine import trailclass as tc  # noqa: E402

PATH = {"highway": "path"}


def test_rules_version_is_2():
    assert tc.RULES_VERSION == 2


@pytest.mark.parametrize("tags", [{"highway": "path"}, {"highway": "footway"}, {"highway": "bridleway"},
                                  {"highway": "path", "sac_scale": "hiking"},
                                  {"abandoned:highway": "path"}])
def test_an_untagged_path_is_dirt_out_of_town_and_unknown_in_town(tags):
    assert tc.surface_class(tags, town=False) == "dirt"
    assert tc.surface_class(tags, town=True) == "unknown"
    assert tc.surface_class(tags) == "unknown"               # place not known: v1's answer
    assert tc.needs_place(tags)


@pytest.mark.parametrize("tags,want", [
    ({"highway": "path", "surface": "asphalt"}, "paved"),     # the tags decide first, the place never
    ({"highway": "path", "surface": "gravel"}, "gravel"),
    ({"highway": "path", "sac_scale": "mountain_hiking"}, "dirt"),
    ({"highway": "steps"}, "steps"),
    ({"highway": "track"}, "gravel"),
    ({"highway": "residential"}, "paved"),
    ({"highway": "corridor"}, "unknown"),                     # not a path: the place does not apply
    ({"highway": "footway", "footway": "sidewalk"}, "unknown"),   # follows a street: never a dirt trail
    ({"highway": "footway", "footway": "crossing"}, "unknown"),
])
def test_the_place_only_decides_what_the_tags_leave_open(tags, want):
    assert tc.surface_class(tags, town=False) == want
    assert tc.surface_class(tags, town=True) == want
    assert not tc.needs_place(tags)


def test_the_footway_tag_is_kept_so_a_rebuild_still_sees_a_sidewalk():
    assert tc.kept_tags({"highway": "footway", "footway": "sidewalk"})["footway"] == "sidewalk"


def test_classify_passes_the_place_on():
    assert tc.classify(PATH, town=False) == {"line": "trail", "surface": "dirt", "risk": []}
    assert tc.classify(PATH) == {"line": "trail", "surface": "unknown", "risk": []}


# ---------------------------------------------------------------------------- the town grid

LON, LAT = 121.50, 25.05


def _street_grid(grid, lon0, lat0, n=8, step=0.001):
    """n x n streets 110 m apart (about 18 km of street per km2): a town block."""
    for i in range(n):
        grid.add({"highway": "residential"}, [(lon0 + i * step, lat0), (lon0 + i * step, lat0 + (n - 1) * step)])
        grid.add({"highway": "residential"}, [(lon0, lat0 + i * step), (lon0 + (n - 1) * step, lat0 + i * step)])


def test_streets_make_a_town_and_mountains_do_not():
    g = tc.TownGrid()
    _street_grid(g, LON, LAT)
    assert g.density(LON + 0.0035, LAT + 0.0035) >= tc.TOWN_ROAD_KM_PER_KM2
    inside = [(LON + 0.002, LAT + 0.002), (LON + 0.004, LAT + 0.005)]
    assert g.is_town(inside)
    far = [(121.43, 24.82), (121.432, 24.823)]                 # 北插天山-like: no streets anywhere near
    assert g.density(*far[0]) == 0 and not g.is_town(far)


def test_motorways_and_tracks_do_not_make_a_town():
    g = tc.TownGrid()
    for k in range(10):                                        # a bundle of long motorways and tracks
        g.add({"highway": "motorway"}, [(121.0, 24.0 + k * 1e-4), (121.05, 24.0 + k * 1e-4)])
        g.add({"highway": "track"}, [(121.0, 24.001 + k * 1e-4), (121.05, 24.001 + k * 1e-4)])
        g.add({"highway": "trunk"}, [(121.0, 24.002 + k * 1e-4), (121.05, 24.002 + k * 1e-4)])
    assert g.density(121.02, 24.001) == 0


def test_a_long_road_segment_is_spread_over_the_cells_it_crosses():
    g = tc.TownGrid()
    g.add({"highway": "primary"}, [(121.0, 24.0), (121.1, 24.0)])   # one 10 km segment
    total = sum(g.km.values())
    assert total == pytest.approx(tc._seg_km((121.0, 24.0), (121.1, 24.0)))
    assert max(g.km.values()) < 0.5                            # not all in one cell


def test_a_way_is_in_town_by_the_share_of_its_length():
    g = tc.TownGrid()
    _street_grid(g, LON, LAT)
    # a path from inside the block 3 km out into empty land: mostly out of town
    out = [(LON + 0.003 + i * 0.003, LAT + 0.003) for i in range(11)]
    assert not g.is_town(out)
    assert g.is_town(out[:2])


# ---------------------------------------------------------------------------- the build

def _ways():
    """A town block of streets, a path inside it, a path far out in the hills, a sidewalk out there."""
    g_ways = []
    wid = 100
    for i in range(8):
        for a, b in (((LON + i * 0.001, LAT), (LON + i * 0.001, LAT + 0.007)),
                     ((LON, LAT + i * 0.001), (LON + 0.007, LAT + i * 0.001))):
            wid += 1
            g_ways.append((wid, {"highway": "residential"}, [a, b], None))
    town_path = (1, {"highway": "footway"}, [(LON + 0.0025, LAT + 0.0025), (LON + 0.004, LAT + 0.0045)], None)
    hill_path = (2, {"highway": "path", "name": "CT101"}, [(121.43, 24.82), (121.432, 24.823)],
                 [{"id": 9, "name": "北插天山步道"}])
    sidewalk = (3, {"highway": "footway", "footway": "sidewalk"}, [(121.43, 24.83), (121.431, 24.831)], None)
    tagged = (4, {"highway": "path", "surface": "ground"}, [(121.44, 24.82), (121.441, 24.821)], None)
    # the paths come before the streets on purpose: the place is only decided once every road is in
    return [town_path, hill_path, sidewalk, tagged] + g_ways


def _bundle(out):
    with gzip.open(os.path.join(out, td.BUNDLE_NAME), "rt", encoding="utf-8") as f:
        return {r["id"]: r for r in map(json.loads, f)}


def test_classify_ways_decides_untagged_paths_by_place(tmp_path):
    out = str(tmp_path / "out")
    w = td.DictWriter(out, 13)
    place = build.classify_ways(_ways(), w)
    w.finish()
    recs = _bundle(out)
    assert recs[1]["surface"] == "unknown"                     # in town
    assert recs[2]["surface"] == "dirt"                        # out of town
    assert recs[2]["routes"] == [{"id": 9, "name": "北插天山步道"}]
    assert recs[3]["surface"] == "unknown"                     # a sidewalk is never decided by place
    assert recs[4]["surface"] == "dirt"                        # tagged: the tags decide
    assert place == {"dirt": 1, "unknown": 1}
    assert w.ways == len(_ways())


def _v1_version(tmp_path):
    """A version as rules v1 built it (no place rule: untagged paths unknown)."""
    src = str(tmp_path / "v1")
    w = td.DictWriter(src, 13)
    for wid, tags, coords, routes in _ways():
        w.add(wid, {**tc.classify(tags), "surface": tc.surface_class(tags)}, tc.kept_tags(tags), coords, routes)
    tiles = w.finish()
    bpath = os.path.join(src, td.BUNDLE_NAME)
    m = td.make_manifest(version="20261019-0653-a78bebd2", built_at="2026-10-19T06:53:00Z",
                         source={"md5": "a78bebd26ad96a96a51dd6d2b0223193", "file": "taiwan-latest.o5m.zst",
                                 "last_modified": "Sun, 19 Oct 2026 01:00:00 GMT"},
                         rules_version=1, zoom=13, tiles=tiles,
                         bundle={"path": td.BUNDLE_NAME, "bytes": os.path.getsize(bpath),
                                 "sha256": td.sha256_of(bpath), "ways": w.ways},
                         stats={"ways": w.ways})
    with open(os.path.join(src, td.MANIFEST_NAME), "w", encoding="utf-8") as f:
        f.write(td.dumps(m))
    return src, m


def test_a_rules_bump_rebuilds_from_the_previous_bundle_without_the_extract(tmp_path, monkeypatch):
    src, old = _v1_version(tmp_path)
    assert _bundle(src)[2]["surface"] == "unknown"
    # no OSM reader, no download: the rebuild must not touch either
    monkeypatch.setitem(sys.modules, "osmium", None)
    monkeypatch.setattr(build, "unpack_zst", lambda *a: pytest.fail("unpacked an extract"))
    out = str(tmp_path / "v2")
    assert build.main(["--from", src, "--out", out, "--summary", str(tmp_path / "summary.md")]) == 0
    with open(os.path.join(out, td.MANIFEST_NAME), encoding="utf-8") as f:
        m = json.load(f)
    assert m["rules_version"] == 2 and old["rules_version"] == 1
    assert m["source"] == old["source"]                        # same OSM data, same md5
    assert m["version"].endswith("-a78bebd2") and m["version"] != old["version"]
    assert m["rebuilt_from"] == old["version"]
    assert m["stats"]["place_rule"] == {"dirt": 1, "unknown": 1}
    recs = _bundle(out)
    assert recs[2]["surface"] == "dirt" and recs[1]["surface"] == "unknown" and recs[3]["surface"] == "unknown"
    assert recs[2]["tags"] == {"highway": "path", "name": "CT101"}
    # every file the manifest lists is there (publish.py checks the same list after uploading)
    for rel in td.expected_files(m):
        assert os.path.isfile(os.path.join(out, *rel.split("/"))), rel
    assert "untagged paths decided by place" in (tmp_path / "summary.md").read_text("utf-8")


def test_the_cli_takes_a_source_or_an_earlier_version_not_both(tmp_path):
    with pytest.raises(SystemExit):
        build.main(["--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit):
        build.main(["--source", "x.o5m", "--from", "v/", "--out", str(tmp_path / "o")])
