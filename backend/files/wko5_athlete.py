"""
Reader for WKO5 athlete files (`<Name>.wko5athlete`) — same tagged encoding
as `.wko5chart` / `.wko4` (see wko5chart_reader).

Top-level records (reverse-engineered 2026-09-29, WKO5 5.0.587):

    3001  athlete profile: 3002/3003 last/first name, 3033 birthday,
          3022 ctlconstant (42), 3021 atlconstant (7), 3229 default sport
    3404  dated settings history: 108 {101 name ("runftp", "runthr",
          "weight", ...), 102 {4403 channels: date, value, synceddate,
          syncedvalue, tpid}} — dates are day numbers since 1901-01-01
    3403  current PMC snapshot: 108 {101 "atl"|"ctl"|"tsb"|"ramp", 102 value}
    3201  workout index: 3202 per workout {3203 "UNIFORM:<year>/<file>.wko4",
          3213 title (WKO5 sets it to the workout type on import: "Trail Running"),
          3209 sport group ("Run"), 3206 description, 3207 notes, 3210 code,
          3010 FTP at the time, 3205 {103 day#, 104 ms-of-day} start,
          4202 ranges incl. "Entire Workout" with metric fields 42xx}

Text fields (DISASSEMBLY, WKO5.exe 5.0.587): the index-entry serializer 0x4fb517
writes 3213 <- +0x144, 3206 <- +0x15c, 3207 <- +0x174, 3209 <- +0x18c,
3210 <- +0x1a4; the summary-level variable resolver 0x4f93e6 reads `title` from
+0x144 (0x4f95da), `desc`/`description` from +0x15c (0x4f4990), `notes` from
+0x174 (0x4f49c0), `code` from +0x1a4. 3206 / 3207 are gzip-compressed by the
writer (0x4f8c70) when that is shorter, and gunzipped on read (0x4d7400).
"""
from __future__ import annotations

import datetime as dt
import gzip
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.files.wko5chart_reader import Record, decode_file
from backend.files.wko4_file import decode_channel

AUTO = 1.7976931348623157e308
EPOCH = dt.date(1901, 1, 1)


def day_to_date(n: float) -> dt.date:
    return EPOCH + dt.timedelta(days=int(n))


def _num(v):
    return None if v is None or v == AUTO else v


def text_field(value) -> str:
    """A WKO5 free-text field: str, or gzip bytes (magic 1f 8b), or absent -> ""."""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except (OSError, EOFError):
                return ""
        return raw.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return ""


# index-entry field ids of the workout text variables (see the module docstring)
F_TITLE, F_DESCRIPTION, F_NOTES, F_CODE = 3213, 3206, 3207, 3210


@dataclass
class WorkoutEntry:
    file: str                   # relative path, e.g. "2023/Athlete_2023_04_06_21_57.wko4"
    sport: Optional[str]        # 3213 = WKO5's title; the workout type unless renamed ("Trail Running")
    sport_group: Optional[str]  # "Run", "Walk", "Strength", ...
    start: Optional[dt.datetime]
    ftp: Optional[float]
    metrics: dict[int, float] = field(default_factory=dict)  # Entire Workout 42xx fields
    record: Optional[Record] = None

    def _text(self, fid: int) -> str:
        return text_field(self.record.get(fid)) if self.record is not None else ""

    @property
    def title(self) -> str:
        return self._text(F_TITLE)

    @property
    def description(self) -> str:
        return self._text(F_DESCRIPTION)

    @property
    def notes(self) -> str:
        return self._text(F_NOTES)

    @property
    def code(self) -> str:
        return self._text(F_CODE)


@dataclass
class Athlete:
    first_name: Optional[str]
    last_name: Optional[str]
    ctlconstant: float
    atlconstant: float
    settings: dict[str, list[tuple[dt.date, float]]]  # name -> sorted [(date, value)]
    pmc_snapshot: dict[str, float]
    workouts: list[WorkoutEntry]
    root: Record

    def setting_on(self, name: str, day: dt.date) -> Optional[float]:
        """Value of a dated setting (e.g. runftp) in effect on `day`."""
        hist = self.settings.get(name) or []
        val = None
        for d, v in hist:
            if d <= day:
                val = v
            else:
                break
        if val is None and hist:
            val = hist[0][1]  # before first entry, WKO5 uses the earliest value
        return val


def _settings(rec: Optional[Record]) -> dict[str, list[tuple[dt.date, float]]]:
    out: dict[str, list[tuple[dt.date, float]]] = {}
    if not isinstance(rec, Record):
        return out
    for kv in rec.all(108):
        name, body = kv.get(101), kv.get(102)
        if not isinstance(body, Record):
            continue
        chans = {}
        for c in body.all(4403):
            b = c.get(102)
            blk = b.get(121) if isinstance(b, Record) else None
            if isinstance(blk, bytes):
                chans[c.get(101)] = decode_channel(c.get(101), blk).values
        dates, values = chans.get("date") or [], chans.get("value") or []
        pairs = [(day_to_date(d), v) for d, v in zip(dates, values) if d is not None and v is not None]
        out[name] = sorted(pairs)
    return out


def _start(rec: Record) -> Optional[dt.datetime]:
    s = rec.get(3205)
    if not isinstance(s, Record) or s.get(103) is None:
        return None
    return dt.datetime.combine(day_to_date(s.get(103)), dt.time()) + \
        dt.timedelta(milliseconds=s.get(104) or 0)


def pd_snapshot(root: Record) -> dict[tuple[str, str], float]:
    """WKO5's current PD-model numbers, {(metric, sport group) -> value}:
    record 3405 holds 108 {102 value, 108 {171 "mftp"|"frc"|"pmax"|"tte"|
    "stamina"|"vo2maxkg", 172 "Run"|"All Run"|"Bike"|...}}; DBL_MAX = none."""
    out = {}
    snap = root.get(3405)
    if not isinstance(snap, Record):
        return out
    for kv in snap.all(108):
        tag = kv.get(108)
        v = _num(kv.get(102))
        if isinstance(tag, Record) and v is not None:
            out[(str(tag.get(171)), str(tag.get(172)))] = v
    return out


def read_athlete(path: str | Path) -> Athlete:
    root = decode_file(path)
    prof = root.get(3001) or Record()
    snap_rec = root.get(3403)
    snap = {kv.get(101): _num(kv.get(102)) for kv in snap_rec.all(108)} \
        if isinstance(snap_rec, Record) else {}
    workouts = []
    idx = root.get(3201)
    for w in idx.all(3202) if isinstance(idx, Record) else []:
        whole = next((r for r in w.all(4202) if r.get(4204) == "Entire Workout"), None)
        metrics = {f.fid: f.value for f in whole.fields if f.wire == 2 and f.value != AUTO} \
            if whole is not None else {}
        workouts.append(WorkoutEntry(
            file=(w.get(3203) or "").split(":", 1)[-1],
            sport=w.get(3213), sport_group=w.get(3209), start=_start(w),
            ftp=_num(w.get(3010)), metrics=metrics, record=w,
        ))
    workouts.sort(key=lambda x: x.start or dt.datetime.min)
    return Athlete(
        first_name=prof.get(3003), last_name=prof.get(3002),
        ctlconstant=_num(prof.get(3022)) or 42.0, atlconstant=_num(prof.get(3021)) or 7.0,
        settings=_settings(root.get(3404)), pmc_snapshot=snap,
        workouts=workouts, root=root,
    )
