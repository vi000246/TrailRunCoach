"""Minimal FIT *writer* for tests — enough for fitparse to read a running
activity (file_id, records, session). FIT spec: header, definition + data
messages, CRC-16 over everything. No personal data; values are synthetic."""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)

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


# base types: (id, struct fmt, size)
ENUM, UINT8, UINT16, UINT32 = (0x00, "B", 1), (0x02, "B", 1), (0x84, "H", 2), (0x86, "I", 4)


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


def _ts(t: datetime) -> int:
    return int((t - FIT_EPOCH).total_seconds())


def build_run(start: datetime, seconds: int = 600, hr: int = 140, power: int = 0,
              speed_m_s: float = 3.0, climb_m_per_s: float = 0.0, total_ascent: int | None = None,
              sport: int = 1) -> bytes:
    """A 1 Hz run starting at `start` (aware UTC). power=0 → no power channel."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    body = b""
    fid = [(0, ENUM), (1, UINT16), (4, UINT32)]
    body += _definition(0, 0, fid) + _data(0, fid, [4, 255, _ts(start)])

    rec = [(253, UINT32), (3, UINT8), (5, UINT32), (2, UINT16), (6, UINT16)]
    if power:
        rec.append((7, UINT16))
    body += _definition(1, 20, rec)
    for i in range(seconds):
        alt = 100.0 + climb_m_per_s * i
        vals = [_ts(start + timedelta(seconds=i)), hr, int(speed_m_s * i * 100),
                int((alt + 500) * 5), int(speed_m_s * 1000)]
        if power:
            vals.append(power)
        body += _data(1, rec, vals)

    ses = [(253, UINT32), (2, UINT32), (5, ENUM), (7, UINT32), (8, UINT32), (9, UINT32), (22, UINT16)]
    ascent = total_ascent if total_ascent is not None else int(climb_m_per_s * seconds)
    body += _definition(2, 18, ses) + _data(2, ses, [
        _ts(start + timedelta(seconds=seconds)), _ts(start), sport,
        seconds * 1000, seconds * 1000, int(speed_m_s * seconds * 100), ascent])

    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", _crc(header))
    data = header + body
    return data + struct.pack("<H", _crc(data))
