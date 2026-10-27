"""
Any two activities compared (SP-625; docs/spec/route-progress.spec.md › Two activities).

    GET /api/v1/app/activity-compare/page              the page (?a=&b= workout indexes)
    GET /api/v1/app/activity-compare/activities        the picker's list (?race=1: the races only)
    GET /api/v1/app/activity-compare?a=&b=            both activities' summary, splits, overlay,
                                                       gradient groups (engine/activity_compare.py)
                                                       and `aligned`: the route / segment both ran,
                                                       compared point by point by
                                                       GET /api/v1/routes/{id}/compare

Same course = both activities are runs of one route (same direction, started
within the route's start tolerance — what the route comparison needs), else
both did one segment covering ≥ ALIGN_SHARE of the shorter activity. The
other segments both did are listed (`shared`), each one click from its own
point-by-point comparison.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.engine import activity_compare as AC
from backend.i18n.pages import render_page

router = APIRouter(prefix="/api/v1/app/activity-compare", tags=["activity-compare"])

ALIGN_SHARE = 0.8      # 推估 a segment this much of the shorter activity counts as "the same course"


def _ds():
    from backend.api.wko5views import _dataset
    return _dataset()


@router.get("/page", include_in_schema=False)
def page():
    return render_page("actcompare")


@router.get("/activities")
def activities(race: bool = False):
    """Every activity with a distance, newest first: index, start, name, sport,
    distance, duration and `race` (the user's own 「比賽」 mark, as the activity
    list's 「只看比賽」 filter reads it)."""
    from backend.engine import activity_tags as AT
    ds = _ds()
    rows = AT.load()
    out = []
    for w in reversed(ds.workouts):
        m = w.metrics
        if not m.get("distance"):
            continue
        tag = AT.find(rows, w.entry.start, w.entry.file) if rows else None
        is_race = AT.user_type(tag) == "race"
        if race and not is_race:
            continue
        out.append({"index": w.idx, "start": w.entry.start.isoformat(), "name": AT.name_of(tag),
                    "sport": w.sport, "sport_type": w.sport_type, "distance": m.get("distance"),
                    "duration": m.get("duration"), "climbing": m.get("climbing"), "race": is_race})
    return out


def _series(ds, idx: int, power: bool = True) -> AC.Series:
    """`power` False (SP-954: an activity without trusted power, or no power meter now): no power line,
    no average power."""
    ch = lambda n: ds.channel(idx, n)
    elev = ch("_elevation")
    if elev is None or not (elev == elev).any():
        elev = ch("elevation")
    return AC.Series(ch("elapsedtime"), ch("elapseddistance"), elev, ch("heartrate"),
                     ch("power") if power else None)


def _meta(ds, w) -> dict:
    from backend.engine import activity_tags as AT
    m = w.metrics
    rows = AT.load()
    tag = AT.find(rows, w.entry.start, w.entry.file) if rows else None
    return {"index": w.idx, "start": w.entry.start.isoformat(), "name": AT.name_of(tag),
            "sport": w.sport, "sport_type": w.sport_type, "file": w.entry.file,
            "race": AT.user_type(tag) == "race",
            "duration": m.get("duration"), "distance": m.get("distance"),
            "climbing": m.get("climbing"), "load": m.get("tss")}


@router.get("")
def compare(a: int, b: int):
    ds = _ds()
    n = len(ds.workouts)
    if not (0 <= a < n and 0 <= b < n):
        raise HTTPException(404, "workout not found")
    if a == b:
        raise HTTPException(400, "pick two different activities")
    wa, wb = ds.workouts[a], ds.workouts[b]
    from backend import membership as MS
    for w in (wa, wb):
        MS.require_workout_age(ds, w)        # SP-713: like the charts — 90 days free, 365 / all paid
    ma, mb = _meta(ds, wa), _meta(ds, wb)
    # SP-954: each activity's power by THAT day's rule (engine/power_display.py): shown when it was a trusted
    # Stryd power and there is a power meter now (stale or fresh — no % CP on this page either way)
    from backend.engine import power_display as PWD
    pwv = PWD.View(ds)
    ma["pw"], mb["pw"] = pwv.activity(wa), pwv.activity(wb)
    out = AC.compare(_series(ds, a, ma["pw"]["mode"] != PWD.NONE), _series(ds, b, mb["pw"]["mode"] != PWD.NONE),
                     ma, mb)
    out["a"], out["b"] = ma, mb
    out.update(same_course(wa, wb))
    return out


def same_course(wa, wb) -> dict:
    """{aligned, shared, aligned_note}: the route / segment both activities ran
    (routes index, backend/api/routes.py), for the point-by-point comparison."""
    from backend.api import routes as RA
    from backend.engine.activity_key import ByStartDict
    res = {"aligned": None, "shared": [], "aligned_note": None}
    try:
        RA._ensure_fresh()
        view = RA._index()
    except Exception:                    # noqa: BLE001 — the comparison works without routes
        return res
    if view is None:
        return res
    files = ByStartDict({e["file"]: {"start": e.get("start")} for it in view["items"].values()
                         for e in it["efforts"] if e.get("file")})
    fa = files.key_for(wa.entry.file, wa.entry.start)
    fb = files.key_for(wb.entry.file, wb.entry.start)
    if not fa or not fb or fa == fb:
        return res
    names = RA._names(view)
    short_km = min(x for x in (wa.metrics.get("distance"), wb.metrics.get("distance"), 1e9) if x)
    routes, segs = [], []
    for it in view["items"].values():
        if it["n_activities"] < 2:
            continue
        ea = next((e for e in it["efforts"] if e["file"] == fa), None)
        eb = next((e for e in it["efforts"] if e["file"] == fb), None)
        if ea is None or eb is None:
            continue
        row = {"id": it["id"], "kind": it["kind"], "direction": it["direction"],
               "name": names.get(it["id"]) or it["auto_name"], "length_m": it["length_m"],
               "a": ea["id"], "b": eb["id"], "time_key": it["time_key"],
               "a_s": ea.get(it["time_key"]), "b_s": eb.get(it["time_key"])}
        (routes if it["kind"] == "route" else segs).append(row)
    routes.sort(key=lambda r: -r["length_m"])
    segs.sort(key=lambda r: -r["length_m"])
    res["shared"] = segs
    for r in routes:
        try:
            RA.compare(r["id"], r["a"], r["b"])        # the route comparison's own rules decide
        except HTTPException as ex:
            res["aligned_note"] = ex.detail if isinstance(ex.detail, str) else None
            continue
        res["aligned"] = r
        return res
    if segs and segs[0]["length_m"] / 1000.0 >= ALIGN_SHARE * short_km:
        res["aligned"] = segs[0]
    return res
