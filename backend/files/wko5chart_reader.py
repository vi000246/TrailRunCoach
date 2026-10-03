"""
Reader for WKO5 `.wko5chart` view files (PKViewPanelConfig / PKDashboardConfig5 /
PKAthleteGraphConfig / PKWorkoutGraphConfig).

Layout (reverse-engineered 2026-09-29 from WKO5 5.0.587 exports):

    b"wko5chart" <version:1 byte> <root field>

Every field is `<tag varint><payload>` where the varint encodes
`field_id << 3 | wire_type`:

    wire 0 / 1  varint (0 = unsigned-ish, 1 = small enum / bool)
    wire 2      8-byte little-endian double
    wire 3      string: <len varint><utf-8 bytes>
    wire 4      nested record: <len varint><fields...>
    wire 6      4-byte little-endian float32

Record boundaries come from the nested length, so unknown fields are skipped
safely. `decode()` returns a generic tree; semantic mapping (chart title,
series expressions, ...) lives in `extract_charts()`.
"""
from __future__ import annotations

import html
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

MAGIC = b"wko5chart"

Value = Union[int, float, str, bytes, "Record"]


@dataclass
class Field:
    fid: int
    wire: int
    value: Value


@dataclass
class Record:
    fields: list[Field] = field(default_factory=list)

    def get(self, fid: int, default: Any = None) -> Any:
        for f in self.fields:
            if f.fid == fid:
                return f.value
        return default

    def all(self, fid: int) -> list[Any]:
        return [f.value for f in self.fields if f.fid == fid]

    @property
    def class_name(self) -> str | None:
        """First string value that looks like a PowerKit class name."""
        for f in self.fields:
            if isinstance(f.value, str) and f.value.startswith("PK"):
                return f.value
        return None


class WKO5ChartFormatError(ValueError):
    pass


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise WKO5ChartFormatError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _decode_record(buf: bytes, pos: int, end: int) -> Record:
    rec = Record()
    while pos < end:
        tag, pos = _varint(buf, pos)
        fid, wire = tag >> 3, tag & 7
        if wire in (0, 1):
            val, pos = _varint(buf, pos)
        elif wire == 2:
            val = struct.unpack_from("<d", buf, pos)[0]
            pos += 8
        elif wire == 6:
            val = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
        elif wire in (3, 4, 5):
            n, pos = _varint(buf, pos)
            if pos + n > end:
                raise WKO5ChartFormatError(f"field {fid} overruns record at {pos}")
            raw = buf[pos:pos + n]
            if wire == 5:
                # Packed blob (.wko4 sample channels). Contents are either a
                # sub-record or raw delta-varints; caller decodes explicitly
                # with decode_record_bytes() / unpack_varints().
                val = bytes(raw)
            elif wire == 3:
                try:
                    val = raw.decode("utf-8")
                except UnicodeDecodeError:
                    val = raw
            else:
                val = _decode_record(buf, pos, pos + n)
            pos += n
        else:
            raise WKO5ChartFormatError(f"unknown wire type {wire} (field {fid}) at {pos}")
        rec.fields.append(Field(fid, wire, val))
    return rec


def file_kind(data: bytes) -> str | None:
    """Every WKO5 file is b"wko" + kind letters + 0x1a, e.g. wko5chart, wko4,
    wko5athlete, wko5cache, wko5home."""
    if not data.startswith(b"wko"):
        return None
    end = data.find(b"\x1a", 0, 32)
    return data[:end].decode("ascii") if end > 0 else None


def decode(data: bytes) -> Record:
    """Decode any WKO5 file (.wko5chart / .wko4 / .wko5athlete / .wko5cache ...)."""
    kind = file_kind(data)
    if kind is None:
        raise WKO5ChartFormatError("not a WKO5 file")
    return _decode_record(data, len(kind) + 1, len(data))


def decode_record_bytes(raw: bytes) -> Record:
    return _decode_record(raw, 0, len(raw))


