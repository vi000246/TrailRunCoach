"""FIT *writer* (moved here from backend/tests/fit_builder.py and extended for
the demo athlete, docs/plans/auth-and-demo.plan.md §3.2).

Two entry points:

* `build_run(...)` — the small 1 Hz run the tests have always used (file_id,
  records, session; optional Stryd developer fields / device_info).
* `encode_activity(act)` — a full activity from per-second numpy arrays:
  position, altitude, distance, speed, heart rate (with dropouts), cadence,
  power, temperature, the Stryd developer fields (Form Power, Air Power, Leg
  Spring Stiffness, as a COROS watch forwards them), laps and session totals.
  The records are packed with one numpy structured array (fast for a year of
  1 Hz data). Nothing in the file depends on the wall clock: the bytes are a
  function of the inputs only.

FIT spec: 14-byte header, definition + data messages, CRC-16 over everything.
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import numpy as np

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
SEMI = 2 ** 31 / 180.0              # degrees -> semicircles

_CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def _crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def _crc_fast(data: bytes) -> int:
    """CRC-16 (FIT) with a 256-entry table: the demo files are ~100 KB each."""
    crc = 0
    tab = _CRC256
    for byte in data:
        crc = (crc >> 8) ^ tab[(crc ^ byte) & 0xFF]
    return crc


def _make_crc256() -> list[int]:
    out = []
    for b in range(256):
        out.append(_crc(bytes([b])))
    return out


_CRC256 = _make_crc256()

# base types: (id, struct fmt, size)
ENUM, SINT8, UINT8, UINT16, SINT32, UINT32 = ((0x00, "B", 1), (0x01, "b", 1), (0x02, "B", 1),
                                              (0x84, "H", 2), (0x85, "i", 4), (0x86, "I", 4))


def _definition(local: int, global_num: int, fields) -> bytes:
    out = struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
    for num, (bt, _fmt, size) in fields:
        out += struct.pack("<BBB", num, size, bt)
    return out


def _data(local: int, fields, values) -> bytes:
    out = struct.pack("<B", local)
    for (_num, (_bt, fmt, _size)), v in zip(fields, values):
        out += struct.pack("<" + fmt, v)
    return out


def _string(n: int):
    return (0x07, f"{n}s", n)


BYTES16 = (0x0D, "16s", 16)


def _dev_definition(local: int, global_num: int, fields, dev_fields) -> bytes:
    """A definition message with developer fields: dev_fields = [(field num,
    size, developer data index)]."""
    out = struct.pack("<BBBHB", 0x40 | 0x20 | local, 0, 0, global_num, len(fields))
    for num, (bt, _fmt, size) in fields:
        out += struct.pack("<BBB", num, size, bt)
    out += struct.pack("<B", len(dev_fields))
    for num, size, idx in dev_fields:
        out += struct.pack("<BBB", num, size, idx)
    return out


# the Stryd developer fields a COROS watch forwards (names as in real files)
STRYD_DEV_FIELDS = ((0, "Form Power", "Watts"), (1, "Air Power", "Watts"), (2, "Leg Spring Stiffness", "KN/m"))


def _stryd_dev_header() -> bytes:
    """developer_data_id + field_description messages for STRYD_DEV_FIELDS
    (local types 4 / 5)."""
    out = b""
    did = [(1, BYTES16), (3, UINT8)]
    out += _definition(4, 207, did) + _data(4, did, [bytes(15) + b"\x47", 0])
    for num, name, units in STRYD_DEV_FIELDS:
        nb, ub = name.encode() + b"\0", units.encode() + b"\0"
        fd = [(0, UINT8), (1, UINT8), (2, UINT8), (3, _string(len(nb))), (8, _string(len(ub)))]
        out += _definition(5, 206, fd) + _data(5, fd, [0, num, 0x84, nb, ub])
    return out


def _ts(t: datetime) -> int:
    return int((t - FIT_EPOCH).total_seconds())


def _wrap(body: bytes) -> bytes:
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", _crc(header))
    data = header + body
    return data + struct.pack("<H", _crc_fast(data))


def build_run(start: datetime, seconds: int = 600, hr: int = 140, power: int = 0,
              speed_m_s: float = 3.0, climb_m_per_s: float = 0.0, total_ascent: int | None = None,
              sport: int = 1, sub_sport: int | None = None, stryd: bool = False,
              stryd_device: bool = False, speeds_m_s: list | None = None,
              workout_feel: int | None = None, workout_rpe: int | None = None) -> bytes:
    """A 1 Hz run starting at `start` (aware UTC). power=0 → no power channel;
    a list gives the power of each second (its length sets the duration).
    FIT enums: sport 1 running, 2 cycling, 10 training; sub_sport 0 generic,
    1 treadmill, 3 trail, 20 strength_training. `stryd`: the records also
    carry the Stryd developer fields (Form Power, Air Power, Leg Spring
    Stiffness) as a COROS watch with a paired pod writes them; without it a
    power run reads as watch-estimated power (backend/engine/power_source.py).
    `stryd_device`: a device_info row with manufacturer stryd (95).
    `speeds_m_s`: the speed of each second (its length sets the duration;
    distance accumulates), e.g. a run with a car segment at the end."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    watts = None
    if isinstance(power, (list, tuple)):
        watts, seconds = list(power), len(power)
        power = 1
    if speeds_m_s is not None:
        seconds = len(speeds_m_s)
    dist_m, speed_at = [], []
    acc = 0.0
    for i in range(seconds):
        s = speeds_m_s[i] if speeds_m_s is not None else speed_m_s
        dist_m.append(acc if speeds_m_s is not None else speed_m_s * i)
        speed_at.append(s)
        acc += s
    body = b""
    fid = [(0, ENUM), (1, UINT16), (4, UINT32)]
    body += _definition(0, 0, fid) + _data(0, fid, [4, 255, _ts(start)])

    if stryd_device:
        di = [(253, UINT32), (0, UINT8), (2, UINT16)]
        body += _definition(6, 23, di) + _data(6, di, [_ts(start), 1, 95])

    rec = [(253, UINT32), (3, UINT8), (5, UINT32), (2, UINT16), (6, UINT16)]
    if power:
        rec.append((7, UINT16))
    if stryd:
        body += _stryd_dev_header()
        body += _dev_definition(1, 20, rec, [(num, 2, 0) for num, _n, _u in STRYD_DEV_FIELDS])
    else:
        body += _definition(1, 20, rec)
    for i in range(seconds):
        alt = 100.0 + climb_m_per_s * i
        vals = [_ts(start + timedelta(seconds=i)), hr, int(dist_m[i] * 100),
                int((alt + 500) * 5), int(speed_at[i] * 1000)]
        if power:
            vals.append(int(watts[i]) if watts is not None else power)
        row = _data(1, rec, vals)
        if stryd:
            # Form Power 60 W, Air Power 4 W, LSS 9 kN/m (synthetic)
            row += struct.pack("<HHH", 60, 4, 9)
        body += row

    ses = [(253, UINT32), (2, UINT32), (5, ENUM), (7, UINT32), (8, UINT32), (9, UINT32), (22, UINT16)]
    ascent = total_ascent if total_ascent is not None else int(climb_m_per_s * seconds)
    ses_vals = [_ts(start + timedelta(seconds=seconds)), _ts(start), sport,
                seconds * 1000, seconds * 1000,
                int((acc if speeds_m_s is not None else speed_m_s * seconds) * 100), ascent]
    if sub_sport is not None:
        ses.append((6, ENUM))
        ses_vals.append(sub_sport)
    # the post-workout self-rating a Garmin watch writes (session 192 workout_feel, 193 workout_rpe = RPE × 10)
    if workout_feel is not None:
        ses.append((192, UINT8))
        ses_vals.append(workout_feel)
    if workout_rpe is not None:
        ses.append((193, UINT8))
        ses_vals.append(workout_rpe)
    body += _definition(2, 18, ses) + _data(2, ses, ses_vals)
    return _wrap(body)


