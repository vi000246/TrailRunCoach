"""
管理 (SP-351): the server-wide settings and the users, for an admin.

  GET  /admin                              the page (403 with the page's own "admins only" text for anyone else)
  GET  /api/v1/admin/config                every site setting: value, where it came from, the plans' own;
                                           the data sources' switches            (scope admin:config)
  PUT  /api/v1/admin/config                {values: {key: value | null}, plans: {free: {key: value | null}}}
  PUT  /api/v1/admin/sources               {scope, on}: a data source on / off for the whole server
  GET  /api/v1/admin/users                 the users: plan, role, sign-up / last seen, last sync and its
                                           trouble, folder size                  (scope admin:users)
  PUT  /api/v1/admin/users/{id}            {disabled?, plan?, plan_expires_at?}
  GET  /api/v1/admin/waitlist              the waitlist and the cap              (scope admin:users)
  POST /api/v1/admin/waitlist/{id}/admit   let one person in: the account and its tenant folder now
  GET  /api/v1/admin/audit                 the change log, newest first          (either admin scope)

Test accounts (SP-611, accounts/testaccounts.py; scope admin:users):
  GET    /api/v1/admin/test-accounts             every test account, its made-up data's state, the choices
  POST   /api/v1/admin/test-accounts             {name, count?, mock?: {weeks, level}}: one or `count` accounts
                                                 ("<name> 1" …), the made-up data queued
  POST   /api/v1/admin/test-accounts/{id}/link   a one-time sign-in link (sign-in mode only): {url, expires_at}.
                                                 The URL is in this answer only: never logged, never stored
  DELETE /api/v1/admin/test-accounts/{id}        the account and all its data (its sessions and links too)
  PUT    /api/v1/admin/users/{id} {test}         mark an account someone signed up with a test account (or not)

Who: the membership scopes admin:config / admin:users (SP-330's table: the owner's role holds both;
and whoever the page made an admin, R2 below). Owner mode (no sign-in): every request is the owner, so the page works as before
for the one person there. Never mounted in the demo instance.

Every change is logged (site_config.audit): who (user id), when, what, the old and the new value.
Nothing of anyone's training data is read or returned: a user's row is their account (accounts.db),
the time of their last sync and the codes of its trouble (from sync_state's dates and the site's
pause lists — never a credential column), and the size of their folder.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path as Path_, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import membership as MS, site_config as SC, tenancy
from backend.api.debug import same_origin
from backend.i18n.pages import render_page

router = APIRouter(tags=["admin"], include_in_schema=False)
API = "/api/v1/admin"


def _need(*scopes: str):
    """A dependency: one of `scopes` (403 SCOPE_REQUIRED otherwise)."""
    def dep() -> None:
        if not any(MS.has(s) for s in scopes):
            raise MS.scope_error(scopes[0])
    dep.__name__ = "need_" + "_".join(s.replace(":", "_") for s in scopes)
    return dep


CONFIG = Depends(_need("admin:config"))
USERS = Depends(_need("admin:users"))
ANY = Depends(_need("admin:config", "admin:users"))
WRITE = Depends(same_origin)


def _actor() -> Optional[int]:
    return MS.member().user_id


def _accounts_on() -> bool:
    from backend import accounts
    return accounts.enabled()


# ---------------------------------------------------------------------------- the page

@router.get("/admin")
def admin_page():
    """The page; anyone without an admin scope gets it with status 403 (it then says so and loads
    nothing — every API below refuses them anyway)."""
    ok = any(MS.has(s) for s in MS.ADMIN_SCOPES)
    resp = render_page("admin")
    if not ok:
        resp.status_code = 403
    return resp


# ---------------------------------------------------------------------------- site settings

def _sources() -> list:
    off = MS.sources_off()
    from backend.sync import coros_limits as CL, garmin_limits as GL
    from backend import site_settings as SS
    out = []
    for s in MS.SOURCE_SCOPES:
        if s == "sync:tp_web":                    # the owner's own login, not a site-wide source
            continue
        row = {"scope": s, "on": s not in off}
        if s == "sync:coros_credential" and SS.get("coros.tripped"):
            row["tripped"] = True
        if s == "sync:garmin_trial" and SS.get("garmin.tripped"):
            row["tripped"] = True
        out.append(row)
    return out


def _config_view() -> dict:
    return {**SC.view(), "sources": _sources(), "accounts": _accounts_on()}


@router.get(API + "/config", dependencies=[CONFIG])
def get_config():
    return _config_view()


class ConfigIn(BaseModel):
    values: dict = {}
    plans: dict = {}


@router.put(API + "/config", dependencies=[CONFIG, WRITE])
def put_config(body: ConfigIn):
    try:
        SC.set_values(body.values, _actor(), plans=body.plans)
    except SC.ConfigError as e:
        raise HTTPException(400, {"code": e.code, "key": e.key})
    return _config_view()


class SourceIn(BaseModel):
    scope: str
    on: bool


@router.put(API + "/sources", dependencies=[CONFIG, WRITE])
def put_source(body: SourceIn):
    if body.scope not in MS.SOURCE_SCOPES or body.scope == "sync:tp_web":
        raise HTTPException(400, {"code": "NOT_A_SOURCE", "key": body.scope})
    was = body.scope not in MS.sources_off()
    actor = _actor()
    MS.set_source(body.scope, body.on, by=f"admin:{actor}")
    now = body.scope not in MS.sources_off()
    if was != now:
        SC.audit("source", body.scope, was, now, actor)
    return _config_view()


# ---------------------------------------------------------------------------- users

_SIZE_TTL_S = 600
_SIZES: dict = {}
_SKIP_OWNER = {"users", "base", "sandboxes", "accounts.db", "accounts.db-wal", "accounts.db-shm",
               "site.json", SC.AUDIT_FILE}


def _folder_bytes(root: Path, owner: bool) -> Optional[int]:
    """The size of a tenant's folder (cached a few minutes). The owner's folder is the server root
    too: the other users' folders and the server's own files are not counted."""
    key = str(root)
    hit = _SIZES.get(key)
    if hit and time.monotonic() - hit[1] < _SIZE_TTL_S:
        return hit[0]
    total = 0
    try:
        stack = [root]
        first = True
        while stack:
            d = stack.pop()
            with os.scandir(d) as it:
                for e in it:
                    if first and owner and e.name in _SKIP_OWNER:
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
            first = False
    except OSError:
        return None
    _SIZES[key] = (total, time.monotonic())
    return total


_SYNC_DATES = ("last_sync_at", "coros_last_sync_at", "garmin_last_sync_at", "intervals_last_sync_at")


def _sync_view(root: Path, tenant_id: str) -> dict:
    """The last sync and its trouble: sync_state's dates (read-only, never a credential column), the
    site's pause lists (COROS / Garmin 429, refused logins), intervals.icu's refused token, and how
    many activities wait in the failed list."""
    out: dict = {"last_sync_at": None, "problems": []}
    db = root / "wko5coach.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                row = con.execute(f"SELECT {', '.join(_SYNC_DATES)}, intervals_token_refused FROM sync_state "
                                  "ORDER BY athlete_id LIMIT 1").fetchone()
                if row:
                    dates = [str(v) for v in row[:len(_SYNC_DATES)] if v]
                    out["last_sync_at"] = max(dates) if dates else None
                    if row[len(_SYNC_DATES)]:
                        out["problems"].append({"code": "INTERVALS_REFUSED", "at": str(row[len(_SYNC_DATES)])})
                n = con.execute("SELECT count(*) FROM sync_failures WHERE kind = 'failed'").fetchone()[0]
                if n:
                    out["problems"].append({"code": "FAILED_ACTIVITIES", "n": int(n)})
            finally:
                con.close()
        except sqlite3.Error:
            pass
    from backend.sync import coros_limits as CL, garmin_limits as GL
    for code, p in (("COROS_PAUSED", CL.paused(tenant_id)), ("GARMIN_PAUSED", GL.paused(tenant_id))):
        if p:
            row = {"code": code}
            if isinstance(p, dict):
                row.update({k: p[k] for k in ("at", "reason", "status") if k in p})
            out["problems"].append(row)
    return out


def _user_row(u, seen: dict, role_now: str) -> dict:
    from backend.accounts import tenants as TN
    t = TN.tenant_of(u)
    try:
        root = t.root
    except Exception:                            # noqa: BLE001 — a user row without a folder
        root = None
    row = {"id": u.id, "email": u.email, "name": u.display_name, "role": role_now, "plan": u.plan,
           "plan_expires_at": u.plan_expires_at, "created_at": u.created_at, "last_login_at": u.last_login_at,
           "last_seen_at": seen.get(u.id), "disabled_at": u.disabled_at, "owner": u.is_owner, "test": u.is_test}
    if root is not None:
        row.update(_sync_view(root, t.id))
        row["bytes"] = _folder_bytes(root, u.is_owner) if root.exists() else 0
    row["admin"] = bool(getattr(u, "is_admin", u.is_owner))           # SP-351 R2
    row["me"] = u.id == _actor()                                       # SP-351 R2: no delete on your own row
    return row


@router.get(API + "/users", dependencies=[USERS])
def get_users():
    from backend.accounts import login as LG
    if not _accounts_on():
        return {"accounts": False, "users": [], "count": 0, "max_users": LG.max_users()}
    from backend.accounts import store
    seen = store.last_seen()
    users = store.users(include_disabled=True, include_test=True)
    # the cap's count: never a test account (SP-611)
    return {"accounts": True, "count": store.count_users(), "max_users": LG.max_users(),
            "users": [_user_row(u, seen, MS.role_of_user(u)) for u in users]}


class UserIn(BaseModel):
    disabled: Optional[bool] = None
    plan: Optional[str] = None
    plan_expires_at: Optional[str] = None
    test: Optional[bool] = None


@router.put(API + "/users/{uid}", dependencies=[USERS, WRITE])
def put_user(uid: int = Path_(ge=1, le=2 ** 62), body: UserIn = Body(...)):
    if not _accounts_on():
        raise HTTPException(404, {"code": "ACCOUNTS_OFF"})
    from backend.accounts import store
    u = store.get_user(uid)
    if u is None:
        raise HTTPException(404, {"code": "NO_USER"})
    actor = _actor()
    if body.test is not None and bool(body.test) != u.is_test:
        from backend.accounts import testaccounts as TA
        try:
            TA.mark(u, bool(body.test), actor)
        except TA.Refused as e:
            raise HTTPException(e.status, {"code": e.code})
        SC.audit("user.test", uid, u.is_test, bool(body.test), actor)
        u = store.get_user(uid)
    if body.disabled is not None and bool(body.disabled) != u.disabled:
        if u.is_owner or uid == actor:
            raise HTTPException(400, {"code": "CANNOT_DISABLE_SELF"})
        store.set_disabled(uid, bool(body.disabled), reason=f"admin:{actor}" if body.disabled else None)
        SC.audit("user.disabled", uid, u.disabled, bool(body.disabled), actor)
    if body.plan is not None or body.plan_expires_at is not None:
        plan = body.plan if body.plan is not None else u.plan
        exp = body.plan_expires_at if body.plan_expires_at is not None else u.plan_expires_at
        if exp == "":
            exp = None
        if exp is not None:
            import datetime as dt
            try:
                dt.datetime.fromisoformat(exp)
            except ValueError:
                raise HTTPException(400, {"code": "BAD_VALUE", "key": "plan_expires_at"})
        if plan not in ("free", "subscriber"):
            raise HTTPException(400, {"code": "BAD_VALUE", "key": "plan"})
        if u.is_owner:
            raise HTTPException(400, {"code": "OWNER_PLAN"})
        if (plan, exp) != (u.plan, u.plan_expires_at):
            store.set_plan(uid, plan, exp)
            SC.audit("user.plan", uid, {"plan": u.plan, "expires_at": u.plan_expires_at},
                     {"plan": plan, "expires_at": exp}, actor)
    seen = store.last_seen()
    nu = store.get_user(uid)
    return _user_row(nu, seen, MS.role_of_user(nu))


# ---------------------------------------------------------------------------- the waitlist

@router.get(API + "/waitlist", dependencies=[USERS])
def get_waitlist():
    from backend.accounts import login as LG
    if not _accounts_on():
        return {"accounts": False, "waitlist": [], "count": 0, "max_users": LG.max_users()}
    from backend.accounts import store
    return {"accounts": True, "waitlist": store.waitlist(), "count": store.count_users(),
            "max_users": LG.max_users()}


@router.post(API + "/waitlist/{wid}/admit", dependencies=[USERS, WRITE])
async def admit(wid: int = Path_(ge=1, le=2 ** 62)):
    """Let one waitlisted person in, past the cap: their account and tenant folder are made now;
    they sign in with the same Google e-mail and accept the terms."""
    if not _accounts_on():
        raise HTTPException(404, {"code": "ACCOUNTS_OFF"})
    from backend.accounts import store, tenants as TN
    u = store.admit_waitlisted(wid)
    if u is None:
        raise HTTPException(409, {"code": "NOT_WAITING"})
    if not u.is_owner:
        await TN.ensure_ready(TN.tenant_of(u))
    SC.audit("waitlist.admit", wid, None, {"user": u.id}, _actor())
    return {"user_id": u.id, **get_waitlist()}


# ---------------------------------------------------------------------------- the log

@router.get(API + "/audit", dependencies=[ANY])
def get_audit(limit: int = 200):
    return {"rows": SC.audit_rows(max(1, min(int(limit), SC.AUDIT_READ)))}


# ---------------------------------------------------------------------------- R2 (2026-10-15): admins, deleting, the Google client
#
#   PUT  /api/v1/admin/users/{id}/admin      {admin: bool}: make a user an admin or take it back (admin:users).
#                                            Never the owner (always an admin); never the last admin
#   POST /api/v1/admin/users/{id}/delete     {confirm: true}: delete the account and all its data — the same
#                                            steps as a person deleting their own (SP-386 / SP-326:
#                                            billing.cancel_for_deletion, then accounts/deletion.delete_account:
#                                            platforms disconnected, folder, research pool withdrawn, accounts.db
#                                            rows). Never the owner, never yourself (that is 設定's own flow)
#   GET  /api/v1/admin/google                the sign-in's Google client: the id in force and its source, the
#                                            secret only as set / not set (admin:config)
#   PUT  /api/v1/admin/google                {client_id?, client_secret?}: null / "" clears the page's value
#                                            (back to the environment); used from the next sign-in on

class AdminIn(BaseModel):
    admin: bool


@router.put(API + "/users/{uid}/admin", dependencies=[USERS, WRITE])
def put_user_admin(uid: int = Path_(ge=1, le=2 ** 62), body: AdminIn = Body(...)):
    if not _accounts_on():
        raise HTTPException(404, {"code": "ACCOUNTS_OFF"})
    from backend.accounts import store
    u = store.get_user(uid)
    if u is None:
        raise HTTPException(404, {"code": "NO_USER"})
    if u.is_owner:
        raise HTTPException(400, {"code": "OWNER_ROLE"})
    if bool(body.admin) != u.is_admin:
        try:
            store.set_admin(uid, bool(body.admin))
        except store.LastAdmin:
            raise HTTPException(409, {"code": "LAST_ADMIN"}) from None
        SC.audit("user.admin", uid, u.is_admin, bool(body.admin), _actor())
    nu = store.get_user(uid)
    return _user_row(nu, store.last_seen(), MS.role_of_user(nu))


class DeleteIn(BaseModel):
    confirm: bool = False


@router.post(API + "/users/{uid}/delete", dependencies=[USERS, WRITE])
async def delete_user(uid: int = Path_(ge=1, le=2 ** 62), body: DeleteIn = Body(...)):
    if not _accounts_on():
        raise HTTPException(404, {"code": "ACCOUNTS_OFF"})
    from backend.accounts import deletion as DEL, store
    from backend.billing import service as billing
    from backend.sync import runner
    u = store.get_user(uid)
    if u is None:
        raise HTTPException(404, {"code": "NO_USER"})
    actor = _actor()
    if u.is_owner or uid == 1:
        raise HTTPException(403, {"code": "OWNER_PROTECTED"})
    if uid == actor:
        raise HTTPException(400, {"code": "CANNOT_DELETE_SELF"})
    if not body.confirm:
        raise HTTPException(400, {"code": "CONFIRM_REQUIRED"})
    if store.is_last_admin(uid):
        raise HTTPException(409, {"code": "LAST_ADMIN"})
    old = {"plan": u.plan, "admin": u.is_admin, "disabled": u.disabled}
    await billing.cancel_for_deletion(u)              # a live subscription stops renewing first (SP-328)
    try:
        out = await DEL.delete_account(u)
    except runner.SyncBusy:
        raise HTTPException(409, {"code": "SYNC_BUSY"}) from None
    except ValueError:
        raise HTTPException(403, {"code": "OWNER_PROTECTED"}) from None
    SC.audit("user.delete", uid, old, {"deleted": True, "disconnected": out["disconnected"],
                                       "revoke_failed": out["revoke_failed"]}, actor)
    return {"ok": True, "id": uid, "disconnected": out["disconnected"], "revoked": out["revoked"],
            "revoke_failed": out["revoke_failed"]}


@router.get(API + "/google", dependencies=[CONFIG])
def get_google():
    from backend.accounts import google_client as GC
    return GC.view()


class GoogleIn(BaseModel):
    model_config = {"extra": "forbid"}
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


@router.put(API + "/google", dependencies=[CONFIG, WRITE])
def put_google(body: GoogleIn):
    from backend.accounts import google_client as GC
    try:
        GC.set_client(body.model_dump(exclude_unset=True), _actor())
    except GC.ClientError as e:
        raise HTTPException(400, {"code": e.code, "key": e.key})
    return GC.view()


# ---------------------------------------------------------------------------- test accounts (SP-611)

def _test_row(u, seen: dict) -> dict:
    from backend.accounts import testaccounts as TA
    return {"id": u.id, "name": u.display_name, "email": u.email, "plan": u.plan, "role": MS.role_of_user(u),
            "created_at": u.created_at, "last_seen_at": seen.get(u.id) or u.last_login_at,
            "disabled_at": u.disabled_at, **TA.view(u)}


def _test_user(uid: int):
    from backend.accounts import store
    u = store.get_user(uid)
    if u is None or not u.is_test:
        raise HTTPException(404, {"code": "NO_TEST_ACCOUNT"})
    return u


@router.get(API + "/test-accounts", dependencies=[USERS])
def get_test_accounts():
    from backend.accounts import login as LG, testaccounts as TA
    out = {"accounts": _accounts_on(), "signin": LG.required(), "weeks": list(TA.WEEKS),
           "default_weeks": TA.DEFAULT_WEEKS, "levels": list(TA.LEVELS), "default_level": TA.DEFAULT_LEVEL,
           "link_ttl_s": TA.LINK_TTL_S, "max_count": TA.MAX_BATCH, "rows": []}
    if not out["accounts"]:
        return out
    from backend.accounts import store
    seen = store.last_seen()
    out["rows"] = [_test_row(u, seen) for u in store.users(include_disabled=True, include_test=True) if u.is_test]
    return out


class TestAccountIn(BaseModel):
    name: str = ""
    count: int = 1
    mock: Optional[dict] = None


@router.post(API + "/test-accounts", dependencies=[USERS, WRITE])
def post_test_accounts(body: TestAccountIn):
    if not _accounts_on():
        raise HTTPException(404, {"code": "ACCOUNTS_OFF"})
    from backend.accounts import testaccounts as TA
    actor = _actor()
    try:
        mock = TA.mock_spec((body.mock or {}).get("weeks"), (body.mock or {}).get("level")) if body.mock else None
        name = TA.clean_name(body.name)
        if not 1 <= int(body.count) <= TA.MAX_BATCH:
            raise TA.Refused("BAD_COUNT")
        made = [TA.create(name if body.count == 1 else f"{name} {i}", actor=actor, mock=mock)
                for i in range(1, int(body.count) + 1)]
    except TA.Refused as e:
        raise HTTPException(e.status, {"code": e.code})
    for u in made:
        SC.audit("test.create", u.id, None, {"mock": mock}, actor)
    return {"created": [u.id for u in made], **get_test_accounts()}


@router.post(API + "/test-accounts/{uid}/link", dependencies=[USERS, WRITE])
def post_test_link(request: Request, uid: int = Path_(ge=1, le=2 ** 62)):
    from backend.accounts import google, login as LG, testaccounts as TA
    if not _accounts_on():
        raise HTTPException(404, {"code": "ACCOUNTS_OFF"})
    if not LG.required():
        raise HTTPException(409, {"code": "SIGNIN_OFF"})
    u = _test_user(uid)
    actor = _actor()
    try:
        token, exp = TA.make_link(u, actor)
    except TA.Refused as e:
        raise HTTPException(e.status, {"code": e.code})
    SC.audit("test.link", uid, None, {"expires_at": exp}, actor)
    base = google.public_url() or str(request.base_url).rstrip("/")
    return JSONResponse({"url": TA.link_url(token, base), "expires_at": exp}, headers={"Cache-Control": "no-store"})


@router.delete(API + "/test-accounts/{uid}", dependencies=[USERS, WRITE])
async def delete_test_account(uid: int = Path_(ge=1, le=2 ** 62)):
    from backend.accounts import testaccounts as TA
    from backend.sync import runner
    if not _accounts_on():
        raise HTTPException(404, {"code": "ACCOUNTS_OFF"})
    u = _test_user(uid)
    actor = _actor()
    if u.id == actor:
        raise HTTPException(400, {"code": "CANNOT_DELETE_SELF"})
    try:
        await TA.delete(u)
    except TA.Refused as e:
        raise HTTPException(e.status, {"code": e.code})
    except runner.SyncBusy:
        raise HTTPException(409, {"code": "SYNC_BUSY"})
    SC.audit("test.delete", uid, {"test": True}, None, actor)
    return get_test_accounts()
