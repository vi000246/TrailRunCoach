"""Trail dictionary formats and publishing (SP-709): tiles, bundle, manifest, retention, the R2 order.

Synthetic ways and a fake S3 client; nothing is downloaded.
"""
import datetime as dt
import gzip
import json
import os
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "trail-dict")
sys.path.insert(0, os.path.abspath(TOOLS))

import publish  # noqa: E402
import trail_dict as td  # noqa: E402

from backend.engine import trailclass  # noqa: E402


# ---------------------------------------------------------------------------- tile maths

def test_lonlat_to_tile_known_values():
    assert td.lonlat_to_tile(0.0, 0.0, 1) == (1, 1)
    assert td.lonlat_to_tile(-180.0, 89.0, 13) == (0, 0)      # clamped to the Mercator edge
    # Taipei 101 at z13 (OSM slippy tiles)
    assert td.lonlat_to_tile(121.5645, 25.0340, 13) == (6862, 3507)


def test_tile_bounds_contain_the_point():
    x, y = td.lonlat_to_tile(121.45, 24.79, 13)
    w, s, e, n = td.tile_bounds(x, y, 13)
    assert w <= 121.45 < e and s <= 24.79 < n
    assert 0.04 < e - w < 0.05                          # about 4.5 km wide


def test_split_by_tile_keeps_every_segment_and_overlaps_at_the_border():
    x, y = td.lonlat_to_tile(121.45, 24.79, 13)
    w, s, e, n = td.tile_bounds(x, y, 13)
    mid = (s + n) / 2
    # a line crossing from this tile into the east neighbour
    coords = [(e - 0.010, mid), (e - 0.004, mid), (e + 0.004, mid), (e + 0.010, mid)]
    pieces = td.split_by_tile(coords, 13)
    assert set(pieces) == {(x, y), (x + 1, y)}
    assert pieces[(x, y)] == [coords[0:3]]              # segment 2 crosses: in both tiles
    assert pieces[(x + 1, y)] == [coords[1:4]]


def test_split_by_tile_two_separate_runs_in_one_tile():
    x, y = td.lonlat_to_tile(121.45, 24.79, 13)
    w, s, e, n = td.tile_bounds(x, y, 13)
    mid = (s + n) / 2
    out_e = e + 0.02
    coords = [(e - 0.01, mid), (out_e, mid), (out_e + 0.001, mid), (e - 0.01, mid + 0.001)]
    pieces = td.split_by_tile(coords, 13)[(x, y)]
    assert pieces == [coords[0:2], coords[2:4]]


def test_coords_round_trip():
    coords = [(121.451234, 24.791234), (121.45201, 24.79005), (121.4501, 24.7911)]
    c = td.encode_coords(coords)
    assert all(isinstance(v, int) for v in c)
    back = td.decode_coords(c)
    for (a, b), (p, q) in zip(coords, back):
        assert abs(a - p) < 1e-5 and abs(b - q) < 1e-5


# ---------------------------------------------------------------------------- writer + manifest

def _ways():
    ct103 = {"highway": "path", "access": "no", "sac_scale": "demanding_mountain_hiking", "name": "CT103"}
    road = {"highway": "residential", "name": "x路"}
    trail = {"highway": "footway", "surface": "unpaved"}
    return [
        (1, ct103, [(121.445, 24.785), (121.452, 24.795)], [{"id": 9, "name": "北插天山步道"}]),
        (2, road, [(121.40, 24.70), (121.55, 24.70)], None),        # long: several tiles
        (3, trail, [(121.30, 24.80), (121.3001, 24.8001)], None),
        (4, trail, [(121.30, 24.80)], None),                         # one node: dropped
    ]


