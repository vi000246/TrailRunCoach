"""
The mFTP line on 能力 › 跑步功率曲線 (views/training.json chart key `"cp_ref": "mftp"`; SP-465).

A horizontal dashed line at the CP the 課表 uses on the chart's last day — zones.cp_in_effect, the
same value and source as the settings page (api/plan.py _effective) and the plan's power targets:
a plan CP test, else the dated setting, else the Stryd PD fit (WKO5's mFTP, SP-449). Labelled at
its right end with the value and the date it comes from (「mFTP 191 W · 10/08」; a plan test reads
「CP 200 W · 測試 10/10」); the hover says where it comes from. No CP (not enough Stryd runs to fit)
→ no line, the chart as it was. With the line, the viewer's optional 「mFTP 線」 toggle (the range's
own PD fit, render.mftp_plateau) is not offered on this chart.

The value is part of the chart's render-cache key (wko5views.chart: `_cp_ref`), so a new CP test or
a new fit is never answered with the old line.
"""
from __future__ import annotations

from typing import Optional

from backend.i18n import _

COLOR = "#2a78d6"            # blue: apart from the MMP (yellow), the new bests (orange) and the PD model (red)


def line(ds, end_day, plan=None) -> Optional[dict]:
    """{value, date, kind, source} of the CP in effect on `end_day`, None without one. `plan`: the
    season plan (the tenant's): its CP test first, as the settings page reads it (api/plan.py
    _effective) — in WKO5 對照模式 the chart's Dataset has an empty plan of its own."""
    import math
    from backend.engine.zones import cp_in_effect
    from backend.files.wko5_athlete import day_to_date
    if plan is not None:
        day = day_to_date(int(math.floor(end_day))).isoformat()
        rows = sorted((t for t in plan.thresholds if t.cp is not None and t.date[:10] <= day), key=lambda t: t.date)
        if rows:
            d = rows[-1].date[:10]
            return {"value": round(float(rows[-1].cp), 1), "date": d, "kind": "test",
                    "source": _("你的測試 {day}", day=d)}
    try:
        ci = cp_in_effect(ds, int(math.floor(end_day)))
    except Exception:                       # noqa: BLE001 — no data / no estimate yet: no line
        return None
    v = ci.get("value")
    if not v:
        return None
    return {"value": round(float(v), 1), "date": ci.get("date"), "kind": ci.get("kind"),
            "source": ci.get("source")}


def _md(day: Optional[str]) -> Optional[str]:
    return f"{day[5:7]}/{day[8:10]}" if day and len(day) >= 10 else None


def name(ref: dict) -> str:
    w = f"{ref['value']:.0f}"
    md = _md(ref.get("date"))
    if ref.get("source") == "對照資料夾的 mFTP":
        # WKO5 對照模式: WKO5's own mFTP keeps its name (SP-391)
        return _("mFTP {w} W · {md}", w=w, md=md) if md else _("mFTP {w} W", w=w)
    if ref.get("kind") == "estimate":
        return _("模型閾值功率 {w} W · {md}", w=w, md=md) if md else _("模型閾值功率 {w} W", w=w)
    if ref.get("kind") == "test":
        return _("CP {w} W · 測試 {md}", w=w, md=md) if md else _("CP {w} W · 測試", w=w)
    return _("CP {w} W · {md}", w=w, md=md) if md else _("CP {w} W", w=w)


def tip(ref: dict) -> str:
    return _("課表與設定頁用的就是這個 CP：{w} W\n來源：{src}", w=f"{ref['value']:.1f}",
             src=ref.get("source") or "—")


def apply(res: dict, ref: Optional[dict]) -> dict:
    """The rendered chart with the line appended (role "cp_ref"); unchanged without a CP."""
    if not ref:
        return res
    series = list(res.get("series") or [])
    like = next(iter(series), {})
    series.append({"type": "line", "y_axis": like.get("y_axis") or "WATTS", "unit": like.get("unit"),
                   "x_unit": like.get("x_unit"), "expression": "", "role": "cp_ref", "name": name(ref),
                   "color": COLOR, "line_style": "dash", "line_width": "medium", "label_end": True,
                   "data": {"kind": "hline", "y": ref["value"]}, "tip": tip(ref)})
    # the optional 「mFTP 線」 (render.mftp_plateau: the PD fit of the range shown, off by default) is
    # dropped here: two lines both called mFTP with different numbers would read as a contradiction
    return {**res, "series": series, "cp_ref": ref, "plateau": None}