# ---------------------------------------------------------------------------
# full activities (the demo athlete)
# ---------------------------------------------------------------------------

COROS_MANUFACTURER = 294          # FIT `manufacturer` enum: coros
STRYD_MANUFACTURER = 95
SPORT_RUNNING, SPORT_HIKING = 1, 17
SUB_GENERIC, SUB_TRAIL = 0, 3


def _u(a, lo, hi, invalid, dtype):
    """Round, clip, NaN -> the FIT invalid value."""
    a = np.asarray(a, dtype=float)
    out = np.where(np.isfinite(a), np.clip(np.rint(a), lo, hi), invalid)
    return out.astype(dtype)


def encode_activity(*, start: datetime, lat: np.ndarray, lon: np.ndarray, alt: np.ndarray,
                    dist: np.ndarray, speed: np.ndarray, hr: np.ndarray, cadence: np.ndarray,
                    power: Optional[np.ndarray], temp: np.ndarray, stryd: bool,
                    laps: Sequence[dict], sport: int = SPORT_RUNNING, sub_sport: int = SUB_GENERIC,
                    total_ascent: float = 0.0, total_descent: float = 0.0,
                    form_power: Optional[np.ndarray] = None, air_power: Optional[np.ndarray] = None,
                    lss: Optional[np.ndarray] = None) -> bytes:
    """One activity, 1 Hz: arrays of equal length n (second i = start + i).
    hr NaN = an optical dropout (FIT invalid). power None = no power channel.
    laps = [{"start_s", "duration_s", "distance_m", "avg_power"}]."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    n = len(dist)
    t0 = _ts(start)
    body = b""
    # file_id: activity (4), COROS, time_created = the start (never the wall clock)
    fid = [(0, ENUM), (1, UINT16), (2, UINT16), (4, UINT32)]
    body += _definition(0, 0, fid) + _data(0, fid, [4, COROS_MANUFACTURER, 1, t0])
    # device_info: the watch, and the Stryd pod when paired
    di = [(253, UINT32), (0, UINT8), (2, UINT16)]
    body += _definition(6, 23, di) + _data(6, di, [t0, 0, COROS_MANUFACTURER])
    if stryd:
        body += _data(6, di, [t0, 1, STRYD_MANUFACTURER])
    # event: timer start
    ev = [(253, UINT32), (0, ENUM), (1, ENUM)]
    body += _definition(7, 21, ev) + _data(7, ev, [t0, 0, 0])

    fields = [(253, UINT32), (0, SINT32), (1, SINT32), (3, UINT8), (4, UINT8), (5, UINT32),
              (2, UINT16), (6, UINT16)]
    dt_fields = [("h", "u1"), ("ts", "<u4"), ("lat", "<i4"), ("lon", "<i4"), ("hr", "u1"), ("cad", "u1"),
                 ("dist", "<u4"), ("alt", "<u2"), ("spd", "<u2")]
    if power is not None:
        fields.append((7, UINT16))
        dt_fields.append(("pw", "<u2"))
    fields.append((13, SINT8))
    dt_fields.append(("tmp", "i1"))
    if stryd:
        body += _stryd_dev_header()
        body += _dev_definition(1, 20, fields, [(num, 2, 0) for num, _n, _u2 in STRYD_DEV_FIELDS])
        dt_fields += [("fp", "<u2"), ("ap", "<u2"), ("lss", "<u2")]
    else:
        body += _definition(1, 20, fields)
    rec = np.zeros(n, dtype=np.dtype(dt_fields))
    rec["h"] = 1
    rec["ts"] = t0 + np.arange(n, dtype=np.int64)
    rec["lat"] = np.rint(np.asarray(lat) * SEMI).astype(np.int64)
    rec["lon"] = np.rint(np.asarray(lon) * SEMI).astype(np.int64)
    rec["hr"] = _u(hr, 1, 254, 0xFF, np.uint8)
    rec["cad"] = _u(cadence, 0, 254, 0xFF, np.uint8)
    rec["dist"] = _u(np.asarray(dist) * 100.0, 0, 2 ** 32 - 2, 2 ** 32 - 1, np.uint32)
    rec["alt"] = _u((np.asarray(alt) + 500.0) * 5.0, 0, 65534, 0xFFFF, np.uint16)
    rec["spd"] = _u(np.asarray(speed) * 1000.0, 0, 65534, 0xFFFF, np.uint16)
    if power is not None:
        rec["pw"] = _u(power, 0, 65534, 0xFFFF, np.uint16)
    rec["tmp"] = _u(temp, -127, 127, 0x7F, np.int8)
    if stryd:
        rec["fp"] = _u(form_power if form_power is not None else np.zeros(n), 0, 65534, 0xFFFF, np.uint16)
        rec["ap"] = _u(air_power if air_power is not None else np.zeros(n), 0, 65534, 0xFFFF, np.uint16)
        rec["lss"] = _u(lss if lss is not None else np.zeros(n), 0, 65534, 0xFFFF, np.uint16)
    body += rec.tobytes()

    end = t0 + n
    # laps (lap 19): event lap (9) / stop (1), manual trigger
    lf = [(253, UINT32), (2, UINT32), (0, ENUM), (1, ENUM), (7, UINT32), (8, UINT32), (9, UINT32),
          (15, UINT8), (19, UINT16), (24, ENUM)]
    body += _definition(3, 19, lf)
    hr_f = np.asarray(hr, dtype=float)
    for lp in laps:
        a, d = int(lp["start_s"]), int(lp["duration_s"])
        seg = hr_f[a:a + d]
        avg_hr = int(np.nanmean(seg)) if np.isfinite(seg).any() else 0xFF
        ap = lp.get("avg_power")
        body += _data(3, lf, [t0 + a + d, t0 + a, 9, 1, d * 1000, d * 1000, int(lp["distance_m"] * 100),
                              avg_hr, 0xFFFF if ap is None else int(round(ap)), 0])
    # timer stop
    body += _data(7, ev, [end, 0, 4])
    # session totals
    fin = np.isfinite(hr_f)
    avg_hr = int(np.nanmean(hr_f)) if fin.any() else 0xFF
    max_hr = int(np.nanmax(hr_f)) if fin.any() else 0xFF
    ses = [(253, UINT32), (2, UINT32), (5, ENUM), (6, ENUM), (7, UINT32), (8, UINT32), (9, UINT32),
           (22, UINT16), (23, UINT16), (16, UINT8), (17, UINT8), (20, UINT16), (26, UINT16)]
    avg_p = 0xFFFF if power is None else int(round(float(np.nanmean(power))))
    body += _definition(2, 18, ses) + _data(2, ses, [
        end, t0, sport, sub_sport, n * 1000, n * 1000, int(float(dist[-1]) * 100) if n else 0,
        int(round(total_ascent)), int(round(total_descent)), avg_hr, max_hr, avg_p, len(laps)])
    # activity (34): one session
    act = [(253, UINT32), (0, UINT32), (1, UINT16), (2, ENUM), (3, ENUM), (4, ENUM)]
    body += _definition(8, 34, act) + _data(8, act, [end, n * 1000, 1, 0, 26, 1])
    return _wrap(body)
