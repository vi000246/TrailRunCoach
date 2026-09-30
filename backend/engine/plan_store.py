"""
The stored, editable plan (table plan_sessions). This website's plan is the
source of truth: sessions are generated into the table once, the user edits
them, and reconcile() (engine/reconcile.py) refreshes the rest from the real
state on demand and before every COROS push.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import PlanSession
from backend.engine import reconcile as R

KINDS = {"easy": "輕鬆跑", "long": "長時間", "quality": "強度課", "test": "測試",
         "hike": "健行／登山", "strength": "肌力"}
EDITABLE = ("day", "kind", "title", "minutes", "target", "detail")
DEFAULT_TITLES = {"easy": "輕鬆跑", "long": "長時間輕鬆", "quality": "閾值 3×10 分", "test": "CP 測試 3 分 + 12 分",
                  "hike": "健行", "strength": "肌力（下肢單腳＋核心）"}


class PlanError(ValueError):
    """Bad edit (unknown session, bad field)."""


def to_dict(r: PlanSession) -> dict:
    done_by = None
    if r.done_by:
        try:
            done_by = json.loads(r.done_by)
        except ValueError:
            done_by = None
    return {"uid": r.uid, "week_start": r.week_start, "gen_key": r.gen_key, "day": r.day, "kind": r.kind,
            "title": r.title, "minutes": r.minutes or 0, "target": r.target or "", "detail": r.detail or "",
            "source": r.source or "", "tss": r.tss or 0.0, "origin": r.origin, "edited": bool(r.edited),
            "provisional": bool(r.provisional), "state": r.state, "done_by": done_by, "note": r.note}


def _fill(r: PlanSession, d: dict) -> None:
    for k in ("week_start", "gen_key", "day", "kind", "title", "minutes", "target", "detail", "source",
              "tss", "origin", "edited", "provisional", "state", "note"):
        setattr(r, k, d.get(k))
    r.minutes = int(d.get("minutes") or 0)
    r.done_by = json.dumps(d["done_by"], ensure_ascii=False) if d.get("done_by") else None
    r.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


async def _rows(db: AsyncSession, athlete_id: int) -> dict[str, PlanSession]:
    res = await db.execute(select(PlanSession).where(PlanSession.athlete_id == athlete_id))
    return {r.uid: r for r in res.scalars().all()}


async def load(db: AsyncSession, athlete_id: int = 1, start: Optional[str] = None,
               end: Optional[str] = None) -> list[dict]:
    out = [to_dict(r) for r in (await _rows(db, athlete_id)).values()]
    if start or end:
        out = [s for s in out if s.get("day") and (not start or s["day"] >= start) and (not end or s["day"] <= end)]
    return sorted(out, key=lambda s: (s.get("day") or "9999", s["kind"] == "strength", s["uid"]))


async def save(db: AsyncSession, new: list[dict], athlete_id: int = 1) -> None:
    rows = await _rows(db, athlete_id)
    keep = set()
    for d in new:
        r = rows.get(d["uid"])
        if r is None:
            r = PlanSession(athlete_id=athlete_id, uid=d["uid"])
            db.add(r)
        _fill(r, d)
        keep.add(d["uid"])
    for uid, r in rows.items():
        if uid not in keep:
            await db.delete(r)
    await db.commit()


def gen_weeks(inputs: dict) -> list[dict]:
    cur = inputs["cur"]
    first = {"start": cur["week"]["start"], "mode": cur.get("mode"), "provisional": False,
             "sessions": cur["sessions"]}
    return [first] + [{"start": w["start"], "mode": w["mode"], "provisional": w["provisional"],
                       "sessions": w["sessions"]} for w in inputs.get("weeks", [])]


async def plan_reconcile(db: AsyncSession, inputs: dict, apply: bool, athlete_id: int = 1):
    """(new sessions, changes). `inputs`: cur (week_plan()), weeks (projection),
    activities, today, horizon_end."""
    stored = await load(db, athlete_id)
    new, changes = R.reconcile(stored, gen_weeks(inputs), inputs.get("activities") or [],
                               inputs["today"], inputs.get("horizon_end"), covered=inputs.get("covered"))
    if apply:
        await save(db, new, athlete_id)
    return new, changes


async def initialized(db: AsyncSession, week_start: str, athlete_id: int = 1) -> bool:
    res = await db.execute(select(PlanSession.id).where(PlanSession.athlete_id == athlete_id,
                                                        PlanSession.week_start == week_start).limit(1))
    return res.first() is not None


async def has_leftovers(db: AsyncSession, before: str, athlete_id: int = 1) -> bool:
    """Active sessions on days before `before` (e.g. last week's, on a Monday)."""
    res = await db.execute(select(PlanSession.id).where(PlanSession.athlete_id == athlete_id,
                                                        PlanSession.state == "active",
                                                        PlanSession.day < before).limit(1))
    return res.first() is not None


def _clean(patch: dict, today: str) -> dict:
    out = {}
    for k in EDITABLE:
        if k not in patch:
            continue
        v = patch[k]
        if k == "day":
            try:
                v = dt.date.fromisoformat(str(v)[:10]).isoformat()
            except ValueError:
                raise PlanError(f"日期格式不對：{v!r}")
            if v < today:
                raise PlanError("不能排到過去的日子")
        elif k == "kind":
            if v not in KINDS:
                raise PlanError(f"不支援的類型：{v!r}")
        elif k == "minutes":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise PlanError("分鐘要是數字")
            if not 0 <= v <= 1440:
                raise PlanError("分鐘要在 0–1440")
        else:
            v = str(v or "").strip()[:500]
            if k == "title" and not v:
                raise PlanError("標題不能空白")
        out[k] = v
    return out


async def edit(db: AsyncSession, uid: str, patch: dict, today: str, athlete_id: int = 1) -> dict:
    rows = await _rows(db, athlete_id)
    r = rows.get(uid)
    if r is None or r.state != "active":
        raise PlanError("找不到這堂課（或已經完成／錯過）")
    d = to_dict(r)
    ch = _clean(patch, today)
    if not ch:
        return d
    new_week = R.monday_of(ch["day"]) if "day" in ch else d["week_start"]
    if new_week != d["week_start"] and d["origin"] == "auto":
        # moved to another week: leave a tombstone so that week isn't regenerated
        # into a duplicate, and carry the session over as the user's own
        tomb = {**d, "uid": R.new_uid(), "state": "deleted", "note": "移到別週"}
        t = PlanSession(athlete_id=athlete_id, uid=tomb["uid"])
        db.add(t)
        _fill(t, tomb)
        d.update(origin="custom", gen_key=None)
    d.update(ch)
    d.update(edited=True, week_start=new_week, provisional=False)
    _fill(r, d)
    await db.commit()
    return d


async def add(db: AsyncSession, data: dict, today: str, athlete_id: int = 1) -> dict:
    data = dict(data)
    data.setdefault("kind", "easy")
    data.setdefault("title", DEFAULT_TITLES.get(data.get("kind"), "自訂"))
    if "day" not in data:
        raise PlanError("要選日期")
    ch = _clean(data, today)
    d = {"uid": R.new_uid(), "week_start": R.monday_of(ch["day"]), "gen_key": None, "origin": "custom",
         "edited": True, "provisional": False, "state": "active", "done_by": None, "note": None,
         "source": "", "tss": 0.0, "target": "", "detail": "", "minutes": 45, **ch}
    r = PlanSession(athlete_id=athlete_id, uid=d["uid"])
    db.add(r)
    _fill(r, d)
    await db.commit()
    return d


async def delete(db: AsyncSession, uid: str, athlete_id: int = 1) -> dict:
    rows = await _rows(db, athlete_id)
    r = rows.get(uid)
    if r is None:
        raise PlanError("找不到這堂課")
    d = to_dict(r)
    if r.origin == "auto":
        r.state = "deleted"                  # tombstone: regeneration won't bring it back
        d["state"] = "deleted"
    else:
        await db.delete(r)
        d["state"] = "removed"
    await db.commit()
    return d


def plan_summary(ss: list[dict], week_start: str, today: str, ctl0: float, atl0: float,
                 cc: float, ac: float, horizon_end: Optional[str] = None) -> dict:
    """Week targets and the CTL/ATL projection from the stored plan, so edits show
    in the progress bars and the PMC. Target = active + done sessions of the week
    (missed ones were re-planned or dropped); time excludes strength, like
    week_plan(). The projection continues today's CTL/ATL with the planned TSS
    of each later day up to the horizon."""
    from backend.engine.overview import project
    week_end = (dt.date.fromisoformat(week_start) + dt.timedelta(days=6)).isoformat()
    live = [s for s in ss if s["state"] in ("active", "done") and s.get("day")]
    wk = [s for s in live if week_start <= s["day"] <= week_end]
    hours = sum(s["minutes"] or 0 for s in wk if s["kind"] != "strength") / 60.0
    tss = sum(float(s.get("tss") or 0.0) for s in wk)
    end = max(week_end, horizon_end or week_end)
    days, d = [], dt.date.fromisoformat(today) + dt.timedelta(days=1)
    while d.isoformat() <= end:
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    by_day: dict[str, float] = {}
    for s in live:
        if s["state"] == "active" and s["day"] > today:
            by_day[s["day"]] = by_day.get(s["day"], 0.0) + float(s.get("tss") or 0.0)
    rows = [{"date": x, **p} for x, p in zip(days, project(ctl0, atl0, [by_day.get(x, 0.0) for x in days], cc, ac))]
    wrows = [r for r in rows if r["date"] <= week_end]
    ctl_end = wrows[-1]["ctl"] if wrows else ctl0
    atl_end = wrows[-1]["atl"] if wrows else atl0
    return {"hours": hours, "tss": tss, "projection": rows, "ctl_end": ctl_end, "atl_end": atl_end,
            "tsb_next": ctl_end - atl_end}


def push_dict(s: dict) -> dict:
    """A stored session in the shape coros_workouts expects (key = uid)."""
    return {"id": s["uid"], "key": s["uid"], "week_start": s["week_start"], "kind": s["kind"],
            "title": s["title"], "minutes": s["minutes"], "target": s.get("target") or "",
            "detail": s.get("detail") or "", "source": s.get("source") or "", "day": s.get("day"),
            "done": s["state"] == "done"}
