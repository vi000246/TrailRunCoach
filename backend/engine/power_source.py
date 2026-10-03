"""
Where a workout's running power came from (2026-10-01).

A runner's history can mix two kinds of running power under the same FIT
`power` field:

* **stryd** — a Stryd foot pod. A COROS watch forwards the
  pod's developer fields (Form Power, Air Power, Leg Spring Stiffness; the FIT
  developer_data_id is COROS's, not Stryd's), and a Garmin / other head unit
  may list the pod in `device_info` (manufacturer "stryd");
* **watch** — power estimated by the watch from the wrist (Garmin / COROS
  watches, or a run with the pod not paired):
  `power` present, no Stryd field, no Stryd device;
* **none** — no power above 0.

WKO5 / TrainingPeaks do not tell them apart: both read the same FIT `power`.
The power-based models (race-power envelope / mean-max, CP / PD fits, power
TSS, power effort checks) use only Stryd power by default, because wrist
power reads on another scale than the pod (seen on the same course and
effort); the setting `power.accept_watch_power` (default False) lets watch
power back in. HR- and pace-based paths always use every run.

Reading a run with Form Power / Air Power / LSS as Stryd is 推估 (the watch
itself computes no form power); so is treating any run without them as watch
power.
"""
from __future__ import annotations

from typing import Iterable, Optional

STRYD, WATCH, NONE = "stryd", "watch", "none"
SOURCES = (STRYD, WATCH, NONE)
LABELS = {STRYD: "Stryd", WATCH: "手錶推估功率", NONE: "沒有功率"}
UNUSED_LABEL = "手錶推估功率（未採用）"
SETTING_KEY = "power.accept_watch_power"

# the developer fields only a Stryd pod produces (fit_to_channels AT_CHANNEL_MAP)
STRYD_CHANNELS = ("@form_power", "@air_power", "@leg_spring_stiffness")
STRYD_FIT_FIELDS = ("Form Power", "Air Power", "Leg Spring Stiffness")
STRYD_MANUFACTURER_ID = 95       # FIT profile `manufacturer` enum


def _any_positive(vals) -> bool:
    if vals is None:
        return False
    for v in vals:
        try:
            if v is not None and v == v and float(v) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def is_stryd_device(manufacturer=None, product_name=None) -> bool:
    """A device_info row naming a Stryd pod (FIT manufacturer id 95 = stryd)."""
    if manufacturer == STRYD_MANUFACTURER_ID:
        return True
    txt = f"{manufacturer or ''} {product_name or ''}".lower()
    return "stryd" in txt


def classify(channels: dict, stryd_device: bool = False) -> str:
    """`channels` = {name: values} (Wko4File / FitChannels channels, values
    may be Channel objects with `.values`)."""
    def vals(name):
        c = channels.get(name)
        return getattr(c, "values", c)
    if not _any_positive(vals("power")):
        return NONE
    if stryd_device or any(_any_positive(vals(n)) for n in STRYD_CHANNELS):
        return STRYD
    return WATCH


def usable(source: Optional[str], accept_watch: bool) -> bool:
    """Power of this source may feed the power-based models."""
    return source == STRYD or (source == WATCH and accept_watch)


def label(source: Optional[str], accept_watch: bool) -> Optional[str]:
    if source == WATCH and not accept_watch:
        return UNUSED_LABEL
    return LABELS.get(source or "")


def read_setting(default: bool = False) -> bool:
    """power.accept_watch_power from the app DB (read-only); `default`
    without a DB (tests)."""
    try:
        from backend.engine.wko5expr.datasource import read_setting as rs
        v = rs(SETTING_KEY, default)
    except Exception:                       # noqa: BLE001
        return default
    return bool(v) if isinstance(v, bool) else default


def counts(sources: Iterable[str]) -> dict:
    out = {s: 0 for s in SOURCES}
    for s in sources:
        if s in out:
            out[s] += 1
    return out


def fit_stryd_device(messages: Iterable[dict]) -> bool:
    """Any device_info message (decoded field dict) naming Stryd."""
    return any(is_stryd_device(m.get("manufacturer"), m.get("product_name")) for m in messages)
