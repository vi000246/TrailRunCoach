"""
「使用功率」 (settings: charts.power.enabled) — which charts need running power.

Most athletes train by heart rate only. With the setting off, the viewer
hides every chart that is about power / CP and keeps the HR (or pace)
versions; a chart with a 配速／功率 toggle (basis.py) stays, locked to pace.

A chart needs power when
  * it is a power-only panel: a zone table in watts, the per-activity power
    zones, the CP test / W′ battery and the interval review cards (their
    efforts are detected from power, workout_review._intervals), or
  * every series that plots data reads power — a power channel, CP / FTP /
    W′, NP / IF / Pw:HR, a PD-model function or drift("power").
    Reference lines ((,0), {0:0.8}, goal lines) don't count either way, so a
    power curve with a (,cp) line still counts as power and an HR chart with
    a (,cp) line would not.

Charts with mixed series (HR and power both) are kept; the setting removes
whole charts only.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from backend.engine.wko5expr import parser as P

SETTING_KEY = "charts.power.enabled"

POWER_IDENTS = frozenset({
    "power", "runpower", "bikepower", "_rapower", "_rapower4", "ecpower", "xpower",
    "cp", "ftp", "mftp", "runftp", "bikeftp", "sftp", "wprime", "frc", "pmax",
    "np", "if", "pwhr", "vi", "work",
    # built-ins that read power (evaluator.BUILTIN_EXPRS): TIS is na without a power channel
    "tisaerobic", "tisanaerobic", "stamina",
})
POWER_CALLS = frozenset({
    "pdcurve", "ftp", "frc", "pmax", "vo2max", "tte", "stamina", "pdprofile",
    "ftpcurve", "frccurve", "targetpower", "levelfrom", "levelto",
})
# review cards that only exist with power (workout_review.py)
POWER_SECTIONS = frozenset({
    "cp_test", "wprime_battery", "intervals", "interval_verdict", "interval_reps",
    "interval_power", "interval_battery", "interval_tiz", "interval_hr",
})
POWER_ACTIVITY_CHARTS = frozenset({"powerzones"})        # panels/activity_charts.py


@lru_cache(maxsize=4096)
def _classify(expr: str) -> Optional[str]:
    """'power' | 'data' | None (a reference line / nothing to plot)."""
    try:
        node = P.parse(expr)
    except Exception:                       # noqa: BLE001 — unparseable: let it count as data
        return "data"
    while isinstance(node, P.Unit):
        node = node.value
    if isinstance(node, (P.Empty, P.Num, P.Pair, P.RangeLit, P.Str)):
        return None
    if isinstance(node, P.Ident) and node.name.startswith("goal"):
        return None
    for n in P.walk(node):
        if isinstance(n, P.Ident) and n.name in POWER_IDENTS:
            return "power"
        if isinstance(n, P.Call):
            if n.name in POWER_CALLS:
                return "power"
            if n.name in ("drift", "drift_avg") and n.args and isinstance(n.args[0], P.Str) \
                    and n.args[0].value.strip('"') == "power":
                return "power"
    return "data"


def chart_needs_power(ch: dict) -> bool:
    kind = ch.get("kind")
    if kind == "review":
        return ch.get("section") in POWER_SECTIONS
    if kind == "activity":
        return ch.get("chart") in POWER_ACTIVITY_CHARTS
    if kind == "zones":
        from backend.engine.zones import SYSTEMS
        return (SYSTEMS.get(ch.get("system")) or {}).get("unit") == "W"
    if kind not in ("athlete", "workout", None):
        return False
    basis = ch.get("basis") or {}
    if "pace" in (basis.get("choices") or ()):
        return False                        # drawn on pace when power is off
    series = [s for v in ch.get("variants") or [] for s in v.get("series", [])] or ch.get("series") or []
    kinds = [_classify(s.get("expression") or "") for s in series]
    data = [k for k in kinds if k is not None]
    return bool(data) and all(k == "power" for k in data)


def power_basis(ch: dict) -> bool:
    """A 配速／功率 toggle that the setting locks to pace."""
    b = ch.get("basis") or {}
    return "power" in (b.get("choices") or ()) and "pace" in (b.get("choices") or ())
