"""
腳程試算 (SP-805, engine/racepower/pace_estimate.py): a recorded hike + a route → how long that person
takes on it and how they compare with an ordinary hiker; signed in, also with yourself.

  GET  /share/pace            the page (static/pace.html). Under /share/ because the production site's
                              gate (Cloudflare Access) lets only /share/* through for a visitor who is not
                              signed in (deploy/commercial.md §0); like the share page it loads nothing
                              from /api/v1/static/ (i18n.js inline: pages.INLINE_I18N_PAGES, SP-698).
  POST /share/pace/estimate   anyone, signed in or not: multipart `track` (the recorded GPX / FIT, with
                              times), `route` (GPX / FIT), `pack_kg`, `trip_kind` (group | solo),
                              `route_pack_kg`, `route_days` → pace_estimate.estimate(). No tenant is read
                              (tenancy_mw gives /share/pace* the anonymous tenant).
  POST /api/v1/pace/me        the signed-in person (the owner in owner mode): multipart `route`,
                              `route_pack_kg`, `route_days`, `trip_kind` → the same estimate from their own
                              account (account_ability: the 百岳 calculator's walking curve and stop
                              profile), for the page's 「比你快／慢」. One use of the calculator's count for
                              that route that day (membership.consume("calc"), SP-713: the same route the
                              same day counts once, whichever entry computes it).

Abuse limits (the visitor's route): the body ≤ MAX_BODY (refused before parsing: Content-Length, then
while reading), each file ≤ MAX_FILE, at most PER_IP_BURST at once per client (ratelimit.client_ip, an
IPv6 client by its /64) refilled PER_IP_PER_H an hour, SITE_PER_H site-wide, GATE concurrent; 413 / 429
(Retry-After) / 503. Nothing is kept: the files are read into memory, parsed, dropped with the request —
no cache, no file, no log of their content (tests: test_pace_estimate_sp805.py).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.i18n import N_, UserError, _
from backend.i18n.pages import render_page
from backend.security import ratelimit as RL

log = logging.getLogger("backend.pace")
router = APIRouter(tags=["pace"], include_in_schema=False)

PAGE_PATH = "/share/pace"
ESTIMATE_PATH = "/share/pace/estimate"
ME_PATH = "/api/v1/pace/me"
MAX_FILE = 8 * 1024 * 1024           # a 10 h hike at 1 s is ~5 MB of GPX
MAX_BODY = 2 * MAX_FILE + 64 * 1024
PER_IP_BURST, PER_IP_PER_H = 6, 30   # 推估: a visitor trying a few tracks
SITE_PER_H = 600                     # 推估: the whole site an hour
PER_IP = RL.Buckets(rate=PER_IP_PER_H, per=3600, burst=PER_IP_BURST)
SITE = RL.Buckets(rate=SITE_PER_H, per=3600, burst=60)
GATE = RL.Gate(n=2, wait_s=10.0)
RETRY_S = 120
NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
PACK_MAX = 40.0


def reset_limits() -> None:
    PER_IP.reset()
    SITE.reset()


# ---------------------------------------------------------------------------- reading the upload

class Upload:
    """The multipart form, read with a size cap: fields (str) and files (bytes, name)."""

    def __init__(self, fields: dict, files: dict):
        self.fields, self.files = fields, files

    def num(self, key: str, lo: float, hi: float) -> Optional[float]:
        raw = (self.fields.get(key) or "").strip()
        if not raw:
            return None
        try:
            v = float(raw)
        except ValueError:
            raise UserError(N_("{field} 不是數字"), code="BAD_REQUEST", status=400, field=key) from None
        if not (lo <= v <= hi):
            raise UserError(N_("{field} 要在 {lo:g}–{hi:g} 之間"), code="BAD_REQUEST", status=400, field=key, lo=lo, hi=hi)
        return v

    def kind(self, key: str = "trip_kind") -> Optional[str]:
        v = (self.fields.get(key) or "").strip()
        if v and v not in ("group", "solo"):
            raise UserError(N_("{field} 只能是 group 或 solo"), code="BAD_REQUEST", status=400, field=key)
        return v or None

    def file(self, key: str) -> tuple[bytes, str]:
        got = self.files.get(key)
        if got is None or not got[0]:
            raise UserError(N_("缺少檔案：{field}"), code="FILE_REQUIRED", status=400, field=key)
        return got


def _too_large(max_file: int):
    return UserError(N_("檔案太大（每個最多 {mb} MB）"), code="TOO_LARGE", status=413, mb=max_file // (1024 * 1024))


async def read_upload(request: Request, max_body: int, max_file: int, file_keys=("track", "route")) -> Upload:
    """The request's multipart body, at most `max_body` bytes (413 before parsing anything larger) and
    `max_file` per file; files kept in memory only and returned as bytes."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_body:
        raise _too_large(max_file)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > max_body:
            raise _too_large(max_file)
    data = bytes(body)
    del body

    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}
    try:
        form = await Request(request.scope, receive).form(max_files=len(file_keys), max_fields=12,
                                                          max_part_size=64 * 1024)
    except Exception:                        # noqa: BLE001 — a malformed or oversized form
        raise UserError(N_("上傳的資料無法讀取"), code="BAD_REQUEST", status=400) from None
    fields, files = {}, {}
    try:
        for k, v in form.multi_items():
            if hasattr(v, "read"):
                if k not in file_keys or k in files:
                    continue
                b = await v.read(max_file + 1)
                if len(b) > max_file:
                    raise _too_large(max_file)
                files[k] = (b, v.filename or "")
            elif k not in fields:
                fields[k] = str(v)[:200]
    finally:
        await form.close()                  # the spooled parts gone with the request
    return Upload(fields, files)


