"""
The user's training routes (SP-640; 課表 › 路線, static/routelib.html, api/route_library.py), apart
from the templates: a template is only a structure, the route is picked when the structure is put
on a day.

  * Uploaded routes (table training_routes, models.TrainingRoute): a GPX / FIT course read with the
    race calculator's reader and builder (user_templates.parse_profile — the same smoothing, grade
    segments and SP-639's climb rule), the file gzipped to <HOME>/route_gpx/<id>.gz (per tenant),
    the profile and 坡段表 cached in the row. Upload, rename, delete, download.
  * 「我的路段」 (engine/routes.py: climbs and routes found in the activities): the same page and
    the same picker; their profile is the reference effort's track (its file's compact track, the
    stretch i0..i1), cut into segments and climbs the same way (mine_course).
  * A template's GPX (SP-36, user_templates: template_gpx/<id>.gz) moves here once (_move_templates,
    on the first read of the list): a route named after the template, the same file; the
    template's own copy is left as it was (old sessions still draw it) and a route the user then
    deletes is not brought back (training_routes_moved).

Picking a route for a structure (apply): the session's steps get the route's profile copy
(`route`, user_templates.route_copy, + `src` = where it came from); when the structure has a hill
repeat and the route a climb (the biggest unless another is picked, `climb` 0 = none), each rep's
climb ends at the top with the lap button (直到按下計圈, its estimated time kept as `est`) and is
named 「爬 120 m · 約 6′」 (user_templates.CLIMB_LABEL: the climb's gain, the time at the step's
intensity by Minetti — the step chart's speed, 推估), and the chart starts the repeats at the climb's
foot (`rep_km`). The watch gets the steps and targets only: no route is ever pushed (COROS / Garmin
take a route as a separate thing; the step name is the note, ≤ workout_steps.MAX_NOTE).
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import TrainingRoute, TrainingRouteMoved, WorkoutTemplateUser
from backend.i18n import N_, _

NAME_MAX = 60
MAX_ROUTES = 200
# a repeat's work leg counts as a climb rep when it ends with the lap button, or lasts at least this:
# strides (≤ 1′ fast bits) on a route stay as they are
CLIMB_REP_MIN_S = 90
ROOT = None                      # fixed folder (tests); None = the tenant's route_gpx/

KIND_GPX, KIND_SEG = "gpx", "seg"
# 「我的路段」 offered (done ≥ MINE_MIN times): every kind engine/routes.py finds, climbs first (the hill
# repeats' use), then whole routes, shared stretches, descents
MINE_KINDS = ("climb", "route", "stretch", "descent")
MINE_MIN = 2
NOT_FOUND = N_("找不到這條路線")


class RouteError(ValueError):
    def __init__(self, errors: list[str], status: int = 400):
        super().__init__("；".join(errors))
        self.errors = errors
        self.status = status


def _root() -> Path:
    if ROOT is not None:
        return Path(ROOT)
    from backend import tenancy
    return tenancy.private_path("route_gpx")


def gpx_path(rid: int, root: Optional[Path] = None) -> Path:
    return Path(root or _root()) / f"{int(rid)}.gz"


def _now() -> dt.datetime:
    return dt.datetime.utcnow().replace(microsecond=0)


def _loads(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _name(v) -> str:
    s = " ".join(str(v or "").split())
    if not s:
        raise RouteError([_("路線名稱不能空白")])
    if len(s) > NAME_MAX:
        raise RouteError([_("路線名稱最多 {n} 字", n=NAME_MAX)])
    return s


# ---------------------------------------------------------------------------
# the table, and a template's GPX moved here
# ---------------------------------------------------------------------------

async def _ensure(db: AsyncSession) -> None:
    """The tables in a DB made before them (a demo sandbox from an older base): create_all is idempotent."""
    def mk(sess):
        conn = sess.connection()
        for tbl in (TrainingRoute.__table__, TrainingRouteMoved.__table__, WorkoutTemplateUser.__table__):
            tbl.create(conn, checkfirst=True)
    await db.run_sync(mk)


def _write_file(rid: int, data: bytes, root: Optional[Path] = None) -> None:
    f = gpx_path(rid, root)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    os.replace(tmp, f)


async def _move_templates(db: AsyncSession, root: Optional[Path] = None) -> int:
    """Each template with a GPX not moved yet → a route (its name, the same file and cached profile).
    The template's row and file are left as they were. A template whose file is gone is marked moved."""
    from backend.engine import user_templates as UT
    from sqlalchemy import text
    cols = {r[1] for r in (await db.execute(text("PRAGMA table_info(workout_templates_user)"))).fetchall()}
    if "gpx_sha1" not in cols:
        return 0
    done = set((await db.execute(select(TrainingRouteMoved.tpl_id))).scalars().all())
    tpls = (await db.execute(select(WorkoutTemplateUser).where(WorkoutTemplateUser.gpx_sha1.is_not(None))
                             .order_by(WorkoutTemplateUser.id))).scalars().all()
    n = 0
    for t in tpls:
        if t.id in done:
            continue
        data = UT.read_gpx(t.id)
        if data is None:
            db.add(TrainingRouteMoved(tpl_id=t.id, route_id=None))
            continue
        now = _now()
        r = TrainingRoute(name=(t.name or t.gpx_filename or "route")[:NAME_MAX], filename=t.gpx_filename,
                          sha1=t.gpx_sha1, km=t.gpx_km, gain_m=t.gpx_gain_m, loss_m=t.gpx_loss_m,
                          profile_json=t.gpx_profile_json, course_json=t.gpx_course_json, from_tpl=t.id,
                          created_at=t.updated_at or now, updated_at=now)
        db.add(r)
        await db.flush()
        _write_file(r.id, data, root)
        db.add(TrainingRouteMoved(tpl_id=t.id, route_id=r.id))
        n += 1
    await db.commit()
    return n


