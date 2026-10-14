"""
預估 CTL／ATL／TSB and each day's planned TSS on the PMC charts (SP-219; views/training.json chart
key "plan_projection": "pmc" on 「PMC：體能 CTL・疲勞 ATL・狀況 TSB」, "tss" on 「每日 TSS（跟體能比）」).

The 圖表分析 range may end after today (the viewer's 到階段結束 / 到目標賽 presets — range_options —
or dates typed in). The chart endpoint (api/wko5views.chart) then evaluates every chart only up to
today: there is no activity after it, and tl() over empty days would draw a falling line. On a
chart with this key it adds what the stored plan says (owner 2026-10-12):

  * the days after today, each with the planned TSS of its open sessions — the 課表's own estimate
    of each session (api/plan_sessions.est_tss with plan_rates: the stored TSS, else minutes × the
    kind's TSS / h), so a day's number is the sum the calendar shows — up to the last day with a
    planned session (a race row of 0 minutes counts: the plan reaches race day) and no further,
    so no line falls after the plan ends (`inputs`)
  * the chart's own CTL / ATL of today continued with those TSS by the tl() recurrence
    (overview.project; TSB = the previous day's CTL − ATL, as the chart's own): dashed lines that
    start where the solid ones end; "pmc" a dashed TSB line too, "tss" the planned TSS as outlined
    bars (`bar_style: "hollow"`) beside the solid done ones (`apply`)
  * "pmc": each target race on a projected day (planning.Event, any grade) with its projected CTL
    and TSB that day and the TSB range its grade aims for — A +10～+20 (status.TSB_A), B −5～+5
    (TSB_B), C none: the owner's note PMC規劃比賽周期 (docs/research/late-race-entry.md §2.1;
    Palladino / TrainingPeaks, rules of thumb) — in `projection.races`; the viewer draws them and
    the 今天 line.

Not drawn (owner 2026-10-12): with an activity-type filter (the plan is every sport together, the
filtered PMC one sport's), on the % CTL variant (no CTL line to go on), after the last planned
session, and plan vs actual (the frozen weeks of engine/plan_history.py are not read here). Every
load recomputes: the plan's part is in the chart's render-cache key (wko5views.chart `_plan`), so an
edit on the 課表 page shows on the next load.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import _, fmt

MODES = ("pmc", "tss")
TSB_B = (-5.0, 5.0)              # B race: TSB −5～+5, CTL still rising (PMC規劃比賽周期; late-race-entry.md §2.1)
COLOR_TSB = "#6a5cc9"            # the dashed TSB line: the PMC's 比賽狀態 purple (views/training.json)
COLOR_TSS = "#8a8984"            # the outlined planned-TSS bars: the 每日 TSS chart's 低於 CTL grey
# the chart's own CTL / ATL series, found by expression (spaces dropped, lower case): the PMC's
# builtins, the 每日 TSS chart's tl()
CTL_EXPRS = ("ctl", "tl(tss,ctlconstant)")
ATL_EXPRS = ("atl", "tl(tss,atlconstant)")


# ---- the plan side (the chart's cache key holds what these return) ----------------------------

def load_plan():
    """The season plan (events, phases) — plan.json, not the Dataset's (empty in parity mode)."""
    from backend.engine.planning import Plan
    return Plan.load()


def plan_events() -> list:
    return list(load_plan().events)


def session_rows() -> list[dict]:
    """The stored sessions the 課表 shows (not deleted / superseded): this tenant's plan DB,
    read-only (plan_store.rate_rows: kind, day, state, minutes, tss, origin, edited)."""
    from backend.engine import plan_store as PS
    return [s for s in PS.rate_rows() if s.get("state") not in ("deleted", "superseded")]


def plan_rates(rows: list[dict]) -> dict[str, float]:
    """TSS / h per kind by the calendar's rule (api/plan_sessions.plan_rates) on the plan inputs."""
    from backend.api import plan_sessions as API
    return API.plan_rates(API._compute_inputs(), rows)


def tsb_range(grade: Optional[str]) -> Optional[tuple[float, float]]:
    """The TSB a race of this grade aims for on the day: A +10～+20, B −5～+5, C (any other) none."""
    from backend.engine.status import TSB_A
    return {"A": TSB_A, "B": TSB_B}.get(grade or "")


