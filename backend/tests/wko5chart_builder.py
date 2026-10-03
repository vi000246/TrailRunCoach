"""Tiny synthetic `.wko5chart` files for the tests.

Writes the layout backend/files/wko5chart_reader.py reads (tag varint =
field_id << 3 | wire; wire 0 varint, 2 double, 3 string, 4 nested record),
so no exported WKO5 view is needed in the repo. Only the fields the reader
maps are written.

    write_view(path, "Test Season View", [
        ("Dashboard", [chart("PD", [series("MMP", "meanmax(power)")])]),
    ])
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Union

from backend.files import wko5chart_reader as R

Node = Union[int, float, str, "list[tuple[int, Node]]"]


def _varint(v: int) -> bytes:
    out = b""
    while True:
        b = v & 0x7F
        v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out


def _enc(fields: list) -> bytes:
    """[(fid, value), ...] -> bytes; a list value is a nested record."""
    out = b""
    for fid, v in fields:
        if isinstance(v, list):
            raw = _enc(v)
            out += _varint(fid << 3 | 4) + _varint(len(raw)) + raw
        elif isinstance(v, str):
            raw = v.encode("utf-8")
            out += _varint(fid << 3 | 3) + _varint(len(raw)) + raw
        elif isinstance(v, float):
            out += _varint(fid << 3 | 2) + struct.pack("<d", v)
        else:
            out += _varint(fid << 3 | 0) + _varint(int(v))
    return out


def _meta(title: str, description: str | None = None) -> list:
    m = [(R.F_TITLE_BOX, [(R.F_TITLE, title)])]
    if description:
        m.append((R.F_DESCRIPTION, description))
    return m


def series(name: str, expression: str, y_axis: str = "WATTS", x_axis: str = "HMSSHORT",
           type_: str = "line") -> list:
    return [(R.S_NAME, name), (R.S_TYPE, type_), (R.S_EXPR, expression),
            (R.S_X_AXIS, x_axis), (R.S_Y_AXIS, y_axis)]


def chart(title: str, series_list: list, workout: bool = False, description: str | None = None,
          axes: tuple = ("WATTS",)) -> list:
    cls = "PKWorkoutGraphConfig" if workout else "PKAthleteGraphConfig"
    body = [(R.F_META, _meta(title, description)),
            (R.F_AXES, [(R.F_AXIS, [(R.AXIS_ID, a), (R.AXIS_BODY, [(R.AXIS_MIN, 0.0)])]) for a in axes]),
            (R.F_SERIES_LIST, [(R.F_SERIES, s) for s in series_list])]
    return [(R.F_CLASS, cls), (R.F_BODY, body)]


def map_panel() -> list:
    return [(R.F_CLASS, "PKMapPanelConfig")]


def _container(title: str, children: list, description: str | None = None) -> list:
    return [(R.F_TITLE_BOX, _meta(title, description)),
            (R.F_CHILDREN, [(R.F_CHILD, c) for c in children])]


def view_bytes(title: str, dashboards: list) -> bytes:
    """dashboards: [(title, [chart, ...]), ...]"""
    dash = [[(R.F_CLASS, "PKDashboardConfig5"), (R.F_BODY, _container(t, charts))] for t, charts in dashboards]
    root = [(400, [(R.F_CLASS, "PKViewPanelConfig"), (R.F_BODY, _container(title, dash))])]
    return R.MAGIC + b"\x1a" + _enc(root)


def write_view(path: Path, title: str, dashboards: list) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(view_bytes(title, dashboards))
    return path


NEW_BESTS = ("@historic := athleterange(min({begindate,today-7}), min({enddate,today-7}), meanmax(runpower)),\n"
             "@recent := athleterange(today-6, today, meanmax(runpower)),\n"
             "if(@recent > @historic, @recent)")


def season_view(path: Path, title: str = "WKO5 Season View") -> Path:
    """A two-dashboard athlete view: a PD chart with a "New Bests" series and a load chart."""
    return write_view(path, title, [
        ("Load", [chart("Daily % of CTL (run TSS&#x2F;CTL)",
                        [series("TSS/CTL", 'tss/ctl', y_axis="PERCENT", x_axis="DATE")], axes=("PERCENT",))]),
        ("PDC", [chart("PD Curve with Metrics (Run)", [
            series("MMP Curve", "meanmax(runpower)"),
            series("New Bests", NEW_BESTS, type_="area"),
        ])]),
    ])


def workout_view(path: Path, title: str = "WKO5 Workout View") -> Path:
    return write_view(path, title, [
        ("Workout", [chart("Power & HR", [series("Power", "power"), series("HR", "heartrate", y_axis="BPM")],
                           workout=True, axes=("WATTS", "BPM")),
                     map_panel()]),
    ])
