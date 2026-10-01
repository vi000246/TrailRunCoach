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
         "hike": "健行／登山", "strength": "肌力", "heat_passive": "被動熱適應",
         # 課表待確認 (engine/plan_auto.py): a reminder pushed to the watch, not a
         # training session — never done / missed, no TSS, no compliance
         "notice": "課表待確認"}
NOT_LOAD = ("notice",)
EDITABLE = ("day", "kind", "title", "minutes", "target", "detail", "terrain", "distance_km", "climb_m")
TERRAINS = ("road", "trail", "hike")
DEFAULT_TITLES = {"easy": "輕鬆跑", "long": "長時間輕鬆", "quality": "閾值 3×10 分", "test": "CP 測試 20 分全力",
                  "hike": "健行", "strength": "肌力（下肢單腳＋核心）"}


def _test_default(data: dict) -> None:
    """A new custom test session follows 課表偏好 CP 測試方式 (title, minutes,
    target, detail, protocol); `race` has no session of its own -> quick."""
    from backend.engine import cp_protocols as CPP
    from backend.engine import plan_prefs as PP
    from backend.engine.aet_test import is_aet_session
    if is_aet_session(data):
        return                                   # the AeT test keeps its own title / protocol "aet"
    proto = data.get("protocol") or CPP.protocol_of({"title": data.get("title")}) or PP.load().cp_test_protocol
    t = CPP.session_for(proto) or CPP.session_for(CPP.DEFAULT)
    data["protocol"] = t["protocol"]
    for k in ("title", "minutes", "target", "detail", "source", "tss"):
        data.setdefault(k, t[k])


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
            "provisional": bool(r.provisional), "state": r.state, "done_by": done_by, "note": r.note,
            "terrain": r.terrain, "distance_km": r.distance_km, "climb_m": r.climb_m,
            "protocol": r.protocol}


def _fill(r: PlanSession, d: dict) -> None:
    for k in ("week_start", "gen_key", "day", "kind", "title", "minutes", "target", "detail", "source",
              "tss", "origin", "edited", "provisional", "state", "note", "terrain", "distance_km", "climb_m",
              "protocol"):
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


def blocked_map(inputs: dict) -> dict:
    """ISO day -> label of the 不排課日期 in `inputs` (list of {start, end, label})."""
    from backend.engine import blackouts as BL
    return {d: b.label for d, b in BL.blocked(BL.from_list(inputs.get("blackouts") or [])).items()}


def reconcile_with_adapt(stored: list[dict], inputs: dict, decisions: Optional[dict] = None,
                         adjustments: Optional[list] = None) -> tuple[list[dict], list[dict]]:
    """reconcile() on the generator's weeks after engine/adapt.py's adjustments
    (when `inputs["adapt"]` is there and enabled — _compute_inputs adds it;
    plan.auto.enabled off = the plain generator). Pure."""
    from backend.engine import adapt as A
    prefs = inputs.get("prefs") or {}
    days = prefs.get("days") if prefs and not all(prefs.get("days") or [True]) else None
    blocked = blocked_map(inputs)
    gw = gen_weeks(inputs)
    ctx = inputs.get("adapt")
    adj: list[dict] = []
    notes: dict = {}
    if ctx and ctx.get("enabled", True):
        cur = inputs.get("cur") or {}
        load = cur.get("load") or {}
        gw, adj, notes = A.adapt(gw, stored, {
            "today": inputs["today"], "first_free": ctx.get("first_free"), "blocked": blocked,
            "allowed_days": days, "thresholds": inputs.get("thresholds") or {}, "mode": cur.get("mode"),
            "load": {"tsb": ctx.get("tsb", load.get("tsb_today")), "ramp": ctx.get("ramp")},
            "reviews": ctx.get("reviews") or {}})
    new, changes = R.reconcile(stored, gw, inputs.get("activities") or [],
                               inputs["today"], inputs.get("horizon_end"), covered=inputs.get("covered"),
                               blocked=blocked, allowed_days=days, decisions=decisions)
    if ctx and ctx.get("enabled", True):
        A.apply_notes(new, notes)
        A.annotate(changes, adj, new, stored)
    if adjustments is not None:
        adjustments.extend(adj)
    return new, changes