def inputs(today: dt.date, end: dt.date, rows: Optional[list[dict]] = None, rates: Optional[dict] = None,
           events: Optional[list] = None) -> Optional[dict]:
    """The plan's part over (today, end]: {"today", "until" (the last day with an open session),
    "days": [[date, planned TSS], ...] from tomorrow to `until` (a day without a session 0),
    "races": [{date, name, grade}] of the events on those days}; None without an open session
    after today."""
    from backend.api.plan_sessions import est_tss
    from backend.engine import plan_store as PS
    t, e = today.isoformat(), end.isoformat()
    rows = session_rows() if rows is None else rows
    ahead = [s for s in rows if s.get("state") == "active" and t < (s.get("day") or "") <= e]
    if not ahead:
        return None
    rates = plan_rates(rows) if rates is None else rates
    by_day = PS.planned_by_day(ahead, t, lambda s: est_tss(s, rates))
    if not by_day:
        return None
    until = max(by_day)
    days, d = [], today + dt.timedelta(days=1)
    while d.isoformat() <= until:
        days.append([d.isoformat(), round(by_day.get(d.isoformat(), 0.0), 2)])
        d += dt.timedelta(days=1)
    events = plan_events() if events is None else events
    races = [{"date": ev.start.isoformat(), "name": ev.name, "grade": ev.priority}
             for ev in sorted((x for x in events if x.start is not None), key=lambda x: x.start)
             if t < ev.start.isoformat() <= until]
    return {"today": t, "until": until, "days": days, "races": races}


# ---- the chart side --------------------------------------------------------------------------

def _norm(expr) -> str:
    return "".join(str(expr or "").split()).lower()


def _find(series: list[dict], exprs: tuple) -> Optional[dict]:
    return next((s for s in series if _norm(s.get("expression")) in exprs
                 and (s.get("data") or {}).get("kind") == "points"), None)


def _at(s: Optional[dict], date: str) -> Optional[float]:
    if s is None:
        return None
    for x, y in (s.get("data") or {}).get("points") or []:
        if str(x)[:10] == date:
            return None if y is None else float(y)
    return None


def _like(src: dict, name: str, pts: list) -> dict:
    """A projected series on the source series' axis and unit, dashed."""
    return {"id": None, "name": name, "type": "line", "color": src.get("color"), "y_axis": src.get("y_axis"),
            "x_axis": src.get("x_axis"), "line_style": "dash", "line_width": src.get("line_width"),
            "role": "projection", "expression": "", "unit": src.get("unit"), "x_unit": src.get("x_unit"),
            "data": {"kind": "points", "x": "date", "points": pts}}


def _race(r: dict, row: dict) -> dict:
    from backend.engine.panels.race_refs import COLOR, COLOR_OTHER
    band = tsb_range(r.get("grade"))
    grade = r.get("grade") or ""
    return {**r, "ctl": row["ctl"], "tsb": row["tsb"], "band": list(band) if band else None,
            "color": COLOR.get(grade, COLOR_OTHER),
            "text": _("{grade} 賽「{name}」：預估體能（Fitness）{ctl:.0f}、狀況（Form）{tsb:+.0f}",
                      grade=grade, name=r.get("name") or "", ctl=row["ctl"], tsb=row["tsb"]),
            "band_text": _("{grade} 賽的理想狀況（Form）：{lo:+.0f}～{hi:+.0f}", grade=grade, lo=band[0], hi=band[1])
            if band else None}


def _note(res: dict, note: str) -> str:
    return "；".join(x for x in (res.get("range_note"), note) if x)


