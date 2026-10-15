"""
Garmin attribution of activities that came through intervals.icu (SP-346).

intervals.icu's API terms §1.1: "if your application displays information derived from
Garmin-sourced data, you must display attribution to Garmin in the form and manner required by
Garmin's brand guidelines." Garmin Developer API Brand Guidelines (V 6.30.2025), the parts used here:

  * TITLE-LEVEL OR PRIMARY DISPLAYS / SECONDARY SCREENS: "must include a “Garmin [device model]”
    attribution … If the device model is not provided or unknown via the API, list Garmin as the
    data source." "Position the Garmin attribution directly beneath or adjacent to the primary title
    or heading of the data view … Never bury the Garmin attribution in tooltips, footnotes or
    expandable containers." "For multi-entry displays, you can apply the attribution globally —
    such as in a header — or per entry." → `device_label` beside the activity's title (activity
    page, chart viewer) and per row of the activity list
  * COMBINED OR DERIVED DATA: "All uses of Garmin device-sourced data as an input to analytics,
    algorithms, machine learning models, artificial intelligence … must include a Garmin
    attribution." Acceptable: "Insights derived in part from Garmin device-sourced data." →
    `DERIVED` in the data an AI reads (the debug API's activity answer, the chart viewer's
    activity JSON)
  * DOWNSTREAM AND EXPORTED DATA: "In APIs or webhook transfers, receiving systems must preserve
    attribution" → the same fields in the API answers

Only text, no Garmin logo ("Do not use the Garmin tag logo in avatars, badges or unrelated
imagery"). An activity is Garmin's when intervals.icu says it came from Garmin Connect
(Activity.source GARMIN_CONNECT) or its device_name names Garmin.
"""
from __future__ import annotations

from typing import Optional

from backend.i18n import N_, _

GARMIN = "Garmin"
# the brand guidelines' acceptable sample; the English catalog keeps its exact words
DERIVED = N_("部分分析來自 Garmin 裝置的資料")
NOT_ORIGINAL = N_("不是原始檔")


def is_garmin(device_name: Optional[str], upstream_source: Optional[str] = None) -> bool:
    return (upstream_source or "").upper() == "GARMIN_CONNECT" or "garmin" in (device_name or "").lower()


def device_label(device_name: Optional[str]) -> str:
    """“Garmin [device model]”, or “Garmin” when the model is unknown."""
    name = " ".join(str(device_name or "").split())
    if not name:
        return GARMIN
    if name.lower().startswith("garmin"):
        rest = name[len("garmin"):].strip(" -_")
        return f"{GARMIN} {rest}" if rest else GARMIN
    return f"{GARMIN} {name}"


def mark(device_name: Optional[str], upstream_source: Optional[str]) -> Optional[dict]:
    """What an activity row shows about where its file came from, or None for a plain one:
    {garmin: “Garmin [model]” | None, derived: the AI sentence | None, not_original: bool,
    not_original_text}. not_original: Huawei's API has no FIT export ("The Huawei API does not
    support fit file export."), so a Huawei activity's file is intervals.icu's, not the watch's."""
    garmin = is_garmin(device_name, upstream_source)
    not_original = (upstream_source or "").upper() == "HUAWEI"
    if not garmin and not not_original:
        return None
    return {"garmin": device_label(device_name) if garmin else None,
            "derived": _(DERIVED) if garmin else None,
            "not_original": not_original,
            "not_original_text": _(NOT_ORIGINAL) if not_original else None}
