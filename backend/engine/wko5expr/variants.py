"""
Chart variants — one chart card, several ways to draw it (a segmented toggle).

A custom chart opts in with a list of variants instead of top-level axes / series:

    "variants": [
      {"key": "tss", "label": "TSS",   "axes": [...], "series": [...]},
      {"key": "pct", "label": "% CTL", "axes": [...], "series": [...],
       "description": "optional: replaces the chart's description in this variant"}
    ]

The first variant is the default. Each variant's series go through the same
validation as a chart's series (customviews._series). The parsed chart keeps
the default variant's axes / series at the top level, so everything that reads
a chart without asking for a variant (the view list, a client that never sends
`?variant=`) sees the default.

The viewer's toggle asks for `?variant=<key>`; `apply_variant` swaps in that
variant's axes / series / description. An unknown key falls back to the
default (like basis.py's `?basis=`). The chosen key is part of the render-cache
key (api/wko5views.chart).
"""
from __future__ import annotations

import copy
from typing import Callable, Optional

KEYS = ("key", "label", "axes", "series", "description")


class VariantError(ValueError):
    pass


def parse_variants(raw, where: str, parse_series: Callable[[dict, str], dict]) -> list[dict]:
    """Validate a chart's `variants` list; series via `parse_series` (customviews._series)."""
    if not isinstance(raw, list) or not raw:
        raise VariantError(f"{where}: variants must be a non-empty list")
    out, seen = [], set()
    for i, v in enumerate(raw):
        if not isinstance(v, dict):
            raise VariantError(f"{where}: variant {i} must be an object")
        key = v.get("key")
        if not isinstance(key, str) or not key.strip():
            raise VariantError(f"{where}: variant {i} needs a key")
        if key in seen:
            raise VariantError(f"{where}: variant key {key!r} is used twice")
        seen.add(key)
        label = v.get("label")
        if not isinstance(label, str) or not label.strip():
            raise VariantError(f"{where}: variant {key!r} needs a label")
        axes = v.get("axes") or []
        if not isinstance(axes, list):
            raise VariantError(f"{where}: variant {key!r} axes must be a list")
        series = v.get("series")
        if not isinstance(series, list) or not series:
            raise VariantError(f"{where}: variant {key!r} needs series")
        out.append({"key": key, "label": label, "axes": axes,
                    "series": [parse_series(s, f"{where}/{key}") for s in series],
                    "description": v.get("description")})
    return out


def variant_spec(chart: dict) -> Optional[list[dict]]:
    vs = chart.get("variants")
    return vs if isinstance(vs, list) and vs else None


def apply_variant(chart: dict, asked: Optional[str]) -> tuple[dict, Optional[dict]]:
    """(chart drawn as the chosen variant, extra JSON for the viewer's toggle)."""
    spec = variant_spec(chart)
    if spec is None:
        return chart, None
    chosen = next((v for v in spec if v["key"] == asked), spec[0])
    out = {k: copy.deepcopy(val) for k, val in chart.items() if k != "variants"}
    out["axes"] = copy.deepcopy(chosen["axes"])
    out["series"] = copy.deepcopy(chosen["series"])
    if chosen.get("description"):
        out["description"] = chosen["description"]
    out["variant_chosen"] = chosen["key"]
    return out, {"variant": chosen["key"], "variant_default": spec[0]["key"],
                 "variant_choices": [{"key": v["key"], "label": v["label"]} for v in spec],
                 "variant_toggle": len(spec) > 1}
