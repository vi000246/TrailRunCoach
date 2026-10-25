"""
Coaches and students (SP-789, SP-793; accounts/coaching.py has the rules). Every route answers 404
unless sign-in is on (login.required()); the demo never mounts this router. Signed in only (the login
gate), writes CSRF-checked like every write. No route takes "whose data": a student id in a path is
only ever looked up through coaching.student_of (403 NOT_YOUR_STUDENT unless that link is active now).

The student's side (設定 › 教練 › 我的教練):
  GET  /api/v1/account/coach                    my coaches (live links: pending / active), whether I am a coach
  POST /api/v1/account/coach/code               {code}: who that coach is (name only; 404 NO_CODE, 429 after
                                                CODE_FAILS unknown codes)
  POST /api/v1/account/coach/request            {code}: ask that coach (pending until they accept)
  POST /api/v1/account/coach/invite             {token}: who sent this invite link (name only; 404 INVITE_INVALID)
  POST /api/v1/account/coach/invite/accept      {token}: yes — active now (409 HAS_COACH / REQUEST_PENDING /
                                                COACH_FULL / SELF)
  POST /api/v1/account/coach/{link}/end         withdraw (active) or cancel my request (pending)

The coach's side (設定 › 教練 › 我的學員; 403 NOT_A_COACH for anyone else):
  GET    /api/v1/coach                          my code, invite link (when it ends), students, limit
  POST   /api/v1/coach/invite                   a new invite link {url, expires_at} (the old one stops working;
                                                the URL is in this answer only)
  DELETE /api/v1/coach/invite                   no invite link any more
  POST   /api/v1/coach/code                     a new code (the old one stops working)
  POST   /api/v1/coach/requests/{link}/accept   accept a student's request (409 COACH_FULL, HAS_COACH)
  POST   /api/v1/coach/links/{link}/end         let a student go / decline a request
  GET    /api/v1/coach/students/{student id}    one active student (name, since, what they allow) — the
                                                check SP-790 / SP-791 build on; 403 NOT_YOUR_STUDENT otherwise
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Path as Path_
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.accounts import coaching as CO, login as LG
from backend.i18n import N_, UserError

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


def _ok(body: dict) -> JSONResponse:
    return JSONResponse(body, headers=NO_STORE)


# ---------------------------------------------------------------------------- the student's side

@router.get("/api/v1/account/coach")
async def my_coach():
    _on()
    return _ok(await run_in_threadpool(CO.student_view, _me()))


class CodeIn(BaseModel):
    code: Optional[str] = None


@router.post("/api/v1/account/coach/code")
async def coach_code_lookup(body: CodeIn):
    _on()
    u = _me()
    coach = await run_in_threadpool(CO.lookup_code, u, body.code)
    return _ok({"coach": CO.coach_preview(coach)})


@router.post("/api/v1/account/coach/request")
async def coach_request(body: CodeIn):
    _on()
    u = _me()
    await run_in_threadpool(CO.request_by_code, u, body.code)
    return _ok(await run_in_threadpool(CO.student_view, u))


class InviteIn(BaseModel):
    token: Optional[str] = None


@router.post("/api/v1/account/coach/invite")
async def coach_invite_peek(body: InviteIn):
    _on()
    u = _me()
    coach = await run_in_threadpool(CO.lookup_invite, u, body.token)
    return _ok({"coach": CO.coach_preview(coach)})


@router.post("/api/v1/account/coach/invite/accept")
async def coach_invite_accept(body: InviteIn):
    _on()
    u = _me()
    await run_in_threadpool(CO.accept_invite, u, body.token)
    return _ok(await run_in_threadpool(CO.student_view, u))


@router.post("/api/v1/account/coach/{link_id}/end")
async def coach_end_as_student(link_id: int = Path_(ge=1, le=2 ** 62)):
    _on()
    u = _me()
    await run_in_threadpool(CO.end_as_student, u, link_id)
    return _ok(await run_in_threadpool(CO.student_view, u))


# ---------------------------------------------------------------------------- the coach's side

@router.get("/api/v1/coach")
async def coach_home():
    _on()
    return _ok(await run_in_threadpool(CO.coach_view, _me()))


@router.post("/api/v1/coach/invite")
async def coach_new_invite():
    _on()
    u = _me()
    token, exp = await run_in_threadpool(CO.new_invite, u)
    return _ok({"url": CO.invite_url(token), "expires_at": exp})


@router.delete("/api/v1/coach/invite")
async def coach_drop_invite():
    _on()
    u = _me()
    await run_in_threadpool(CO.require_coach, u)
    from backend.accounts import store
    await run_in_threadpool(store.delete_coach_invite, u.id)
    return _ok(await run_in_threadpool(CO.coach_view, u))


@router.post("/api/v1/coach/code")
async def coach_new_code():
    _on()
    u = _me()
    await run_in_threadpool(CO.reset_code, u)
    return _ok(await run_in_threadpool(CO.coach_view, u))


@router.post("/api/v1/coach/requests/{link_id}/accept")
async def coach_accept(link_id: int = Path_(ge=1, le=2 ** 62)):
    _on()
    u = _me()
    await run_in_threadpool(CO.accept_request, u, link_id)
    return _ok(await run_in_threadpool(CO.coach_view, u))


@router.post("/api/v1/coach/links/{link_id}/end")
async def coach_end(link_id: int = Path_(ge=1, le=2 ** 62)):
    _on()
    u = _me()
    await run_in_threadpool(CO.end_as_coach, u, link_id)
    return _ok(await run_in_threadpool(CO.coach_view, u))


@router.get("/api/v1/coach/students/{student_id}")
async def coach_student(student_id: str):
    """One student of this coach, checked against accounts.db now (coaching.student_of)."""
    _on()
    u = _me()
    await run_in_threadpool(CO.require_coach, u)
    return _ok(await run_in_threadpool(CO.student_info, u, student_id))
