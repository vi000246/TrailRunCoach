"""Build the shared trail dictionary from the OSM extract Rudy's map uses (SP-709).

    python -I tools/trail-dict/build.py --source taiwan-latest.o5m.zst --md5-file taiwan-latest.o5m.zst.md5 --out out/

Reads the o5m with pyosmium (libosmium; a .zst is first unpacked with `zstandard` next to --out),
in two passes: route=hiking relations (which ways belong to which named route), then every
highway=* way with its node locations. Each way gets its classes from backend/engine/trailclass.py,
the rules the app uses too. Writes the tiles, the bundle and manifest.json into --out (see
trail_dict.py for the formats) and prints the numbers.

Dependencies: `pip install -r tools/trail-dict/requirements.txt` (osmium, zstandard).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (HERE, ROOT):                      # run with -I: put only our own folders on the path
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.engine import trailclass  # noqa: E402
import trail_dict as td  # noqa: E402

ROUTE_TYPES = frozenset({"hiking", "foot"})


def md5_of(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_md5_file(path: str) -> str:
    with open(path, encoding="ascii") as f:
        return f.read().split()[0].strip().lower()


def unpack_zst(src: str, dst: str) -> None:
    import zstandard
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        zstandard.ZstdDecompressor().copy_stream(fi, fo, read_size=1 << 20, write_size=1 << 22)


def hiking_routes(path: str) -> dict[int, list[dict]]:
    """{way id: [{"id", "name", "network", "ref"}]} for members of route=hiking|foot relations."""
    import osmium
    member_of: dict[int, list[dict]] = {}
    for rel in osmium.FileProcessor(path, osmium.osm.RELATION):
        tags = rel.tags
        if tags.get("type") not in ("route", None) or tags.get("route") not in ROUTE_TYPES:
            continue
        info = {"id": rel.id}
        for k in ("name", "network", "ref"):
            v = tags.get(k)
            if v:
                info[k] = v
        for m in rel.members:
            if m.type == "w":
                member_of.setdefault(m.ref, []).append(info)
    return member_of


def build(src: str, out_dir: str, *, source_md5: str, source_meta: dict, zoom: int = td.DEFAULT_ZOOM,
          log=print) -> dict:
    import osmium
    t0 = time.monotonic()
    built_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    os.makedirs(out_dir, exist_ok=True)

    log("pass 1: route=hiking relations")
    routes = hiking_routes(src)
    log(f"  {len(routes)} ways in hiking routes ({time.monotonic() - t0:.0f} s)")

    log("pass 2: highway ways with node locations")
    writer = td.DictWriter(out_dir, zoom)
    proc = (osmium.FileProcessor(src, osmium.osm.NODE | osmium.osm.WAY)
            .with_locations("flex_mem")
            .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
            .with_filter(osmium.filter.KeyFilter("highway", "abandoned:highway", "disused:highway")))
    skipped = 0
    for way in proc:
        tags = {t.k: t.v for t in way.tags}
        if not trailclass.in_dictionary(tags):
            skipped += 1
            continue
        coords = [(n.lon, n.lat) for n in way.nodes if n.location.valid()]
        writer.add(way.id, trailclass.classify(tags), trailclass.kept_tags(tags), coords,
                   routes.get(way.id))
    log(f"  {writer.ways} ways, {writer.pieces} tile pieces, {skipped} skipped "
        f"({time.monotonic() - t0:.0f} s)")

    log("writing tiles")
    tiles = writer.finish()
    bundle_path = os.path.join(out_dir, td.BUNDLE_NAME)
    bundle = {"path": td.BUNDLE_NAME, "bytes": os.path.getsize(bundle_path),
              "sha256": td.sha256_of(bundle_path), "ways": writer.ways}
    stats = {"ways": writer.ways, "pieces": writer.pieces, "route_members": writer.route_members,
             "line": dict(writer.counts["line"]), "surface": dict(writer.counts["surface"]),
             "risk": dict(writer.counts["risk"]),
             "length_km": {k: round(v, 1) for k, v in writer.length_km.items()}}
    seconds = round(time.monotonic() - t0, 1)
    manifest = td.make_manifest(
        version=td.version_id(built_at, source_md5), built_at=built_at.isoformat().replace("+00:00", "Z"),
        source={**source_meta, "md5": source_md5}, rules_version=trailclass.RULES_VERSION, zoom=zoom,
        tiles=tiles, bundle=bundle, stats=stats, build_seconds=seconds)
    with open(os.path.join(out_dir, td.MANIFEST_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write(td.dumps(manifest))
    log(f"done in {seconds} s: version {manifest['version']}, {len(tiles)} tiles "
        f"({manifest['tile_bytes'] / 1e6:.1f} MB), bundle {bundle['bytes'] / 1e6:.1f} MB"
        + (f", peak memory {peak_mb():.0f} MB" if peak_mb() else ""))
    return manifest


def peak_mb() -> float | None:
    try:
        import resource                     # Linux / macOS only
    except ImportError:
        return None
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kb / (1 << 20) if sys.platform == "darwin" else kb / 1024


def summary_md(m: dict) -> str:
    st = m["stats"]
    return "\n".join([
        f"### trail-dict {m['version']}",
        "",
        f"- source: {m['source'].get('file', '')} md5 `{m['source']['md5']}`"
        f" (last modified {m['source'].get('last_modified', '?')})",
        f"- rules v{m['rules_version']}, tiles z{m['zoom']}",
        f"- {m['tile_count']} tiles, {m['tile_bytes'] / 1e6:.1f} MB; bundle {m['bundle']['bytes'] / 1e6:.1f} MB",
        f"- {st['ways']} ways ({st.get('route_members', 0)} in hiking routes); build {m['build_seconds']} s",
        f"- lines: {st.get('line')}",
        f"- surfaces: {st.get('surface')}",
        f"- risk flags: {st.get('risk')}",
        "",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", required=True, help="taiwan-latest.o5m.zst (or an unpacked .o5m / .pbf)")
    ap.add_argument("--md5-file", help="the .md5 published next to the source; the build stops on a mismatch")
    ap.add_argument("--out", required=True, help="output folder (must be empty or missing)")
    ap.add_argument("--work", help="where to unpack a .zst (default: next to --out)")
    ap.add_argument("--zoom", type=int, default=td.DEFAULT_ZOOM)
    ap.add_argument("--source-url", default="")
    ap.add_argument("--source-last-modified", default="")
    ap.add_argument("--summary", help="append a markdown summary here (the workflow passes $GITHUB_STEP_SUMMARY)")
    a = ap.parse_args(argv)

    if os.path.isdir(a.out) and os.listdir(a.out):
        print(f"--out {a.out} is not empty", file=sys.stderr)
        return 2
    md5 = md5_of(a.source)
    if a.md5_file:
        want = read_md5_file(a.md5_file)
        if want != md5:
            print(f"md5 mismatch: {a.source} is {md5}, the .md5 says {want}", file=sys.stderr)
            return 3
        print(f"md5 ok: {md5}")
    meta = {"file": os.path.basename(a.source), "bytes": os.path.getsize(a.source)}
    if a.source_url:
        meta["url"] = a.source_url
    if a.source_last_modified:
        meta["last_modified"] = a.source_last_modified

    src, unpacked = a.source, None
    if a.source.endswith(".zst"):
        work = a.work or os.path.dirname(os.path.abspath(a.out))
        os.makedirs(work, exist_ok=True)
        unpacked = os.path.join(work, os.path.basename(a.source)[:-4])
        t = time.monotonic()
        unpack_zst(a.source, unpacked)
        print(f"unpacked {os.path.getsize(unpacked) / 1e6:.0f} MB in {time.monotonic() - t:.0f} s")
        src = unpacked
    try:
        m = build(src, a.out, source_md5=md5, source_meta=meta, zoom=a.zoom)
        if a.summary:
            with open(a.summary, "a", encoding="utf-8") as f:
                f.write(summary_md(m))
    finally:
        if unpacked and os.path.exists(unpacked):
            os.remove(unpacked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
