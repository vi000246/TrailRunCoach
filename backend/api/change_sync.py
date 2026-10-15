"""
POST /api/v1/sync/changes — the change-log sync trial (SP-312, backend/db/changelog.py). 404 unless
the server sets TRC_CHANGE_SYNC=1; no page calls it (the app's own saving is unchanged). The tenant
is the request's (tenancy_mw: the signed-in user's, the owner's in owner mode); a write, so in
sign-in mode it needs the session and the CSRF header like every other write. Never in the demo.

Body:
    {"schema": 1, "device": "<8–64 of A-Z a-z 0-9 _ ->",
     "since": {"seq": 12, "cid": "…"} | null,        the cursor of the last pull (null: a new device)
     "changes": [{"id": "<8–36 chars, unique per change>", "ts": "2026-10-15T08:00:00.000Z",
                  "table": "user_settings" | "activity_tags", "row": "<key | tag uid>",
                  "col": "value" | <tag column> | "tags", "op": "set" | "add" | "remove",
                  "value": …}],
     "limit": 1000}                                    changes' worth of rows per pull (1–5000)
Answer:
    {"schema": 1, "results": [{"id", "seq", "status": applied | superseded} | {"id", "status":
     "refused", "code"}], "rows": [{"table", "row", "values"}], "cursor": {"seq", "cid"},
     "more": bool, "reset": bool, "snapshot": bool}
A device older than changelog.MIN_SCHEMA gets 426 SYNC_CLIENT_TOO_OLD (update the app first; its
queue is kept on the device); one newer than the server 409 SYNC_SERVER_TOO_OLD.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import changelog as CL
from backend.db.database import get_db
from backend.i18n import N_, UserError

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])

MAX_CHANGES = 500


class ChangeIn(BaseModel):
    id: str
    ts: str
    table: str
    row: str
    col: str
    op: str = "set"
    value: Any = None


class Cursor(BaseModel):
    seq: int = Field(ge=0)
    cid: Optional[str] = None


class SyncBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_version: int = Field(alias="schema")
    device: str
    since: Optional[Cursor] = None
    changes: list[ChangeIn] = Field(default_factory=list, max_length=MAX_CHANGES)
    limit: int = Field(default=1000, ge=1, le=5000)


@router.post("/changes")
async def sync_changes(body: SyncBody, db: AsyncSession = Depends(get_db)):
    if not CL.enabled():
        raise HTTPException(404, "NOT_FOUND")
    if body.schema_version < CL.MIN_SCHEMA:
        raise UserError(N_("這個 app 的版本太舊，請重新整理頁面更新後再同步"), code="SYNC_CLIENT_TOO_OLD",
                        status=426, min_schema=CL.MIN_SCHEMA, schema=CL.SCHEMA)
    if body.schema_version > CL.SCHEMA:
        raise UserError(N_("伺服器還沒更新到這個 app 的版本，修改先留在這台裝置，稍後再同步"),
                        code="SYNC_SERVER_TOO_OLD", status=409, schema=CL.SCHEMA)
    if not CL.valid_device(body.device):
        raise HTTPException(400, "INVALID_DEVICE")
    results = []
    if body.changes:
        await CL.take_write_lock(db)
        results = await CL.push(db, body.device, body.schema_version, [c.model_dump() for c in body.changes])
        await db.commit()
    got = await CL.pull(db, body.since.model_dump() if body.since else None, body.limit)
    return {"schema": CL.SCHEMA, "results": results, **got}