def unpack_varints(raw: bytes) -> list[int]:
    out, pos = [], 0
    while pos < len(raw):
        v, pos = _varint(raw, pos)
        out.append(v)
    return out


def decode_file(path: str | Path) -> Record:
    return decode(Path(path).read_bytes())


def dump(rec: Record, depth: int = 0, max_str: int = 100) -> str:
    """Human-readable tree, for reverse-engineering field ids."""
    lines = []
    pad = "  " * depth
    for f in rec.fields:
        v = f.value
        if isinstance(v, Record):
            lines.append(f"{pad}{f.fid}/{f.wire} {{  # {v.class_name or ''}")
            lines.append(dump(v, depth + 1, max_str))
            lines.append(f"{pad}}}")
        elif isinstance(v, bytes):
            lines.append(f"{pad}{f.fid}/{f.wire} <{len(v)} bytes>")
        elif isinstance(v, str):
            s = v if len(v) <= max_str else v[:max_str] + "…"
            lines.append(f"{pad}{f.fid}/{f.wire} {s!r}")
        else:
            lines.append(f"{pad}{f.fid}/{f.wire} {v!r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Semantic extraction
# ---------------------------------------------------------------------------
# Field ids mapped by inspecting `dump()` output of exported Season / Workout
# views against what WKO5 shows in the chart editor.
F_CLASS = 401
F_BODY = 402          # config body (graph settings, or dashboard wrapper)
F_CHILDREN = 432      # list of child configs (dashboards / graphs)
F_CHILD = 102
F_META = 402          # nested inside a graph body: author/category/description/title
F_TITLE_BOX = 994
F_TITLE = 109
F_AUTHOR = 427
F_CATEGORY = 438
F_DESCRIPTION = 428
F_TAGS = 423
F_GRID_X, F_GRID_Y = 1211, 1212
F_GRID_W, F_GRID_H = 433, 434
F_ORIENTATION = 421
F_SERIES_LIST = 415
F_SERIES = 450
F_AXES = 417
F_AXIS = 108

S_ID, S_NAME, S_TYPE = 109, 454, 459
S_LINE_STYLE, S_LINE_WIDTH = 464, 463
S_EXPR = 461
S_X_AXIS, S_Y_AXIS = 470, 471
S_LABEL_POS = 473
S_COLOR = 458

AXIS_ID, AXIS_BODY = 101, 102
AXIS_MIN, AXIS_MAX = 502, 503

_AUTO = 1.7976931348623157e308
GRAPH_CLASSES = ("PKAthleteGraphConfig", "PKWorkoutGraphConfig")


def _color(rec: Any) -> str | None:
    if not isinstance(rec, Record):
        return None
    r, g, b, a = (rec.get(fid, 0.0) for fid in (190, 191, 192, 193))
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (r, g, b))) + (
        "" if a >= 0.999 else f"{round(a * 255):02x}"
    )


def _text(v: Any) -> str | None:
    """WKO5 stores UI text XML-escaped (e.g. &#x2F;)."""
    return html.unescape(v) if isinstance(v, str) and v else None


def _meta(rec: Record) -> dict:
    box = rec.get(F_TITLE_BOX)
    tags = rec.get(F_TAGS)
    return {
        "title": _text(box.get(F_TITLE)) if isinstance(box, Record) else None,
        "author": rec.get(F_AUTHOR) or None,
        "category": rec.get(F_CATEGORY) or None,
        "description": _text(rec.get(F_DESCRIPTION)),
        "tags": tags.all(F_CHILD) if isinstance(tags, Record) else [],
    }


def _series(rec: Record) -> dict:
    return {
        "id": rec.get(S_ID),
        "name": _text(rec.get(S_NAME)),
        "type": rec.get(S_TYPE),
        "expression": rec.get(S_EXPR),
        "x_axis": rec.get(S_X_AXIS),
        "y_axis": rec.get(S_Y_AXIS),
        "line_style": rec.get(S_LINE_STYLE),
        "line_width": rec.get(S_LINE_WIDTH),
        "label_position": rec.get(S_LABEL_POS),
        "color": _color(rec.get(S_COLOR)),
    }