def _error(e) -> HTTPException:
    return HTTPException(400, {"code": e.code, "message": str(e)})


# ---------------------------------------------------------------------------- the computation

def estimate_from_track(up: Upload) -> dict:
    """The uploaded track on the uploaded route (raises HTTPException 400 for a file that cannot be used)."""
    from backend.engine.racepower import pace_estimate as PE
    track, tname = up.file("track")
    route, rname = up.file("route")
    pack = up.num("pack_kg", 0.0, PACK_MAX)
    route_pack = up.num("route_pack_kg", 0.0, PACK_MAX)
    days = up.num("route_days", 1, PE.MAX_DAYS)
    kind = up.kind()
    try:
        ab = PE.track_ability(track, tname, pack, kind)
        rt = PE.route_of(route, rname, int(days) if days else None)
        return PE.estimate(ab, rt, route_pack)
    except PE.EstimateError as e:
        raise _error(e) from None


def estimate_from_account(up: Upload) -> dict:
    """The signed-in person's own estimate on the uploaded route (409 NO_DATA without a walking curve)."""
    from backend.api import racepower as RP
    from backend.engine.racepower import pace_estimate as PE
    route, rname = up.file("route")
    route_pack = up.num("route_pack_kg", 0.0, PACK_MAX)
    days = up.num("route_days", 1, PE.MAX_DAYS)
    kind = up.kind()
    try:
        rt = PE.route_of(route, rname, int(days) if days else None)
    except PE.EstimateError as e:
        raise _error(e) from None
    try:
        gm = RP._grade_models()
    except Exception:                        # noqa: BLE001 — no data to fit from: the person has no curve
        gm = {}
    try:
        th = RP._trail_hr()
    except Exception:                        # noqa: BLE001 — the stops then use the prior
        th = None
    try:
        ab = PE.account_ability(gm.get("hike_speed"), (th or {}).get("nonmoving"), kind,
                                (gm.get("hike_basis") or {}).get("solo_hikes"))
        out = PE.estimate(ab, rt, route_pack)
    except PE.EstimateError as e:
        raise HTTPException(409 if e.code == "NO_DATA" else 400, {"code": e.code, "message": str(e)}) from None
    return out


def _sanitize(out: dict) -> dict:
    """numpy numbers → plain JSON."""
    from backend.engine.racepower.calc import py
    return py(out)


# ---------------------------------------------------------------------------- the routes

@router.get(PAGE_PATH)
def pace_page():
    from backend.accounts import login as LG
    import json
    join = json.dumps(LG.join_link("share")).replace("<", "\\u003c")
    return render_page("pace", headers=NO_STORE, fill={"join": f"<script>window.TRC_JOIN = {join};</script>"})


def _limited(request: Request) -> Optional[str]:
    key = RL.limit_key(RL.client_ip(request.scope))
    if not PER_IP.take(key):
        return "client"
    if not SITE.take("site"):
        return "site"
    return None


@router.post(ESTIMATE_PATH)
async def pace_estimate(request: Request):
    who = _limited(request)
    if who:
        log.info("pace estimate refused: %s limit", who)
        return JSONResponse(status_code=429, headers={**NO_STORE, "Retry-After": str(RETRY_S)},
                            content={"detail": {"code": "RATE_LIMITED", "message": _("試算次數太多，請稍後再試")}})
    up = await read_upload(request, MAX_BODY, MAX_FILE)
    if not await GATE.acquire():
        return JSONResponse(status_code=503, headers={**NO_STORE, "Retry-After": "10"},
                            content={"detail": {"code": "BUSY", "message": _("伺服器忙碌，請稍後再試")}})
    try:
        out = await run_in_threadpool(estimate_from_track, up)
    finally:
        GATE.release()
    return JSONResponse(_sanitize(out), headers=NO_STORE)


@router.post(ME_PATH)
async def pace_me(request: Request):
    from backend import membership as MS
    from backend.accounts import login as LG
    if LG.required() and LG.current_user() is None:
        raise UserError(N_("請先登入"), code="LOGIN_REQUIRED", status=401)
    up = await read_upload(request, MAX_FILE + 64 * 1024, MAX_FILE, file_keys=("route",))
    route, _name = up.file("route")
    now = dt.datetime.now(dt.timezone.utc)
    item = f"{MS._period_key('day', now)}/gpx:{hashlib.sha1(route).hexdigest()}"   # = calc_route of that GPX
    out = await run_in_threadpool(estimate_from_account, up)      # a route that cannot be used counts nothing
    await run_in_threadpool(MS.consume, "calc", item, now)
    return JSONResponse(_sanitize(out), headers={"Cache-Control": "no-store"})
