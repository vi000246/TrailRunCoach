"""
課表 › 路線 (SP-640; engine/route_library.py, static/routelib.html): the training routes, apart from
the templates, and picking one for a day's structure.

  GET    /api/v1/overview/plan/routes/page
  GET    /api/v1/overview/plan/routes/library              {routes, limits} (a template's GPX moved here once)
  POST   /api/v1/overview/plan/routes/library              upload (multipart file, name?)
  GET    /api/v1/overview/plan/routes/library/{id}         {…, profile, table (the 坡段表 at the easy run)}
  PATCH  /api/v1/overview/plan/routes/library/{id}         {name}
  DELETE /api/v1/overview/plan/routes/library/{id}
  GET    /api/v1/overview/plan/routes/library/{id}/file    the stored file
  GET    /api/v1/overview/plan/routes/mine                 「我的路段」: climbs and routes done ≥ 2 times
  GET    /api/v1/overview/plan/routes/mine/{rid}           {…, profile, table, link (路線進步 page)}
  PUT    /api/v1/overview/plan/routes/mine/{rid}/terrain   {level: normal | technical | scramble | null} — a
                                                           route's default 地形難度 (SP-731)
  POST   /api/v1/overview/plan/steps/route                 {steps, route: {src: gpx | seg, id, climb?} | null,
                                                            + the session fields /steps/check takes}
                                                           → {steps, climbs, climb, applied, name}
  POST   /api/v1/overview/plan/steps/from-route            {route: {src: gpx | seg, id}, + the session fields}
                                                           → a session made from the whole route (SP-641,
                                                             engine/route_session.py): {steps, legs, minutes,
                                                             km, gain_m, title, notes}; nothing is saved

「我的路段」 are read from the last built route index (api/routes.py); this page starts no build.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.db.database import get_db
from backend.i18n import _
from backend.i18n.pages import render_page

router = APIRouter(prefix="/api/v1/overview/plan", tags=["overview"])


def _err(e) -> HTTPException:
    return HTTPException(e.status, {"errors": e.errors} if e.status != 404 else e.errors[0])


async def _call(fn, *a, **kw):
    from backend.engine import route_library as RL
    try:
        return await fn(*a, **kw)
    except RL.RouteError as e:
        raise _err(e)


@router.get("/routes/page", include_in_schema=False)
def routes_page():
    """路線 (static/routelib.html, SP-640): the fourth tab of 課表."""
    return render_page("routelib")


async def _env(body: Optional[dict], db: AsyncSession) -> dict:
    """The step context of the session in `body` (the editor's fields, else an easy trail run)."""
    from backend.api import plan_sessions as PSA
    body = body or {"kind": "hike", "minutes": 0}
    stored = None
    if body.get("uid"):
        stored = next((x for x in await PSA.PS.load(db) if x["uid"] == body.get("uid")), None)
    s = PSA._session_of(body, stored)
    return await PSA._steps_env(s, await PSA._inputs(), stored)


async def _table(course: dict, db: AsyncSession) -> dict:
    from backend.engine import user_templates as UT
    env = await _env(None, db)
    c = env["ctx"]
    return UT.course_table(course, c, [("easy", _(UT.LEVEL_EASY), c.easy_frac())])


def _profile_out(p: dict) -> dict:
    from backend.engine import user_templates as UT
    r = UT.route_copy({"km": p.get("km") or [], "z": p.get("z") or []}) or {"km": [], "z": []}
    return {"km": r["km"], "z": r["z"]}


# ---------------------------------------------------------------------------
# the uploaded routes
# ---------------------------------------------------------------------------

@router.get("/routes/library")
async def library_list(db: AsyncSession = Depends(get_db)):
    from backend.engine import route_library as RL
    return {"routes": await RL.list_all(db), "limits": {"name": RL.NAME_MAX, "routes": RL.MAX_ROUTES}}


@router.post("/routes/library")
async def library_upload(file: UploadFile = File(...), name: Optional[str] = Form(None),
                         db: AsyncSession = Depends(get_db)):
    from backend.engine import route_library as RL
    from backend.engine import user_templates as UT
    from backend.engine.racepower import gpx as GPX
    data = await file.read(GPX.MAX_BYTES + 1)
    try:
        got = await run_in_threadpool(UT.parse_profile, data, file.filename or "")
    except UT.TemplateError as e:
        raise HTTPException(400, {"errors": e.errors})
    return await _call(RL.create, db, data, file.filename or "", name=name, parsed=got)


@router.get("/routes/library/{rid}")
async def library_detail(rid: int, db: AsyncSession = Depends(get_db)):
    from backend.engine import route_library as RL
    meta = await _call(RL.get, db, rid)
    c = await _call(RL.course_of, db, rid)
    return {**meta, "profile": _profile_out(c["profile"]), "table": await _table(c["course"], db)}


@router.patch("/routes/library/{rid}")
async def library_rename(rid: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import route_library as RL
    return await _call(RL.rename, db, rid, body.get("name"))


@router.delete("/routes/library/{rid}")
async def library_delete(rid: int, db: AsyncSession = Depends(get_db)):
    from backend.engine import route_library as RL
    return await _call(RL.delete, db, rid)


@router.get("/routes/library/{rid}/file")
async def library_file(rid: int, db: AsyncSession = Depends(get_db)):
    from urllib.parse import quote

    from fastapi.responses import Response

    from backend.engine import route_library as RL
    r = await _call(RL.get, db, rid)
    data = RL.read_file(r["id"])
    if data is None:
        raise HTTPException(404, _(RL.NOT_FOUND))
    name = r.get("filename") or "route.gpx"
    mt = "application/gpx+xml" if not name.lower().endswith(".fit") else "application/octet-stream"
    return Response(content=data, media_type=mt,
                    headers={"Content-Disposition": f"attachment; filename=\"route\"; filename*=UTF-8''{quote(name)}"})


# ---------------------------------------------------------------------------
# 「我的路段」 (api/routes.py's index; no build started here)
# ---------------------------------------------------------------------------

def _mine_view():
    from backend.api import routes as RA
    try:
        view = RA._index()
    except Exception:         # noqa: BLE001 — no index yet / unreadable: none listed
        return None, {}, {}
    if not view:
        return None, {}, {}
    return view, RA._names(view), RA._favs(view) if hasattr(RA, "_favs") else {}


def _mine_item(rid: str) -> tuple[dict, str, dict]:
    """(item, its name, its reference track) of a 「我的路段」; 404 when it isn't one."""
    from backend.api import routes as RA
    from backend.engine import route_library as RL
    view, names, _favs = _mine_view()
    items = (view or {}).get("items", {})
    rid = rid if rid in items else (view or {}).get("aliases", {}).get(rid, rid)
    it = items.get(rid)
    if it is None or it.get("kind") not in RL.MINE_KINDS:
        raise HTTPException(404, _(RL.NOT_FOUND))
    e = RL.ref_effort(it)
    track = RA.STORE.load_track(e["file"]) if e else None
    return it, names.get(rid) or it.get("auto_name") or "", track


def _mine_terrain(view) -> dict:
    """{route id: 地形難度} the user set (SP-731; routes/terrain.json), old ids resolved."""
    from backend.api import routes as RA
    from backend.engine import terrain_difficulty as TD
    try:
        return {k: v for k, v in RA._by_current_id(RA.STORE.terrain(), view).items() if TD.valid(v)}
    except Exception:         # noqa: BLE001
        return {}


@router.get("/routes/mine")
async def mine_list():
    from backend.engine import route_library as RL
    view, names, favs = _mine_view()
    return {"rows": RL.mine_rows(view, names, favs, _mine_terrain(view)), "built_at": (view or {}).get("built_at"),
            "page": "/api/v1/routes/page"}


@router.put("/routes/mine/{rid}/terrain")
async def mine_terrain(rid: str, body: dict = Body(...)):
    """SP-731: a whole route's default 地形難度 {level: normal | technical | scramble | null}; the
    activities on it without their own mark take it (engine/terrain_difficulty.py) — in the race
    calculator's trail model as in the activity list. Only kind 「路線」."""
    from backend.api import routes as RA
    from backend.engine import terrain_difficulty as TD
    level = (body or {}).get("level")
    if level is not None and not TD.valid(level):
        raise HTTPException(400, "INVALID_TERRAIN_DIFFICULTY")
    it, _name, _track = _mine_item(rid)
    if it.get("kind") != "route":
        raise HTTPException(400, "ROUTE_ONLY")
    view = RA._index() or {}
    aliases = tuple(k for k, v in (view.get("aliases") or {}).items() if v == it["id"])
    RA.STORE.set_terrain(it["id"], level, aliases)
    return {"id": it["id"], "terrain": level}


async def _mine_course(rid: str) -> tuple[dict, str, dict]:
    from backend.engine import route_library as RL
    it, name, track = _mine_item(rid)
    try:
        got = await run_in_threadpool(RL.mine_course, it, track)
    except RL.RouteError as e:
        raise _err(e)
    return it, name, got


@router.get("/routes/mine/{rid}")
async def mine_detail(rid: str, db: AsyncSession = Depends(get_db)):
    from backend.engine import route_library as RL
    it, name, got = await _mine_course(rid)
    view, _names, _favs = _mine_view()
    return {**RL.mine_row(it, name, terrain=_mine_terrain(view)), "profile": _profile_out(got["profile"]), "totals": got["totals"],
            "table": await _table(got["course"], db), "link": f"/api/v1/routes/page#{it['id']}"}


# ---------------------------------------------------------------------------
# a route on a day's structure
# ---------------------------------------------------------------------------

async def _picked(pick, db: AsyncSession) -> tuple[str, str, dict, dict, dict]:
    """(kind, id, name, course, profile + meta) of a picked route: an uploaded GPX or a 「我的路段」."""
    from backend.engine import route_library as RL
    kind, rid = (pick or {}).get("src"), (pick or {}).get("id")
    if kind not in (RL.KIND_GPX, RL.KIND_SEG) or rid in (None, ""):
        raise HTTPException(400, {"errors": [_("要選上傳的路線或我的路段")]})
    if kind == RL.KIND_GPX:
        c = await _call(RL.course_of, db, rid)
        rid = (await _call(RL.get, db, rid))["id"]
        return kind, rid, c["name"], c["course"], {**c["profile"], "route_km": c["km"], "gain_m": c["gain_m"]}
    it, name, got = await _mine_course(str(rid))
    return kind, it["id"], name, got["course"], {**got["profile"], "route_km": got["totals"]["km"],
                                                 "gain_m": got["totals"]["gain_m"]}


@router.post("/steps/from-route")
async def steps_from_route(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """「從這條路線產生課」 (SP-641, the routes page → the 課表 editor): a session on the whole route, one
    step per climb, each ending with the lap button (engine/route_session.build). Nothing is saved: the
    editor opens with it and the user saves it as a session of their own (POST /sessions, origin custom)."""
    from backend.api import plan_sessions as PSA
    from backend.engine import route_session as RS
    kind, rid, name, course, prof = await _picked(body.get("route"), db)
    body = {**body, "kind": body.get("kind") or "hike", "terrain": body.get("terrain") or "trail"}
    env = await _env(body, db)
    try:
        out = await run_in_threadpool(RS.build, course, prof, name, {"kind": kind, "id": rid}, env["ctx"])
    except ValueError as e:
        raise HTTPException(400, {"errors": [str(e)]})
    out["steps"] = PSA._norm_or_400(out["steps"])
    out["name"] = name
    out["src"] = {"kind": kind, "id": str(rid)}
    return out


@router.post("/steps/route")
async def steps_route(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """Pick (or drop: route null) the route of a structure: the steps back with the route's profile copy
    and, with a hill repeat, each climb to the top with the lap button (engine/route_library.apply).
    Nothing is saved: the editor saves the steps with the session."""
    from backend.api import plan_sessions as PSA
    from backend.engine import route_library as RL
    from backend.engine import user_templates as UT
    steps = PSA._norm_or_400(body.get("steps"))
    pick = body.get("route")
    if not pick:
        steps.pop("route", None)
        steps.pop("tpl", None)
        return {"steps": steps, "climbs": [], "climb": None, "applied": 0, "name": None}
    kind, rid = pick.get("src"), pick.get("id")
    if kind not in (RL.KIND_GPX, RL.KIND_SEG) or rid in (None, ""):
        raise HTTPException(400, {"errors": [_("要選上傳的路線或我的路段")]})
    if kind == RL.KIND_GPX:
        c = await _call(RL.course_of, db, rid)
        name, course, prof = c["name"], c["course"], c["profile"]
        rid = (await _call(RL.get, db, rid))["id"]
        meta = {"route_km": c["km"], "gain_m": c["gain_m"]}
    else:
        it, name, got = await _mine_course(str(rid))
        rid = it["id"]
        course, prof = got["course"], got["profile"]
        meta = {"route_km": got["totals"]["km"], "gain_m": got["totals"]["gain_m"]}
    no = pick.get("climb")
    try:
        no = None if no is None else int(no)
    except (TypeError, ValueError):
        raise HTTPException(400, {"errors": [_("沒有這段坡")]})
    climb = RL.pick_climb(course, no)
    if no and climb is None:
        raise HTTPException(400, {"errors": [_("沒有這段坡")]})
    env = await _env(body, db)
    c = env["ctx"]
    try:
        out, n = RL.apply(steps, {**prof, **meta, "name": name}, {"kind": kind, "id": rid}, climb, c)
    except RL.RouteError as e:
        raise _err(e)
    table = UT.course_table({"climbs": course.get("climbs") or [], "segments": []}, c,
                            [("easy", _(UT.LEVEL_EASY), c.easy_frac())])
    return {"steps": PSA._norm_or_400(out), "climbs": table["climbs"], "climb": climb["no"] if climb else None,
            "applied": n, "name": name}
