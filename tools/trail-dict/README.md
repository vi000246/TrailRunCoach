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

### The classes (`trailclass.py`, rules v1)

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
10. otherwise unknown (a path with no surface and no sac_scale: could be a paved park path)

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
