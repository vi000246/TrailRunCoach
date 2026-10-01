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


def build_run(start: datetime, seconds: int = 600, hr: int = 140, power: int = 0,
              speed_m_s: float = 3.0, climb_m_per_s: float = 0.0, total_ascent: int | None = None,
              sport: int = 1, sub_sport: int | None = None, stryd: bool = False,
              stryd_device: bool = False) -> bytes:
    """A 1 Hz run starting at `start` (aware UTC). power=0 → no power channel;
    a list gives the power of each second (its length sets the duration).
    FIT enums: sport 1 running, 2 cycling, 10 training; sub_sport 0 generic,
    1 treadmill, 3 trail, 20 strength_training. `stryd`: the records also
    carry the Stryd developer fields (Form Power, Air Power, Leg Spring
    Stiffness) as a COROS watch with a paired pod writes them; without it a
    power run reads as watch-estimated power (backend/engine/power_source.py).
    `stryd_device`: a device_info row with manufacturer stryd (95)."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    watts = None
    if isinstance(power, (list, tuple)):
        watts, seconds = list(power), len(power)
        power = 1
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
        vals = [_ts(start + timedelta(seconds=i)), hr, int(speed_m_s * i * 100),
                int((alt + 500) * 5), int(speed_m_s * 1000)]
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
                seconds * 1000, seconds * 1000, int(speed_m_s * seconds * 100), ascent]
    if sub_sport is not None:
        ses.append((6, ENUM))
        ses_vals.append(sub_sport)
    body += _definition(2, 18, ses) + _data(2, ses, ses_vals)

    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", _crc(header))
    data = header + body
    return data + struct.pack("<H", _crc(data))