# ---------------------------------------------------------------------------
# the uploaded routes
# ---------------------------------------------------------------------------

def to_dict(r: TrainingRoute) -> dict:
    c = _loads(r.course_json, {})
    return {"id": r.id, "name": r.name, "filename": r.filename, "km": r.km, "gain_m": r.gain_m, "loss_m": r.loss_m,
            "n_climbs": len(c.get("climbs") or []), "from_tpl": r.from_tpl,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


async def list_all(db: AsyncSession, root: Optional[Path] = None) -> list[dict]:
    await _ensure(db)
    try:
        await _move_templates(db, root)
    except Exception:          # noqa: BLE001 — a read-only copy (the static demo): moved next time
        await db.rollback()
    rows = (await db.execute(select(TrainingRoute).order_by(TrainingRoute.id))).scalars().all()
    return [to_dict(r) for r in rows]


async def _get(db: AsyncSession, rid) -> TrainingRoute:
    await _ensure(db)
    try:
        r = await db.get(TrainingRoute, int(rid))
    except (TypeError, ValueError):
        r = None
    if r is None:
        raise RouteError([_(NOT_FOUND)], 404)
    return r


async def get(db: AsyncSession, rid) -> dict:
    return to_dict(await _get(db, rid))


async def create(db: AsyncSession, data: bytes, filename: str = "", name: Optional[str] = None,
                 parsed: Optional[dict] = None, root: Optional[Path] = None) -> dict:
    """Upload a route (a broken file is refused before anything is written). Its name: `name`, else
    the GPX's own, else the file name without its extension."""
    from backend.engine import user_templates as UT
    await _ensure(db)
    nm = _name(name) if name is not None and str(name).strip() else None
    try:
        got = parsed or UT.parse_profile(data, filename)
    except UT.TemplateError as e:
        raise RouteError(e.errors)
    n = len((await db.execute(select(TrainingRoute.id))).all())
    if n >= MAX_ROUTES:
        raise RouteError([_("路線最多 {n} 條", n=MAX_ROUTES)])
    tot = got["totals"]
    now = _now()
    stem = Path(filename or "").stem
    r = TrainingRoute(name=nm or (got.get("name") or stem or "route").strip()[:NAME_MAX],
                      filename=(filename or got.get("name") or "route.gpx")[:200], sha1=hashlib.sha1(data).hexdigest(),
                      km=float(tot["km"]), gain_m=float(tot["gain_m"]), loss_m=float(tot["loss_m"]),
                      profile_json=json.dumps(got["profile"]), course_json=json.dumps(got.get("course") or {}),
                      created_at=now, updated_at=now)
    db.add(r)
    await db.flush()
    _write_file(r.id, data, root)
    await db.commit()
    return to_dict(r)


async def rename(db: AsyncSession, rid, name) -> dict:
    r = await _get(db, rid)
    r.name = _name(name)
    r.updated_at = _now()
    await db.commit()
    return to_dict(r)


async def delete(db: AsyncSession, rid, root: Optional[Path] = None) -> dict:
    r = await _get(db, rid)
    rid = r.id
    await db.delete(r)
    await db.commit()
    try:
        gpx_path(rid, root).unlink()
    except OSError:
        pass
    return {"removed": rid}


def read_file(rid: int, root: Optional[Path] = None) -> Optional[bytes]:
    try:
        return gzip.decompress(gpx_path(rid, root).read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile, ValueError):
        return None


async def course_of(db: AsyncSession, rid, root: Optional[Path] = None) -> dict:
    """{profile, course, totals, name} of an uploaded route; the segments recomputed from the stored
    file when made under other climb thresholds (user_templates._fresh), and kept."""
    import asyncio

    from backend.engine import user_templates as UT
    r = await _get(db, rid)
    c = _loads(r.course_json, {})
    prof = _loads(r.profile_json, {})
    if not UT._fresh(c) or not prof.get("km"):
        data = read_file(r.id, root)
        if data is None:
            raise RouteError([_(NOT_FOUND)], 404)
        got = await asyncio.to_thread(UT.parse_profile, data, r.filename or "")
        c, prof = got["course"], got["profile"]
        r.course_json, r.profile_json = json.dumps(c), json.dumps(prof)
        await db.commit()
    return {"profile": prof, "course": c, "name": r.name, "km": r.km, "gain_m": r.gain_m, "loss_m": r.loss_m}


# ---------------------------------------------------------------------------
# 「我的路段」
# ---------------------------------------------------------------------------

def ref_effort(item: dict) -> Optional[dict]:
    """The effort whose track is the item's shape: on its reference file, else the first, the same direction."""
    effs = [e for e in item.get("efforts") or [] if (e.get("dir") or "same") == "same"]
    return next((e for e in effs if e.get("file") == item.get("ref_file")), effs[0] if effs else None)


def mine_course(item: dict, track: Optional[dict]) -> dict:
    """parse_profile of a 「我的路段」 item: its reference effort's stretch of the compact track
    (engine/routes.py: lat, lon, e = elevation m, d = distance km at each kept point), as a course."""
    from backend.engine import user_templates as UT
    from backend.engine.racepower import gpx as GPX
    e = ref_effort(item)
    if not track or e is None:
        raise RouteError([_("這個路段沒有可用的軌跡")], 404)
    i0, i1 = int(e["i0"]), int(e["i1"]) + 1
    lat, lon, ele, d = (list(track.get(k) or [])[i0:i1] for k in ("lat", "lon", "e", "d"))
    if len(lat) < 2 or len(lon) != len(lat) or len(ele) != len(lat):
        raise RouteError([_("這個路段沒有可用的軌跡")], 404)
    dist = None
    if len(d) == len(lat) and all(v is not None for v in d):
        dist = [(float(v) - float(d[0])) * 1000.0 for v in d]
    tr = GPX.Track(lat=[float(v) for v in lat], lon=[float(v) for v in lon], ele=ele, dist=dist)
    try:
        return UT.parse_track(tr)
    except UT.TemplateError as ex:
        raise RouteError(ex.errors, 404)


def mine_row(item: dict, name: str, fav: bool = False) -> dict:
    return {"id": item["id"], "name": name, "kind": item["kind"], "direction": item.get("direction"),
            "length_m": item.get("length_m"), "gain_m": item.get("gain_m"), "n_efforts": item.get("n_efforts"),
            "n_activities": item.get("n_activities"), "best_s": item.get("best_s"), "last_s": item.get("last_s"),
            "last_date": item.get("last_date"), "time_key": item.get("time_key"), "fav": fav,
            "renamed": name != item.get("auto_name")}


def mine_rows(view: Optional[dict], names: dict, favs: dict) -> list[dict]:
    """「我的路段」 on the page and in the picker: the segments and routes done at least MINE_MIN times —
    starred first, then renamed, then by kind (MINE_KINDS: climbs first), then the most done."""
    if not view:
        return []
    out = []
    for it in view.get("items", {}).values():
        if it.get("kind") not in MINE_KINDS or (it.get("n_activities") or 0) < MINE_MIN:
            continue
        out.append(mine_row(it, names.get(it["id"]) or it.get("auto_name") or "", it["id"] in favs))
    out.sort(key=lambda r: (not r["fav"], not r["renamed"], MINE_KINDS.index(r["kind"]), -(r["n_efforts"] or 0), r["name"]))
    return out


# ---------------------------------------------------------------------------
# a route on a day's structure
# ---------------------------------------------------------------------------

def pick_climb(course: dict, no: Optional[int]) -> Optional[dict]:
    """The climb the hill repeats go up: `no` (0 = none), else the biggest gain."""
    climbs = course.get("climbs") or []
    if no == 0 or not climbs:
        return None
    if no is not None:
        return next((c for c in climbs if c.get("no") == no), None)
    return max(climbs, key=lambda c: (c.get("gain_m") or 0.0, -(c.get("no") or 0)))


def _reps(items: list):
    for x in items:
        if x.get("kind") == "repeat":
            yield x
            yield from _reps(x.get("items") or [])


def _climb_rep(st: dict) -> bool:
    d = st.get("dur") or {}
    if d.get("type") == "open":
        return True
    if d.get("type") == "time":
        return float(d.get("value") or 0) >= CLIMB_REP_MIN_S
    return d.get("type") in ("distance", "load")


def climb_secs(st: dict, climb: dict, c) -> float:
    """The climb's time at the step's intensity: the flat speed there (the step chart's, user_templates
    ._flat_kmh) over Minetti's factor at the climb's grade; a step with no target at the session's
    easy intensity. 推估."""
    from backend.engine import user_templates as UT
    from backend.engine import workout_steps as WS
    r = WS.resolve(st, c)
    f = r.frac if r.frac is not None else WS.NONE_IF.get(st["kind"], c.easy_frac())
    v = UT._flat_kmh(f, c) / UT._gf(float(climb.get("grade") or 0.0))
    return float(climb.get("dist_m") or 0.0) / 1000.0 / max(0.1, v) * 3600.0


def apply(steps: dict, prof: dict, src: dict, climb: Optional[dict], c) -> tuple[dict, int]:
    """(the steps with the route, how many repeats now climb to the top). The structure's template link
    goes (a template is a structure only); each hill repeat's first work leg ends with the lap button at
    the top (its estimated time kept), named 「爬 120 m · 約 6′」, its target as it was."""
    from backend.engine import user_templates as UT
    out = copy.deepcopy(steps)
    out.pop("tpl", None)
    r = UT.route_copy(prof)
    if r is None:
        raise RouteError([_("這條路線沒有高度剖面")])
    r["src"] = {"kind": src["kind"], "id": str(src["id"])}
    n = 0
    if climb:
        r["climb"] = {k: climb[k] for k in ("no", "gain_m", "dist_m", "grade", "start_km", "end_km")}
        r["rep_km"] = round(float(climb["start_km"]), 3)
        for rep in _reps(out.get("items") or []):
            leg = next((x for x in rep.get("items") or [] if x.get("kind") in ("work", "other")), None)
            if leg is None or not _climb_rep(leg):
                continue
            secs = climb_secs(leg, climb, c)
            leg["dur"] = {"type": "open", "est": int(round(min(6 * 3600.0, max(5.0, secs))))}
            leg["note"] = _(UT.CLIMB_LABEL, m=round(float(climb["gain_m"])), t=UT._mins(secs))
            n += 1
    out["route"] = r
    return out, n
