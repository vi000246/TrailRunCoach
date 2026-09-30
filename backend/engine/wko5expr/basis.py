"""
配速／功率 — the drift charts' basis toggle (Pa:HR vs Pw:HR).

A custom chart opts in with

    "basis": {"default": "pace", "choices": ["pace", "power"],
              "power_note": "optional sentence added to the description in power mode"}

and tags the series that belong to one basis:

    {"name": "Pa:HR", "basis": "pace",  "expression": "…pahr…"},
    {"name": "Pw:HR", "basis": "power", "expression": "…pwhr…"}

Untagged series are drawn in both modes. The viewer's 配速／功率 toggle asks
for `?basis=power`; `apply_basis` keeps only the matching series and rewrites
the title and description (Pa:HR → Pw:HR, 速度 → 功率). Each series names
itself, so the legend follows without rewriting.

A review card (kind "review") has no series: the chosen basis is passed on to
workout_review.review (`chart["basis_chosen"]`).

Same pattern as the 近 7／14／28 天 window (recentbests.py).
"""
from __future__ import annotations

import copy
from typing import Optional

BASES = ("pace", "power")
LABELS = {"pace": "配速", "power": "功率"}
# pace wording → power wording in titles and descriptions
_TEXT = (("Pa:HR", "Pw:HR"), ("速度／心率", "功率／心率"), ("速度/心率", "功率/心率"),
         ("每下心跳換到的速度", "每下心跳換到的功率"), ("m/min per bpm", "W per bpm"),
         ("每單位速度", "每單位功率"), ("心率與速度", "心率與功率"), ("速度掉", "功率掉"))
NO_POWER = "這次沒有功率"


def basis_spec(chart: dict) -> Optional[dict]:
    b = chart.get("basis")
    if not isinstance(b, dict):
        return None
    return {"default": b["default"], "choices": list(b["choices"]), "power_note": b.get("power_note")}


def retext(s: Optional[str], basis: str) -> Optional[str]:
    if not s or basis != "power":
        return s
    for a, b in _TEXT:
        s = s.replace(a, b)
    return s


def apply_basis(chart: dict, asked: Optional[str]) -> tuple[dict, Optional[dict]]:
    """(chart for the chosen basis, extra JSON for the viewer's toggle)."""
    spec = basis_spec(chart)
    if spec is None:
        return chart, None
    basis = asked if asked in spec["choices"] else spec["default"]
    out = copy.deepcopy(chart)
    out["series"] = [s for s in out.get("series", []) if s.get("basis") in (None, basis)]
    out["title"] = retext(out.get("title"), basis)
    desc = retext(out.get("description"), basis)
    if basis == "power" and spec.get("power_note"):
        desc = f"{desc} {spec['power_note']}" if desc else spec["power_note"]
    out["description"] = desc
    out["basis_chosen"] = basis
    return out, {"basis": basis, "basis_default": spec["default"], "basis_choices": spec["choices"],
                 "basis_labels": {c: LABELS.get(c, c) for c in spec["choices"]}, "basis_toggle": True}


def no_power_note(res: dict, chart: dict, has_power: bool) -> dict:
    """Power mode on a workout without a power channel: drop the power series
    and say 「這次沒有功率」 — as the chart's empty message when nothing else is
    left to draw, otherwise as a note above it. Never a zero line."""
    if has_power or chart.get("basis_chosen") != "power" or res.get("kind") == "review":
        return res
    power_names = {s.get("name") for s in chart.get("series", []) if s.get("basis") == "power"}
    if not power_names:
        return res
    rest = [s for s in res.get("series") or [] if s.get("name") not in power_names]
    drawn = [s for s in rest if (s.get("data") or {}).get("kind") not in (None, "none", "error")
             and not _constant(s)]
    if not drawn:
        return {**res, "series": rest, "empty": NO_POWER}
    return {**res, "series": rest, "basis_note": NO_POWER}


def _constant(s: dict) -> bool:
    """A reference line like `(,aethr)` is not something to look at on its own."""
    return (s.get("expression") or "").replace(" ", "").startswith("(,")
