"""
教練看板 (SP-790): a coach's students at a glance, read only (backend/coach_board.py computes and
keeps each student's summary ahead; accounts/coaching.py has the rules of who may read whom).

    GET /api/v1/coach/board                     every active student of mine: name, since, the stored
                                                summary (null until the first one is computed), last sync,
                                                next race, alerts; the most alerting first
    GET /api/v1/coach/students/{id}/summary     one student's row — 403 NOT_YOUR_STUDENT unless that
                                                link is active now (the same answer for any other id)
    GET /api/v1/coach/board/page                the page (static/coach_board.html)

Mounted only with sign-in on (404 otherwise, like api/coach.py; never in the demo). 403 NOT_A_COACH
for anyone who is not a coach. A student is read only through coaching.in_student — the active link
is read from accounts.db again for every student of every request, so a withdrawal counts from the
next request — and only their stored summary and their sync times are read: never their Dataset.
No route here takes "whose data" except through that check.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend import coach_board as CB
from backend.accounts import coaching as CO, login as LG
from backend.i18n import N_, UserError
from backend.i18n.pages import render_page

router = APIRouter(tags=["coach"], include_in_schema=False)
NO_STORE = {"Cache-Control": "no-store"}
LEVEL_RANK = {"bad": 0, "watch": 1}


def _on() -> None:
    if not LG.required():
        raise HTTPException(404, "not found")


def _me():
    u = LG.current_user()
    if u is None:                       # the gate answers 401 first
        raise UserError(N_("請先登入"), code="LOGIN_REQUIRED", status=401)
    return u


def _row_here(student: dict) -> dict:
    """In the student's tenant (in_student): their stored summary and what changes by the minute."""
    return {"student_id": student["student_id"], "name": student["name"], "since": student["since"],
            "open_url": CB.view_url(student["student_id"]), **CB.board_row(CB.read())}


async def _row(coach, student: dict) -> dict:
    async with CO.in_student(coach, student["student_id"]):
        return await run_in_threadpool(_row_here, student)


def _order(row: dict) -> tuple:
    """Who needs a look first: the most red alerts, then yellow, then by name."""
    lv = [a.get("level") for a in row.get("alerts") or []]
    return (-lv.count("bad"), -lv.count("watch"), row.get("name") or "")


@router.get("/api/v1/coach/board")
async def board():
    _on()
    me = _me()
    view = await run_in_threadpool(CO.coach_view, me)          # 403 NOT_A_COACH
    rows = []
    for s in view["students"]:
        if s["status"] != "active" or s.get("disabled"):
            continue
        try:
            rows.append(await _row(me, {"student_id": s["student_id"], "name": s["name"], "since": s["since"]}))
        except CO.NotYourStudent:       # withdrawn between the list and the read
            continue
    rows.sort(key=_order)
    return JSONResponse({"students": rows, "used": view["used"], "max_students": view["max_students"],
                         "pending": sum(1 for s in view["students"] if s["status"] == "pending")},
                        headers=NO_STORE)


@router.get("/api/v1/coach/students/{student_id}/summary")
async def student_summary(student_id: str):
    _on()
    me = _me()
    await run_in_threadpool(CO.require_coach, me)
    info = await run_in_threadpool(CO.student_info, me, student_id)   # 403 NOT_YOUR_STUDENT
    return JSONResponse(await _row(me, {"student_id": info["student_id"], "name": info["name"],
                                        "since": info["since"]}), headers=NO_STORE)


@router.get("/api/v1/coach/board/page")
def board_page():
    _on()
    return render_page("coach_board")
