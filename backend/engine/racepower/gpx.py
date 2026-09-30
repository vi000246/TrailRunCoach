"""
GPX / FIT course parsing — docs/research/racepower-v2.md §6.5 step 1, §10.3.

Standard library only (xml.etree.ElementTree): GPX 1.0 and 1.1, trk/trkseg/
trkpt and rte/rtept, several trksegs concatenated, waypoint names kept (camp /
hut names mark multi-day splits). Files with a DOCTYPE or ENTITY declaration
are refused before parsing (entity expansion), and the size is capped.

Also `write_gpx`, the inverse, used to export one of the athlete's own
activities as a GPX course (tests, page checks).
"""
from __future__ import annotations

import datetime as dt
import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

MAX_BYTES = 20 * 1024 * 1024


class GpxError(ValueError):
    """The file cannot be used as a course (message is shown to the user)."""


@dataclass
class Track:
    lat: list[float]
    lon: list[float]
    ele: list[Optional[float]]
    time: Optional[list[Optional[float]]] = None     # s from the first point
    wpts: list[dict] = field(default_factory=list)   # {name, lat, lon}
    name: str = ""

    def __len__(self) -> int:
        return len(self.lat)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_time(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def parse(data: bytes, filename: str = "") -> Track:
    """Bytes of a .gpx (or .fit course) file → Track. Raises GpxError."""
    if len(data) > MAX_BYTES:
        raise GpxError(f"檔案超過 {MAX_BYTES // (1024 * 1024)} MB")
    if filename.lower().endswith(".fit") or data[8:12] == b".FIT":
        return parse_fit_course(data)
    head = data[:4096].lower()
    if b"<!doctype" in data.lower() or b"<!entity" in head or b"<!entity" in data.lower():
        raise GpxError("GPX 含 DOCTYPE / ENTITY 宣告，基於安全不解析")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise GpxError(f"不是有效的 GPX：{e}") from None
    if _local(root.tag) != "gpx":
        raise GpxError("不是 GPX 檔（根元素不是 <gpx>）")
    lat, lon, ele, times = [], [], [], []
    name = ""
    wpts = []

    def take(pt):
        try:
            la, lo = float(pt.get("lat")), float(pt.get("lon"))
        except (TypeError, ValueError):
            return
        e = t = None
        for ch in pt:
            tag = _local(ch.tag)
            if tag == "ele" and ch.text and ch.text.strip():
                try:
                    e = float(ch.text)
                except ValueError:
                    e = None
            elif tag == "time":
                t = _parse_time(ch.text)
        lat.append(la)
        lon.append(lo)
        ele.append(e)
        times.append(t)

    for el in root:
        tag = _local(el.tag)
        if tag == "wpt":
            nm = next((c.text for c in el if _local(c.tag) == "name"), None)
            try:
                wpts.append({"name": (nm or "").strip(), "lat": float(el.get("lat")), "lon": float(el.get("lon"))})
            except (TypeError, ValueError):
                pass
        elif tag == "metadata" and not name:
            name = next(((c.text or "").strip() for c in el if _local(c.tag) == "name"), "")
    trk_found = False
    for trk in (el for el in root if _local(el.tag) == "trk"):
        trk_found = True
        if not name:
            name = next(((c.text or "").strip() for c in trk if _local(c.tag) == "name"), "")
        for seg in (c for c in trk if _local(c.tag) == "trkseg"):
            for pt in seg:
                if _local(pt.tag) == "trkpt":
                    take(pt)
    if not lat:
        for rte in (el for el in root if _local(el.tag) == "rte"):
            if not name:
                name = next(((c.text or "").strip() for c in rte if _local(c.tag) == "name"), "")
            for pt in rte:
                if _local(pt.tag) == "rtept":
                    take(pt)
    if len(lat) < 2:
        raise GpxError("GPX 裡沒有軌跡點（trkpt / rtept）" if not trk_found else "軌跡點少於 2 個")
    if all(e is None for e in ele):
        raise GpxError("GPX 沒有海拔（ele）：請改用手動輸入距離與爬升")
    t0 = next((t for t in times if t is not None), None)
    tt = None
    if t0 is not None:
        tt = [None if t is None else (t - t0).total_seconds() for t in times]
    return Track(lat, lon, ele, tt, wpts, name)


def parse_fit_course(data: bytes) -> Track:
    """A .fit course or activity: record messages with position (semicircles)
    and altitude, via fitdecode (already a dependency)."""
    try:
        import fitdecode
    except ImportError:                     # pragma: no cover
        raise GpxError("伺服器沒有 FIT 解析器") from None
    lat, lon, ele, times = [], [], [], []
    wpts = []
    t0 = None
    try:
        with fitdecode.FitReader(io.BytesIO(data)) as fr:
            for frame in fr:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "record":
                    def g(f):
                        return frame.get_value(f) if frame.has_field(f) else None
                    la, lo = g("position_lat"), g("position_long")
                    if la is None or lo is None:
                        continue
                    alt = g("enhanced_altitude")
                    if alt is None:
                        alt = g("altitude")
                    ts = g("timestamp")
                    lat.append(la * 180.0 / 2 ** 31)
                    lon.append(lo * 180.0 / 2 ** 31)
                    ele.append(None if alt is None else float(alt))
                    if isinstance(ts, dt.datetime):
                        t0 = t0 or ts
                        times.append((ts - t0).total_seconds())
                    else:
                        times.append(None)
                elif frame.name == "course_point":
                    la = frame.get_value("position_lat") if frame.has_field("position_lat") else None
                    lo = frame.get_value("position_long") if frame.has_field("position_long") else None
                    nm = frame.get_value("name") if frame.has_field("name") else ""
                    if la is not None and lo is not None:
                        wpts.append({"name": str(nm or ""), "lat": la * 180.0 / 2 ** 31,
                                     "lon": lo * 180.0 / 2 ** 31})
    except Exception as e:                  # noqa: BLE001
        raise GpxError(f"FIT 解析失敗：{str(e)[:80]}") from None
    if len(lat) < 2:
        raise GpxError("FIT 裡沒有帶座標的紀錄點")
    if all(e is None for e in ele):
        raise GpxError("FIT 沒有海拔：請改用手動輸入距離與爬升")
    return Track(lat, lon, ele, times if any(t is not None for t in times) else None, wpts)


def write_gpx(track: Track, name: str = "", start: Optional[dt.datetime] = None) -> str:
    """Track → GPX 1.1 text (trk/trkseg/trkpt with ele and, when known, time)."""
    root = ET.Element("gpx", {"version": "1.1", "creator": "wko5_coach",
                              "xmlns": "http://www.topografix.com/GPX/1/1"})
    for w in track.wpts:
        we = ET.SubElement(root, "wpt", {"lat": f"{w['lat']:.7f}", "lon": f"{w['lon']:.7f}"})
        ET.SubElement(we, "name").text = w.get("name", "")
    trk = ET.SubElement(root, "trk")
    ET.SubElement(trk, "name").text = name or track.name or "course"
    seg = ET.SubElement(trk, "trkseg")
    start = start or dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    for i in range(len(track.lat)):
        pt = ET.SubElement(seg, "trkpt", {"lat": f"{track.lat[i]:.7f}", "lon": f"{track.lon[i]:.7f}"})
        if track.ele[i] is not None:
            ET.SubElement(pt, "ele").text = f"{track.ele[i]:.1f}"
        if track.time and track.time[i] is not None:
            ET.SubElement(pt, "time").text = (start + dt.timedelta(seconds=track.time[i])).strftime("%Y-%m-%dT%H:%M:%SZ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