def _axes(rec: Any) -> list[dict]:
    if not isinstance(rec, Record):
        return []
    out = []
    for ax in rec.all(F_AXIS):
        body = ax.get(AXIS_BODY)
        lo = body.get(AXIS_MIN) if isinstance(body, Record) else None
        hi = body.get(AXIS_MAX) if isinstance(body, Record) else None
        out.append({
            "id": ax.get(AXIS_ID),
            "min": None if lo in (None, _AUTO) else lo,
            "max": None if hi in (None, _AUTO) else hi,
        })
    return out


def _graph(cfg: Record) -> dict:
    body = cfg.get(F_BODY)
    series_list = body.get(F_SERIES_LIST) if isinstance(body, Record) else None
    meta_rec = body.get(F_META) if isinstance(body, Record) else None
    return {
        "kind": "workout" if cfg.class_name == "PKWorkoutGraphConfig" else "athlete",
        **(_meta(meta_rec) if isinstance(meta_rec, Record) else {}),
        "grid": {"x": cfg.get(F_GRID_X), "y": cfg.get(F_GRID_Y),
                 "w": meta_rec.get(F_GRID_W) if isinstance(meta_rec, Record) else None,
                 "h": meta_rec.get(F_GRID_H) if isinstance(meta_rec, Record) else None},
        "orientation": body.get(F_ORIENTATION) if isinstance(body, Record) else None,
        "axes": _axes(body.get(F_AXES) if isinstance(body, Record) else None),
        "series": [_series(s) for s in series_list.all(F_SERIES)]
        if isinstance(series_list, Record) else [],
    }


def _container(body: Any) -> Record | None:
    """A container is {994: meta, 432: children}. The view body *is* one;
    a dashboard body wraps it one level deeper under 994."""
    if not isinstance(body, Record):
        return None
    if body.get(F_CHILDREN) is not None:
        return body
    inner = body.get(F_TITLE_BOX)
    if isinstance(inner, Record) and inner.get(F_CHILDREN) is not None:
        return inner
    return None


def _children(body: Any) -> list[Record]:
    c = _container(body)
    kids = c.get(F_CHILDREN) if c else None
    return kids.all(F_CHILD) if isinstance(kids, Record) else []


def _container_meta(body: Any) -> dict:
    c = _container(body)
    meta = c.get(F_TITLE_BOX) if c else None
    return _meta(meta) if isinstance(meta, Record) else {}


def extract_charts(root: Record) -> dict:
    """Return {"view": title, "dashboards": [{"title", "charts": [...]}]}.
    Non-graph panels (maps etc.) are listed with only their class name."""
    panel = root.get(400) if isinstance(root.get(400), Record) else root
    body = panel.get(F_BODY)
    vmeta = _container_meta(body)
    title = vmeta.get("title") or ""
    view = {"view": title.rsplit("/", 1)[-1] or None, "dashboards": []}
    for dash in _children(body):
        dbody = dash.get(F_BODY)
        dmeta = _container_meta(dbody)
        entry = {"title": dmeta.get("title"), "class": dash.class_name,
                 "description": dmeta.get("description"), "charts": []}
        for g in _children(dbody):
            if g.class_name in GRAPH_CLASSES:
                entry["charts"].append(_graph(g))
            else:
                entry["charts"].append({"kind": "other", "class": g.class_name})
        view["dashboards"].append(entry)
    return view


def read_view(path: str | Path) -> dict:
    return extract_charts(decode_file(path))


if __name__ == "__main__":
    import json
    import sys
    args = sys.argv[1:]
    if args and args[0] == "--dump":
        print(dump(decode_file(args[1])))
    else:
        print(json.dumps(read_view(args[0]), ensure_ascii=False, indent=2))
