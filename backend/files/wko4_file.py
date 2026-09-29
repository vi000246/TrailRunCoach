"""
Full decoder for WKO5 activity files (`.wko4`), built on the shared tagged
encoding in `wko5chart_reader` (same format as `.wko5chart`, magic b"wko4").

Top-level records (reverse-engineered 2026-09-29, WKO5 5.0.587 downloads):

    4001  workout info: 4020 sport, 4044 start time (local ISO), 4017/4018
          device, 4009 weight kg, 4010 FTP?, 4012 LTHR?, 4049 original file
          blob + 4050 its type ("fit")
    4201  ranges (4202 each): 4204 name ("Entire Workout", "Peak 0:05:00
          Speed"...), 4205 start s, 4206 duration s, 4237 per-channel stats
          {4238 name, 4239 {4240 min, 4241 max, 4242 avg}} — WKO5's own numbers

Averages (verified against every fresh stored stat, 2026-09-29):
    Entire Workout: time-weighted, each sample weighted by the gap since the
    previous sample, the first sample by (t0 - range start). Invalid samples
    are skipped; cadence also skips zeros (power keeps them).
    Peak ranges: plain mean of valid samples with start <= t <= start+duration.
    4401  channels: 116 -> 4403 {101 name, 102 {119, 120 last value,
          121 packed block}}
            packed block: 111 encoding (0 = zigzag int32 delta varints,
            1 = raw float64 array), 112 sample count, 114 scale divisor,
            122 content hash, 115 sample data
            (int: 0x7fffffff == "no data"; float64: DBL_MAX == "no data")
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.files.wko5chart_reader import (
    Record, decode, decode_record_bytes, unpack_varints,
)

INVALID = 0x7FFFFFFF
_AUTO = 1.7976931348623157e308


def _zigzag(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _signed32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - (1 << 32) if n & 0x80000000 else n


@dataclass
class Channel:
    name: str
    values: list[Optional[float]]   # None where WKO5 has no data
    scale: float
    raw_hash: Optional[int] = None
    base: Optional[float] = None    # outer field 119: the channel's value before sample 0
                                    # (0 for elapsedtime; the starting odometer for distance)


@dataclass
class RangeStats:
    name: str
    start_s: Optional[float]
    end_s: Optional[float]
    stats: dict[str, dict[str, float]] = field(default_factory=dict)  # ch -> min/max/avg
    # WKO5 cache keys, e.g. "calculateMinMaxAvgMetrics|all|heartrate" -> "8650|17385";
    # the last number is the channel content hash (packed field 122) the stats
    # were computed from — if it differs, the stored stats are stale.
    cache: dict[str, str] = field(default_factory=dict)


@dataclass
class Wko4File:
    path: str
    sport: Optional[str]
    start_time: Optional[str]
    device: Optional[str]
    weight_kg: Optional[float]
    original_type: Optional[str]
    original_bytes: Optional[bytes]
    channels: dict[str, Channel]
    ranges: list[RangeStats]
    info: Record


def _num(v):
    return None if v is None or v == _AUTO else v



def decode_channel(name: str, block: bytes) -> Channel:
    rec = decode_record_bytes(block)
    count = rec.get(112, 0)
    scale = rec.get(114, 1.0) or 1.0
    deltas = unpack_varints(rec.get(115, b""))
    # Encoding flag 111: 0 = zigzag int32 deltas / scale; 1 = raw little-endian
    # float64 array (used by "@" developer channels, verticaloscillation...),
    # where DBL_MAX marks "no data".
    if rec.get(111, 0) == 1:
        raw = rec.get(115, b"")
        n = len(raw) // 8
        doubles = struct.unpack(f"<{n}d", raw[:n * 8])
        values = [None if d >= _AUTO or d != d else d / scale for d in doubles]
        return Channel(name=name, values=values, scale=scale, raw_hash=rec.get(122))

    values: list[Optional[float]] = []
    acc = 0
    for d in deltas[:count] if count else deltas:
        acc = _signed32(acc + _zigzag(d))
        values.append(None if acc == INVALID else acc / scale)
    return Channel(name=name, values=values, scale=scale, raw_hash=rec.get(122))


def read_wko4(path: str | Path) -> Wko4File:
    root = decode(Path(path).read_bytes())
    info = root.get(4001) or Record()

    channels: dict[str, Channel] = {}
    ch_root = root.get(4401)
    ch_list = ch_root.get(116) if isinstance(ch_root, Record) else None
    for c in ch_list.all(4403) if isinstance(ch_list, Record) else []:
        body = c.get(102)
        block = body.get(121) if isinstance(body, Record) else None
        if isinstance(block, bytes):
            ch = decode_channel(c.get(101), block)
            ch.base = _num(body.get(119))
            channels[ch.name] = ch

    ranges: list[RangeStats] = []
    rng_root = root.get(4201)
    for r in rng_root.all(4202) if isinstance(rng_root, Record) else []:
        start, dur = _num(r.get(4205)), _num(r.get(4206))
        end = start + dur if start is not None and dur is not None else None
        rs = RangeStats(r.get(4204), start, end)
        cache = r.get(4059)
        for kv in cache.all(108) if isinstance(cache, Record) else []:
            rs.cache[kv.get(101)] = kv.get(102)
        for s in r.all(4237):
            vals = s.get(4239)
            if isinstance(vals, Record):
                rs.stats[s.get(4238)] = {
                    k: _num(vals.get(fid))
                    for k, fid in (("min", 4240), ("max", 4241), ("avg", 4242))
                }
        ranges.append(rs)

    orig = info.get(4049)
    device = " ".join(x for x in (info.get(4017), info.get(4018)) if x) or None
    return Wko4File(
        path=str(path),
        sport=info.get(4020),
        start_time=info.get(4044),
        device=device,
        weight_kg=_num(info.get(4009)),
        original_type=info.get(4050) or None,
        original_bytes=orig.encode("latin-1") if isinstance(orig, str) else orig,
        channels=channels,
        ranges=ranges,
        info=info,
    )


# Channels whose WKO5 average ignores zero samples (e.g. stopped = cadence 0).
ZERO_EXCLUDED_AVG = frozenset({"cadence"})


def range_average(w: Wko4File, channel: str, start_s: float = 0.0,
                  end_s: Optional[float] = None) -> Optional[float]:
    """WKO5 range average. Each sample holds its value over the interval
    (previous sample time, its own time] — the first sample from `start_s` —
    and the average is time-weighted over that interval clipped to
    (start_s, end_s]. Invalid samples are skipped (and zeros, for
    ZERO_EXCLUDED_AVG channels)."""
    c, t = w.channels.get(channel), w.channels.get("elapsedtime")
    if not c or not t or len(c.values) != len(t.values):
        return None
    skip_zero = channel in ZERO_EXCLUDED_AVG
    hi_lim = float("inf") if end_s is None else end_s
    num = den = 0.0
    prev = None
    for v, ti in zip(c.values, t.values):
        if ti is None:
            continue
        lo = start_s if prev is None else prev
        prev = ti
        dt = min(ti, hi_lim) - max(lo, start_s)
        if dt <= 0 or v is None or (skip_zero and v == 0):
            continue
        num += v * dt
        den += dt
    return num / den if den else None


def range_stats(w: Wko4File, channel: str, start_s: float = 0.0,
                end_s: Optional[float] = None) -> Optional[dict]:
    """min / max / avg over samples whose hold interval (prev t, t] overlaps
    (start_s, end_s] — the same sample set WKO5 uses for range stats."""
    c, t = w.channels.get(channel), w.channels.get("elapsedtime")
    if not c or not t or len(c.values) != len(t.values):
        return None
    hi_lim = float("inf") if end_s is None else end_s
    sel, prev = [], None
    for v, ti in zip(c.values, t.values):
        if ti is None:
            continue
        lo = start_s if prev is None else prev
        prev = ti
        # zero-length first interval (t0 == start) still counts as inside
        inside = (min(ti, hi_lim) - max(lo, start_s) > 0) or (lo == ti and start_s <= ti <= hi_lim)
        if inside and v is not None:
            sel.append(v)
    if not sel:
        return None
    return {"min": min(sel), "max": max(sel), "avg": range_average(w, channel, start_s, end_s)}


def workout_average(w: Wko4File, channel: str, start_s: float = 0.0) -> Optional[float]:
    """WKO5 'Entire Workout' average."""
    return range_average(w, channel, start_s)


def window_average(w: Wko4File, channel: str, start_s: float, end_s: float) -> Optional[float]:
    """WKO5 peak-range average."""
    return range_average(w, channel, start_s, end_s)
