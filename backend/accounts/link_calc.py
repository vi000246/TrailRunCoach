"""
疊石 Cairn reads this person's saved calculations and time curves (SP-654 = SP-570 TRC-4; the
"pull" half of SP-538). GET /api/v1/link/cairn/curves?gpx_sha256= (api/link.py) runs in the token
owner's tenant (tenancy_mw's /api/v1/link/ step): never anyone else's data, whatever the caller sends.

  saved_curves(sha)   every saved race calculation (engine/race_calc_store.py, one per plan event)
                      whose cairn-curve is for that GPX: {event_id, uid, name, date, saved_at,
                      curve}, newest first. The form's inputs and the full result never leave.

The curve is SP-653's: `backend.engine.racepower.cairn_curve.for_saved(eid) -> dict`, the
cairn-curve v1 of the calculation saved for plan event `eid` in the current tenant (its
`gpx_sha256`: the hash of the GPX file, the one Cairn holds); it raises CurveError (.code,
.status) when there is none to give, and that calculation is left out of the list.

A paid plan (SP-570 Q1-5: C goes with the paid tier): without one 402 PLAN_REQUIRED, which Cairn
turns into its upgrade hint (require_plan; links.FEATURES says which plan).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Optional

from fastapi import HTTPException

log = logging.getLogger("backend.links")

_SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_CALCS = 200                  # saved calculations looked at (one per plan event)


def clean_sha256(v: Optional[str]) -> Optional[str]:
    s = (v or "").strip().lower()
    return s if _SHA.match(s) else None


def require_plan(scope: str) -> None:
    """402 PLAN_REQUIRED unless the current person's plan holds the feature's (links.FEATURES)."""
    from backend import membership as MS
    from backend.accounts import links as LK
    need = (LK.FEATURES.get(scope) or {}).get("plan")
    if not need:
        return
    m = MS.member()
    if m.role in (MS.SUBSCRIBER, MS.OWNER):
        return
    raise HTTPException(402, {"code": "PLAN_REQUIRED", "plan": need, "role": m.role, "upgrade": m.role == MS.USER})


def curve_for_saved(eid: str) -> Optional[dict]:
    """SP-653's curve of the calculation saved for `eid` (current tenant); None when that
    calculation has none to give (CurveError: NO_RESULT, NO_GPX, GPX_CHANGED, GPX_MISMATCH,
    NO_TRACK, NO_SEGMENTS, BAD_ID — a stale or GPX-less calculation is simply not listed)."""
    from backend.engine.racepower import cairn_curve as CC
    try:
        return CC.for_saved(eid)
    except CC.CurveError as e:
        log.debug("no cairn curve for a saved calculation: %s", e.code)
        return None


def _saved_ids() -> list[tuple[str, Optional[str]]]:
    """(event_id, saved_at) of every saved calculation in the current tenant, newest first."""
    from backend.engine import race_calc_store as RC
    p = RC._db()
    if p is None or not p.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = con.execute(f"SELECT event_id, saved_at FROM {RC.TABLE} ORDER BY saved_at DESC, id DESC "
                               "LIMIT ?", (MAX_CALCS,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:                   # no table yet
        return []
    return [(r[0], r[1]) for r in rows]


def _events() -> dict:
    """The plan's events by id (name, date), {} when the plan can't be read."""
    try:
        from backend.engine.planning import Plan
        return {e.id: e for e in Plan.load().events}
    except Exception:                       # noqa: BLE001 — a name is only a label here
        return {}


def saved_curves(sha: str) -> list[dict]:
    from backend.engine import race_calc_store as RC
    out = []
    events = None
    for eid, saved_at in _saved_ids():
        try:
            curve = curve_for_saved(eid)
        except Exception as e:              # noqa: BLE001 — one bad calculation never hides the others
            log.warning("cairn curve of a saved calculation failed: %s", type(e).__name__)
            continue
        if not isinstance(curve, dict) or str(curve.get("gpx_sha256") or "").lower() != sha:
            continue
        if events is None:
            events = _events()
        ev = events.get(eid)
        row = RC.get(eid) or {}
        date = getattr(ev, "date", None)
        out.append({"event_id": eid, "uid": row.get("uid"), "name": getattr(ev, "name", None),
                    "date": date.isoformat() if hasattr(date, "isoformat") else date,
                    "saved_at": saved_at, "curve": curve})
    return out