def _build(tmp_path):
    out = str(tmp_path / "out")
    w = td.DictWriter(out, 13)
    for wid, tags, coords, routes in _ways():
        w.add(wid, trailclass.classify(tags), trailclass.kept_tags(tags), coords, routes)
    tiles = w.finish()
    bundle_path = os.path.join(out, td.BUNDLE_NAME)
    bundle = {"path": td.BUNDLE_NAME, "bytes": os.path.getsize(bundle_path),
              "sha256": td.sha256_of(bundle_path), "ways": w.ways}
    built = dt.datetime(2026, 10, 19, 6, 50, tzinfo=dt.timezone.utc)
    m = td.make_manifest(version=td.version_id(built, "a78bebd26ad96a96a51dd6d2b0223193"),
                         built_at="2026-10-19T06:50:00Z",
                         source={"md5": "a78bebd26ad96a96a51dd6d2b0223193", "url": "http://x"},
                         rules_version=trailclass.RULES_VERSION, zoom=13, tiles=tiles, bundle=bundle,
                         stats={"ways": w.ways})
    with open(os.path.join(out, td.MANIFEST_NAME), "w", encoding="utf-8") as f:
        f.write(td.dumps(m))
    return out, w, tiles, m


def test_writer_outputs_bundle_tiles_and_manifest(tmp_path):
    out, w, tiles, m = _build(tmp_path)
    assert w.ways == 3
    assert not os.path.exists(os.path.join(out, "_buckets"))

    with gzip.open(os.path.join(out, td.BUNDLE_NAME), "rt", encoding="utf-8") as f:
        recs = [json.loads(line) for line in f]
    assert [r["id"] for r in recs] == [1, 2, 3]
    ct = recs[0]
    assert ct["line"] == "closed" and ct["risk"] == ["closed"] and ct["surface"] == "dirt"
    assert ct["routes"] == [{"id": 9, "name": "北插天山步道"}]
    assert ct["tags"]["name"] == "CT103"
    assert recs[2]["surface"] == "dirt"                       # unpaved footway -> dirt
    assert "routes" not in recs[1]

    # the long road is split over several tiles, every tile file is there and readable
    assert len(tiles) == len(m["tiles"]) == m["tile_count"]
    road_tiles = 0
    for t in tiles:
        tile = td.read_tile(os.path.join(out, *td.tile_relpath(13, t["x"], t["y"]).split("/")))
        assert (tile["x"], tile["y"], tile["z"]) == (t["x"], t["y"], 13)
        assert len(tile["ways"]) == t["ways"]
        road_tiles += any(r["id"] == 2 for r in tile["ways"])
        for r in tile["ways"]:
            pts = td.decode_coords(r["c"])
            assert len(pts) >= 2
    assert road_tiles >= 3


def test_manifest_fields(tmp_path):
    _, _, _, m = _build(tmp_path)
    assert m["version"] == "20261019-0650-a78bebd2"
    assert m["source"]["md5"] == "a78bebd26ad96a96a51dd6d2b0223193"
    assert m["built_at"] == "2026-10-19T06:50:00Z"
    assert m["rules_version"] == trailclass.RULES_VERSION
    assert m["zoom"] == 13 and m["coord_scale"] == td.COORD_SCALE
    assert "OpenStreetMap" in m["license"]
    files = td.expected_files(m)
    assert files[-1] == td.MANIFEST_NAME and td.BUNDLE_NAME in files
    assert len(files) == m["tile_count"] + 2


def test_versions_to_delete_keeps_two_newest_and_latest():
    vs = ["20260801-0300-aaaa", "20260901-0300-bbbb", "20261001-0300-cccc", "20261019-0650-dddd"]
    assert td.versions_to_delete(vs, latest="20261019-0650-dddd") == vs[:2]
    # a newer half-uploaded version must not push out the one latest points to
    assert td.versions_to_delete(vs, latest="20261001-0300-cccc") == vs[:2]
    assert td.versions_to_delete(vs, latest="20260901-0300-bbbb") == vs[:1]
    assert td.versions_to_delete(vs[:2], latest=None) == []


# ---------------------------------------------------------------------------- publishing (fake S3)