async def plan_reconcile(db: AsyncSession, inputs: dict, apply: bool, athlete_id: int = 1,
                         decisions: Optional[dict] = None, adjustments: Optional[list] = None):
    """(new sessions, changes). `inputs`: cur (week_plan()), weeks (projection),
    activities, today, horizon_end, blackouts (不排課日期), prefs, adapt
    (engine/adapt.py context). `decisions`: {uid: move | delete} for edited
    sessions on a blocked day. `adjustments`: filled with adapt()'s list."""
    stored = await load(db, athlete_id)
    new, changes = reconcile_with_adapt(stored, inputs, decisions, adjustments)
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
            if v not in KINDS or v in NOT_LOAD:
                raise PlanError(f"不支援的類型：{v!r}")
        elif k == "minutes":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise PlanError("分鐘要是數字")
            if not 0 <= v <= 1440:
                raise PlanError("分鐘要在 0–1440")
        elif k == "terrain":
            v = v or None
            if v is not None and v not in TERRAINS:
                raise PlanError(f"不支援的地形：{v!r}")
        elif k in ("distance_km", "climb_m"):
            if v is not None and v != "":
                try:
                    v = round(float(v), 2 if k == "distance_km" else 0)
                except (TypeError, ValueError):
                    raise PlanError("距離／爬升要是數字")
                if not 0 <= v <= (500 if k == "distance_km" else 20000):
                    raise PlanError("距離要在 0–500 km、爬升 0–20000 m")
            else:
                v = None
        else:
            v = str(v or "").strip()[:500]
            if k == "title" and not v:
                raise PlanError("標題不能空白")
        out[k] = v
    if patch.get("tss") is not None:
        # the estimate the 課表 dialog shows (minutes × the kind's TSS per hour)
        try:
            t = float(patch["tss"])
        except (TypeError, ValueError):
            raise PlanError("TSS 要是數字")
        if not 0 <= t <= 2000:
            raise PlanError("TSS 要在 0–2000")
        out["tss"] = round(t, 1)
    return out


def _not_blocked(day: str, blocked: Optional[dict]) -> None:
    if blocked and day in blocked:
        lb = blocked[day]
        raise PlanError(f"{int(day[5:7])}/{int(day[8:10])} 是不排課日期{f'（{lb}）' if lb else ''}，不能排課")


async def edit(db: AsyncSession, uid: str, patch: dict, today: str, athlete_id: int = 1,
               blocked: Optional[dict] = None) -> dict:
    """`blocked`: ISO day -> label of the 不排課日期; moving onto one is refused."""
    rows = await _rows(db, athlete_id)
    r = rows.get(uid)
    if r is None or r.state != "active":
        raise PlanError("找不到這堂課（或已經完成／錯過）")
    d = to_dict(r)
    ch = _clean(patch, today)
    if not ch:
        return d
    if "day" in ch and ch["day"] != d["day"]:
        _not_blocked(ch["day"], blocked)
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
    if d["kind"] == "test" and "title" in ch:
        from backend.engine import cp_protocols as CPP
        d["protocol"] = CPP.protocol_of({"title": ch["title"]}) or d.get("protocol")
    d.update(edited=True, week_start=new_week, provisional=False)
    _fill(r, d)
    await db.commit()
    return d


async def add(db: AsyncSession, data: dict, today: str, athlete_id: int = 1,
              blocked: Optional[dict] = None) -> dict:
    data = dict(data)
    data.setdefault("kind", "easy")
    if data["kind"] in NOT_LOAD:
        raise PlanError("課表待確認是自動調整的提醒，不能自己新增")
    if data["kind"] == "test":
        _test_default(data)
    data.setdefault("title", DEFAULT_TITLES.get(data.get("kind"), "自訂"))
    if "day" not in data:
        raise PlanError("要選日期")
    ch = _clean(data, today)
    _not_blocked(ch["day"], blocked)
    d = {"uid": R.new_uid(), "week_start": R.monday_of(ch["day"]), "gen_key": None, "origin": "custom",
         "edited": True, "provisional": False, "state": "active", "done_by": None, "note": None,
         "source": "", "tss": 0.0, "target": "", "detail": "", "minutes": 45, **ch}
    if d["kind"] == "test":
        d["protocol"] = data.get("protocol")
        d["source"] = str(data.get("source") or "")
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


