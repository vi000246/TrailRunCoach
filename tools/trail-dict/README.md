# Trail dictionary (SP-709)

One dictionary of Taiwan's OSM paths and roads, rebuilt every month, shared by TrailRunCoach (the NAS
downloads the bundle) and inReachLiveTrack (the Worker fetches the tiles a route crosses). Both sites
read the classes stored here instead of querying Overpass and classifying on their own.

- Source: the OSM extract Rudy's map (alpha-rudy/taiwan-topo) is built from,
  `http://osm.kcwu.csie.org/download/tw-extract/recent/taiwan-latest.o5m.zst` (rebuilt daily, a
  `.md5` next to it). Raw OSM data -- not Rudy's `.map` / `.img` map files.
- Rules: [`backend/engine/trailclass.py`](../../backend/engine/trailclass.py), the one copy. The build
  imports it, and the app can import the same module. `RULES_VERSION` goes into every manifest.
- Workflow: [`.github/workflows/trail-dict.yml`](../../.github/workflows/trail-dict.yml), on the 2nd
  of each month at 02:00 Taipei time, and by hand (Actions > trail-dict > Run workflow).

## Build it yourself

```sh
python -m venv .venv-trail && .venv-trail/bin/pip install -r tools/trail-dict/requirements.txt
curl -O http://osm.kcwu.csie.org/download/tw-extract/recent/taiwan-latest.o5m.zst
curl -O http://osm.kcwu.csie.org/download/tw-extract/recent/taiwan-latest.o5m.zst.md5
.venv-trail/bin/python -I tools/trail-dict/build.py --source taiwan-latest.o5m.zst \
    --md5-file taiwan-latest.o5m.zst.md5 --out out/
```

pyosmium (libosmium) reads the `.o5m`; the `.zst` is unpacked first (to `--work`, default next to
`--out`, removed afterwards). Works on Linux (Actions) and Windows. A mismatching md5 stops the build.

After a rules change (a `RULES_VERSION` bump) an earlier version can be rebuilt as it is, without the
extract and without pyosmium: the kept tags and coordinates are all the rules read.

```sh
python -I tools/trail-dict/build.py --from <folder with manifest.json + trail-dict.ndjson.gz> --out out/
```

The new version has the same source (md5, date) and zoom, a new build time, the new `rules_version`,
and `rebuilt_from: <the old version>` in its manifest. A bundle built before rules v2 does not keep
`footway=*`, so its sidewalks are then decided by place like any other footway (they are almost all
in town, so they stay unknown).

Trial build, 2026-10-19 extract (433.7 MB packed, 1356 MB unpacked), Windows laptop:

| | |
|---|---|
| ways | 822,558 (13,218 in hiking routes) |
| tiles (z13) | 2,026 files, 28.4 MB (median 8.6 KB, p95 43 KB, largest 281 KB) |
| bundle | 27.5 MB |
| one version | about 56 MB |
| build time | 131-167 s over two runs (unpack 3-5 s, read + classify 116-151 s, tiles 15 s); same tiles byte for byte |
| peak memory | about 620 MB |

## Output

```
manifest.json                version, source (url, md5, last_modified, bytes), built_at, rules_version,
                             zoom, coord_scale, license, bundle {path, bytes, sha256}, tile list, stats
trail-dict.ndjson.gz         every way once, one JSON object per line
tiles/13/{x}/{y}.json.gz     {"format", "z", "x", "y", "bbox", "coord_scale", "ways": [...]}
```

A way (bundle line, or tile piece):

```json
{"id": 1436930472, "line": "closed", "surface": "dirt", "risk": ["closed"],
 "routes": [{"id": 1, "name": "...", "network": "...", "ref": "..."}],
 "tags": {"highway": "path", "sac_scale": "demanding_mountain_hiking", "access": "no", "name": "CT103"},
 "c": [12145012, 2479034, 12, -8, ...]}
```