def apply(res: dict, proj: Optional[dict], mode: str, cc: float, ac: float, end_iso: str) -> dict:
    """The rendered chart `res` (evaluated up to today) with the projection added: dashed CTL /
    ATL (and TSB on "pmc") from today's values, outlined planned-TSS bars on "tss", the race days
    on "pmc", `projection` {today, until, races}, the x axis to `end_iso` (the range asked for).
    A day-bucket chart (periods.py: 每日 TSS is one, `x_period` "day") gets its buckets to that end
    too. Unchanged when there is no projection, the chart is empty, has no CTL / ATL line or is
    bucketed by week / month / … (a day's projection has no place there)."""
    if not proj or res.get("empty") or mode not in MODES or res.get("x_period") not in (None, "day"):
        return res
    series = list(res.get("series") or [])
    ctl, atl = _find(series, CTL_EXPRS), _find(series, ATL_EXPRS)
    t = proj["today"]
    c0, a0 = _at(ctl, t), _at(atl, t)
    if c0 is None or a0 is None:
        return res
    from backend.engine.overview import project
    dates = [d for d, _x in proj["days"]]
    tss = [float(x) for _d, x in proj["days"]]
    rows = project(c0, a0, tss, cc, ac)
    add = [_like(ctl, _("{name}・預估", name=ctl.get("name") or _("體能（Fitness）")),
                 [[t, c0]] + [[d, r["ctl"]] for d, r in zip(dates, rows)]),
           _like(atl, _("{name}・預估", name=atl.get("name") or _("疲勞（Fatigue）")),
                 [[t, a0]] + [[d, r["atl"]] for d, r in zip(dates, rows)])]
    if mode == "pmc":
        y = (dt.date.fromisoformat(t) - dt.timedelta(days=1)).isoformat()
        cy, ay = _at(ctl, y), _at(atl, y)
        first = [[t, cy - ay]] if cy is not None and ay is not None else []
        add.append({**_like(ctl, _("狀況（Form）・預估"), first + [[d, r["tsb"]] for d, r in zip(dates, rows)]),
                    "color": COLOR_TSB, "line_width": None})
        by_date = dict(zip(dates, rows))
        races = [_race(r, by_date[r["date"]]) for r in proj.get("races") or [] if r.get("date") in by_date]
        note = _("今天以後是照課表的預估（虛線），畫到最後一堂排好的課（{until}）", until=fmt.date(proj["until"]))
    else:
        bar = next((s for s in series if s.get("type") in ("bar", "stackedbar")), ctl)
        add.insert(0, {**_like(bar, _("預估負荷（Load）"), [[d, x] for d, x in zip(dates, tss)]),
                       "type": "bar", "bar_style": "hollow", "color": COLOR_TSS, "line_style": "solid"})
        races = []
        note = _("今天以後是照課表的預估（空心長條、虛線），畫到最後一堂排好的課（{until}）",
                 until=fmt.date(proj["until"]))
    out = {**res, "series": series + add, "end": end_iso,
           "projection": {"today": t, "until": proj["until"], "races": races}, "range_note": _note(res, note)}
    if res.get("x_period") == "day" and res.get("buckets"):
        # the viewer draws a day-bucket chart on its buckets (one category a day): on to the end asked
        bk, d = list(res["buckets"]), dt.date.fromisoformat(res["buckets"][-1]) + dt.timedelta(days=1)
        while d.isoformat() <= end_iso:
            bk.append(d.isoformat())
            d += dt.timedelta(days=1)
        out["buckets"] = bk
    return out


def filtered_note(res: dict) -> dict:
    """An activity-type filter is on: the chart as is (to today), saying why there is no projection."""
    return {**res, "range_note": _note(res, _("篩選活動類型時不畫預估：課表是所有運動合計"))}


def none_note(res: dict) -> dict:
    """A range into the future but no open session after today: the chart to today, and why."""
    return {**res, "range_note": _note(res, _("今天以後課表沒有排課，沒有預估可畫"))}


# ---- the viewer's future presets --------------------------------------------------------------

def range_options(plan, today: dt.date) -> dict:
    """「到階段結束」/「到目標賽」 (wko5_viewer.html FUTURE_PRESETS): {today, phase: {kind, label,
    end} — the current phase when it ends after today, else None —, race: {date, name, grade} — the
    next A race after today, else the next B race, else None}."""
    from backend.engine import planning
    try:
        p = planning.phase_on(plan, today)
    except Exception:                       # noqa: BLE001 — no phase preset then
        p = None
    ph = {"kind": p.kind, "label": _(p.label), "end": p.end} if p is not None and p.end > today.isoformat() else None
    ahead = sorted((e for e in plan.events if e.start is not None and e.start > today), key=lambda e: e.start)
    nxt = next((e for e in ahead if e.priority == "A"), None) or next((e for e in ahead if e.priority == "B"), None)
    race = {"date": nxt.start.isoformat(), "name": nxt.name, "grade": nxt.priority} if nxt else None
    return {"today": today.isoformat(), "phase": ph, "race": race}