def session_tss(s: dict) -> float:
    """The load a session counts for: a done one its activity's real TSS (an
    easy run done too hard counts what it really cost, engine/adapt.py rule
    D1), else the planned TSS; a notice nothing."""
    if s.get("kind") in NOT_LOAD:
        return 0.0
    if s.get("state") == "done" and isinstance(s.get("done_by"), dict) and s["done_by"].get("tss") is not None:
        return float(s["done_by"]["tss"])
    return float(s.get("tss") or 0.0)


def plan_summary(ss: list[dict], week_start: str, today: str, ctl0: float, atl0: float,
                 cc: float, ac: float, horizon_end: Optional[str] = None) -> dict:
    """Week targets and the CTL/ATL projection from the stored plan, so edits show
    in the progress bars and the PMC. Target = active + done sessions of the week
    (missed ones were re-planned or dropped); time excludes strength, like
    week_plan(). The projection continues today's CTL/ATL with the planned TSS
    of each later day up to the horizon."""
    from backend.engine.overview import project
    week_end = (dt.date.fromisoformat(week_start) + dt.timedelta(days=6)).isoformat()
    live = [s for s in ss if s["state"] in ("active", "done") and s.get("day") and s["kind"] not in NOT_LOAD]
    wk = [s for s in live if week_start <= s["day"] <= week_end]
    hours = sum(s["minutes"] or 0 for s in wk if s["kind"] != "strength") / 60.0
    tss = sum(session_tss(s) for s in wk)
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
            "done": s["state"] == "done", "protocol": s.get("protocol")}


# ---------------------------------------------------------------------------
# synchronous read for workout_review.classify (runs in worker threads, like
# plan_prefs.load): the stored CP-test sessions, so a test is recognised from
# the plan (done_by) before any guessing from the power pattern
# ---------------------------------------------------------------------------

_TEST_CACHE: dict = {}
_TITLE_CACHE: dict = {}


def done_titles(db_path=None) -> dict:
    """{activity index: title} of the done quality / test sessions, so
    quality_gate.dose_step judges an interval against what was planned
    (a recovery fartlek is not a ladder step). Read-only sqlite, cached on
    the file's mtime; {} when the DB is missing."""
    import sqlite3
    from pathlib import Path
    if db_path is None:
        from backend.engine.wko5expr.datasource import _db_path
        db_path = _db_path()
    if db_path is None:
        return {}
    p = Path(db_path)
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _TITLE_CACHE.get(str(p))
    if hit and hit[0] == mt:
        return hit[1]
    out: dict = {}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            for title, done_by in con.execute("SELECT title, done_by FROM plan_sessions "
                                              "WHERE state='done' AND kind IN ('quality','test')"):
                try:
                    d = json.loads(done_by) if done_by else None
                except ValueError:
                    d = None
                if isinstance(d, dict) and d.get("index") is not None:
                    out[d["index"]] = title
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    _TITLE_CACHE[str(p)] = (mt, out)
    return out


def test_sessions(db_path=None) -> list[dict]:
    """Stored kind 'test' sessions: {uid, day, state, title, protocol, done_by, gen_key}.
    Read-only sqlite; [] when the DB / table / column is missing. Cached on
    the file's mtime."""
    import sqlite3
    from pathlib import Path
    if db_path is None:
        from backend.engine.wko5expr.datasource import _db_path
        db_path = _db_path()
    if db_path is None:
        return []
    p = Path(db_path)
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return []
    hit = _TEST_CACHE.get(str(p))
    if hit and hit[0] == mt:
        return hit[1]
    out: list[dict] = []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(plan_sessions)")}
            if cols:
                proto = "protocol" if "protocol" in cols else "NULL"
                gk = "gen_key" if "gen_key" in cols else "NULL"
                for uid, day, state, title, pr, done_by, gen in con.execute(
                        f"SELECT uid, day, state, title, {proto}, done_by, {gk} FROM plan_sessions "
                        "WHERE kind='test'"):
                    try:
                        db_ = json.loads(done_by) if done_by else None
                    except ValueError:
                        db_ = None
                    # gen_key test_aet: the AeT test (aet_test.is_aet_session), also on rows without protocol
                    out.append({"uid": uid, "day": day, "state": state, "title": title, "protocol": pr,
                                "done_by": db_ if isinstance(db_, dict) else None, "gen_key": gen})
        finally:
            con.close()
    except sqlite3.Error:
        return []
    _TEST_CACHE[str(p)] = (mt, out)
    return out