- `c`: lon, lat as integers x `coord_scale` (1e5, about 1.1 m), the first pair absolute, the rest deltas.
- Tiles are the standard slippy-map tiles (as OSM's), z13 is about 4.5 km wide in Taiwan. A long way
  is cut at vertices: a tile keeps the run of segments that touch it, so pieces of neighbouring tiles
  overlap by a segment. The same way id can appear in several tiles.
- `routes` only when the way is in a `route=hiking` / `route=foot` relation (Rudy's thick red line).
- `tags`: the raw tags kept (`KEPT_TAGS`), so changed rules can be re-run without the source.

### The classes (`trailclass.py`, rules v2)

`line` -- the line Rudy's map draws (MOI_OSM.xml / process_osm.py, SP-701):

| line | when |
|---|---|
| closed | `access=no`, or an abandoned / disused way (abandoned: 推估, same grey) |
| steps | `highway=steps` |
| hard | path / footway / bridleway with `trail_visibility` bad / horrible / no, or `sac_scale` T5-T6; `highway=via_ferrata` |
| trail | other path / footway / bridleway (the thin red dash; T1-T4 alike) |
| forest_road | `highway=track` |
| road | motorway ... service, links, living_street, cycleway, pedestrian |
| other | anything else (corridor, ...) |

Hiking-route membership (`routes`) is separate: Rudy draws it over whatever the way is, and it says
nothing about difficulty.

`surface` -- paved / gravel / dirt / steps / unknown, first match wins:

1. a laid `surface` (asphalt, concrete, paving_stones, sett, wood, ...) -> paved
2. `highway=steps` -> steps
3. `surface=unpaved` on path / footway / bridleway -> **dirt** (on a track or a road it stays gravel)
4. gravel, fine_gravel, compacted, pebblestone, unpaved -> gravel
5. dirt, ground, soil, earth, mud, sand, grass, rock, bare_rock, woodchips -> dirt
6. `sac_scale` T2 or above -> dirt
7. a road highway -> paved
8. `smoothness` very_bad or worse -> dirt
9. `highway=track` -> gravel
10. a path / footway / bridleway with none of the above (not a sidewalk or crossing, `footway=sidewalk|crossing|traffic_island`):
    **dirt out of town, unknown in town** (rules v2, SP-735; see below)
11. otherwise unknown

#### In town or not (rules v2, SP-735)

About 126,000 of Taiwan's paths carry no surface, no `sac_scale` T2+ and no rough `smoothness`. In
the mountains they are nearly all dirt trails; in town they can as well be a paved park path, so they
are decided by place:

- **Town roads per km2.** Every `residential`, `living_street`, `service`, `unclassified`, `tertiary`,
  `secondary`, `primary` (and links), `road`, `pedestrian` and `cycleway` way is counted into a grid of
  0.003 deg cells (about 330 m x 300 m). Motorways and trunk roads (they cross the mountains too) and
  tracks (forest roads, farm tracks) are not counted.
- **A point is in town** when the town roads in its 3 x 3 cells (about 1 km x 0.9 km) come to
  **8 km per km2** or more; **a path is in town** when at least half of its length is. Both numbers
  are 推估 (estimates), set from the spot check below: the riverside parks and town edges sit at
  16-26 km/km2, farmland villages 10-27, the mountain paths 0-4, the 柴山 / 四獸山 park interiors 0-8.
- Only the dictionary's own ways are read (no `landuse`, no elevation model), so a rules change
  rebuilds from the previous version's bundle (`build.py --from`, below) without downloading the extract.
- The classes are stored per way; readers (the NAS app once SP-710 reads the dictionary, the
  inReachLiveTrack Worker) take them as they are and see the rules in `rules_version` (manifest and `latest.json`). The build prints, and the
  manifest keeps (`stats.place_rule`), how many untagged paths went to dirt and how many stayed unknown:
  the dirt ones are the ways rules v1 left unknown.

Spot check, live OSM data of 2026-10-19 (Overpass), untagged paths in each area -> dirt:

| area | untagged paths | -> dirt | stay unknown |
|---|---|---|---|
| 北插天山 / 東眼山 (24.80-24.85 N, 121.40-121.45 E) | 68 | 68 | 0 |
| 柴山 (22.63-22.67 N, 120.25-120.28 E) | 478 | 434 | 44 (the town edge) |
| 象山 / 四獸山 (25.020-25.035 N, 121.570-121.585 E) | 273 | 85 (the hill interior) | 188 (park edge, next to 信義) |
| 大佳 / 美堤河濱 (25.066-25.08 N, 121.52-121.545 E) | 245 | 0 | 245 |
| 雲林 farmland (23.70-23.72 N, 120.40-120.43 E) | 34 | 0 | 34 |
| 烏來 (24.85-24.87 N, 121.54-121.56 E) | 17 | 12 | 5 (the village) |

Sidewalks and crossings are not counted above (they never change). The nationwide count comes with
the first rules-v2 build (`stats.place_rule.dirt`).

`risk` -- shown as a trail-condition warning, not turned into a speed factor (SP-716):
`closed` (`access=no`), `abandoned`, `sac_t4plus` (`sac_scale` T4-T6), `poor_visibility`
(`trail_visibility` bad / horrible / no).

Which ways go in: `highway=*` (or `abandoned:highway` / `disused:highway`), except plans and
non-ways (proposed, construction, platform, ...) and areas (`area=yes`).

## Publishing to R2

`publish.py` uploads `v/<version>/...`, lists it back and compares every name and size, and only
then writes `latest.json`; after that it deletes all but the newest two versions (never the one
`latest.json` points to). A missing secret, a failed download, a md5 mismatch, a failed build or an
incomplete upload fails the job: `latest.json` keeps pointing to the previous version and GitHub
e-mails the failure (for a scheduled run, to whoever last edited the cron line in the workflow).

```
latest.json                  {"version", "base": "v/<version>/", "manifest", "built_at", "rules_version", "source_md5"}
v/<version>/manifest.json
v/<version>/trail-dict.ndjson.gz
v/<version>/tiles/13/{x}/{y}.json.gz
```

`<version>` = build time (UTC) + the source md5's first 8 characters, e.g. `20261019-0653-a78bebd2`.
Readers start from `latest.json`; everything under `v/` never changes once written (cached for a
year), `latest.json` and manifests for 5 minutes. Files are stored gzip-compressed
(`application/gzip`); a reader un-gzips them itself.

### What you set up once

1. **Bucket**: Cloudflare dashboard > R2 > Create bucket, e.g. `trail-dict` (a bucket of its own;
   to share one, set the repository variable `TRAIL_DICT_PREFIX`, e.g. `trail-dict/`).
2. **Public read**: bucket > Settings > Custom Domains > connect a domain, e.g.
   `trail-dict.yichlin.com` (or turn on the r2.dev URL for testing). Readers fetch
   `https://<domain>/latest.json`. The inReachLiveTrack Worker can bind the bucket instead.
3. **API token**: R2 > Manage R2 API Tokens > Create API token, permission **Object Read & Write**,
   **applied to the `trail-dict` bucket only**, no expiry or a long one. Note the Access Key ID and
   Secret Access Key, and the account ID (on the R2 overview page).
4. **GitHub secrets** in TrailRunCoach-private (Settings > Secrets and variables > Actions):
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
5. Run it once by hand (Actions > trail-dict > Run workflow) and open `https://<domain>/latest.json`.

Until the secrets are there, a scheduled run fails at "Check the R2 settings" and says which are
missing. A manual run with "Upload to R2" off builds only and keeps the bundle and manifest as a
workflow artifact for 7 days. The workflow only runs in the private repo (the public mirror carries
the file but skips the job).

### R2 use per month (2026-10 prices: 10 GB-month, 1M Class A, 10M Class B free; egress free)

- storage: two versions, about 112 MB (about 170 MB for the minutes a third one is uploading)
- Class A (writes, lists): about 2,040 per build (2,026 tiles + bundle + manifest + pointer + a few lists / deletes)
- Class B (reads): the NAS 1 bundle a month; the Worker about 5-30 tiles per route analysed,
  e.g. 1,000 routes -> about 20,000

All well inside the free tier, which is shared by the whole Cloudflare account (inReachLiveTrack's
`inreach-livetrack` bucket counts too).

## License

The dictionary is a derived database of OpenStreetMap data, under the
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/). Anyone showing it must credit
"© OpenStreetMap contributors" (https://www.openstreetmap.org/copyright); the manifest carries the
notice. Publishing the dictionary itself (the public R2 domain) means offering it under the ODbL.

## Tests

`backend/tests/test_trailclass.py` (the rules, synthetic tags) and `backend/tests/test_trail_dict.py`
(tiles, bundle, manifest, retention, the upload order against a fake S3). Nothing is downloaded.
