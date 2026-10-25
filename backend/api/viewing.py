"""
代看 (SP-791) and what a student allows their coach to change (SP-792); the rules are
accounts/viewing.py's, the links accounts/coaching.py's. Every route answers 404 unless sign-in is on;
the demo never mounts this router. Writes are CSRF-checked by the gate like every write.

The coach's side:
  POST /api/v1/coach/view/{student id}   this session views that student (403 NOT_A_COACH /
                                         NOT_YOUR_STUDENT — the same answer for anyone who is not an
                                         active student of theirs —, COACH_VIEW_OWNER for the site's owner)
                                         -> {"go": the student's overview}. Closed while viewing (stop first)
  POST /api/v1/coach/view/stop           this session views nobody any more -> {"go": the coach's 教練 tab};
                                         open while viewing, also after the student withdrew

The student's side (設定 › 教練 › 我的教練; closed while viewing, like every /api/v1/account/ route):
  PUT  /api/v1/account/coach/{link}/perms   {plan?: bool, training?: bool}: the switches of an active link
                                            (a consents row each time) -> 我的教練 as GET /account/coach
  GET  /api/v1/account/coach/edits          what my coaches did in my data, newest first (?limit ≤ 200)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Path as Path_, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.accounts import coaching as CO, login as LG, store, viewing as VW
from backend.i18n import N_, UserError
from backend.tenancy_mw import cookie

router = APIRouter(tags=["coach"], include_in_schema=False)
NO_STORE = {"Cache-Control": "no-store"}


def _on() -> None:
    if not LG.required():
        raise HTTPException(404, "not found")


def _me():
    u = LG.current_user()
    if u is None:                       # the gate answers 401 first
        raise UserError(N_("請先登入"), code="LOGIN_REQUIRED", status=401)
    return u


def _token(request: Request) -> Optional[str]:
    return cookie(request.scope, LG.cookie_name(LG.SESSION_COOKIE))


def _start(coach, student_id, token: Optional[str]) -> dict:
    CO.require_coach(coach)
    student = CO.student_of(coach, student_id)
    if student.is_owner or int(student.id) == 1:
        raise UserError(N_("站主的資料不開放代看"), code="COACH_VIEW_OWNER", status=403)
    if not token:
        raise UserError(N_("請先登入"), code="LOGIN_REQUIRED", status=401)
    store.coach_view_put(token, coach.id, student.id)
    store.coach_audit_add(student.id, coach.id, "view_start")
    VW.log.info("coach view started")
    return {"go": VW.HOME, "student": {"id": student.id, "name": CO._name(student.display_name, student.id)}}


def _stop(user, token: Optional[str]) -> dict:
    sid = store.coach_view_drop(token)
    if sid is not None and store.get_user(sid) is not None:
        store.coach_audit_add(sid, user.id, "view_end")
        VW.log.info("coach view ended")
    return {"go": VW.COACH_HOME + "#coach"}


@router.post("/api/v1/coach/view/stop")
async def coach_view_stop(request: Request):
    _on()
    return JSONResponse(await run_in_threadpool(_stop, _me(), _token(request)), headers=NO_STORE)


@router.post("/api/v1/coach/view/{student_id}")
async def coach_view_start(request: Request, student_id: str = Path_(max_length=24)):
    _on()
    return JSONResponse(await run_in_threadpool(_start, _me(), student_id, _token(request)), headers=NO_STORE)


@router.put("/api/v1/account/coach/{link_id}/perms")
async def coach_perms(body: dict, link_id: int = Path_(ge=1, le=2 ** 62)):
    _on()
    u = _me()
    await run_in_threadpool(CO.set_perms, u, link_id, body)
    return JSONResponse(await run_in_threadpool(CO.student_view, u), headers=NO_STORE)


def edits_view(student_id: int, limit: int = 100, since: Optional[str] = None) -> list[dict]:
    """The student's coach log for the page: who (the coach's name as it was; none once deleted), when,
    which change and its few values."""
    return [{"at": r["at"], "coach": r["coach_name"] or (CO._name(None, r["coach_id"]) if r["coach_id"] else None),
             "action": r["action"], "params": r["params"]} for r in store.coach_audit_of(student_id, limit, since)]


@router.get("/api/v1/account/coach/edits")
async def coach_edits(limit: int = Query(100, ge=1, le=200)):
    _on()
    u = _me()
    return JSONResponse({"edits": await run_in_threadpool(edits_view, u.id, limit)}, headers=NO_STORE)