class FakeS3:
    def __init__(self, existing=None, drop=None):
        self.objects = dict(existing or {})         # key -> bytes
        self.drop = drop or (lambda key: False)     # simulate an upload that silently goes missing
        self.log = []

    def upload_file(self, local, bucket, key, ExtraArgs=None):
        self.log.append(("put", key))
        if self.drop(key):
            return
        with open(local, "rb") as f:
            self.objects[key] = f.read()

    def put_object(self, Bucket, Key, Body, **kw):
        self.log.append(("put", Key))
        self.objects[Key] = Body

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)

    def get_paginator(self, name):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix, Delimiter=None):
                keys = [k for k in s3.objects if k.startswith(Prefix)]
                if Delimiter:
                    cps = sorted({Prefix + k[len(Prefix):].split(Delimiter)[0] + Delimiter
                                  for k in keys if Delimiter in k[len(Prefix):]})
                    yield {"CommonPrefixes": [{"Prefix": p} for p in cps]}
                else:
                    yield {"Contents": [{"Key": k, "Size": len(s3.objects[k])} for k in sorted(keys)]}

        return P()


def _old(version):
    return {f"v/{version}/manifest.json": b"{}", f"v/{version}/tiles/13/1/1.json.gz": b"x"}


def test_publish_uploads_then_switches_latest_then_prunes(tmp_path):
    out, _, _, m = _build(tmp_path)
    s3 = FakeS3({**_old("20260801-0300-aaaa"), **_old("20260901-0300-bbbb"),
                 "latest.json": b'{"version": "20260901-0300-bbbb"}'})
    res = publish.publish(s3, "bkt", "", out, log=lambda *_: None)
    v = m["version"]
    assert res["version"] == v and res["deleted"] == ["20260801-0300-aaaa"]
    assert json.loads(s3.objects["latest.json"])["version"] == v
    assert json.loads(s3.objects["latest.json"])["manifest"] == f"v/{v}/manifest.json"
    puts = [k for op, k in s3.log if op == "put"]
    assert puts[-1] == "latest.json" and puts[-2] == f"v/{v}/manifest.json"    # manifest, then pointer
    assert f"v/{v}/{td.BUNDLE_NAME}" in s3.objects
    assert not any(k.startswith("v/20260801") for k in s3.objects)
    assert any(k.startswith("v/20260901") for k in s3.objects)                  # two versions remain


def test_incomplete_upload_leaves_latest_on_the_old_version(tmp_path):
    out, _, _, _ = _build(tmp_path)
    old_latest = b'{"version": "20260901-0300-bbbb"}'
    s3 = FakeS3({**_old("20260901-0300-bbbb"), "latest.json": old_latest},
                drop=lambda key: key.endswith(td.BUNDLE_NAME))
    with pytest.raises(publish.PublishError, match="latest NOT switched"):
        publish.publish(s3, "bkt", "", out, log=lambda *_: None)
    assert s3.objects["latest.json"] == old_latest
    assert any(k.startswith("v/20260901") for k in s3.objects)                  # nothing pruned


def test_incomplete_build_is_refused_before_any_upload(tmp_path):
    out, _, tiles, _ = _build(tmp_path)
    t = tiles[0]
    os.remove(os.path.join(out, *td.tile_relpath(13, t["x"], t["y"]).split("/")))
    s3 = FakeS3()
    with pytest.raises(publish.PublishError, match="incomplete"):
        publish.publish(s3, "bkt", "", out, log=lambda *_: None)
    assert s3.log == []


def test_missing_secrets_stop_before_anything(tmp_path, monkeypatch, capsys):
    for k in publish.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("R2_BUCKET", "trail-dict")
    assert publish.missing_env() == ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    assert publish.main(["--out", str(tmp_path)]) == 4
    err = capsys.readouterr().err
    assert "R2_ACCOUNT_ID" in err and "Nothing was uploaded" in err
