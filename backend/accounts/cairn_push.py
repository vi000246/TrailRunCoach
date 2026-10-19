"""
賽事頁「推到 Cairn」(SP-655 = SP-570 TRC-5; the "push" half of SP-538).

A season-plan event with its stored GPX and a saved calculation goes to 疊石 Cairn's internal API
(SP-657, Cairn v2/server/src/routes/trc.ts), as the signed-in person (their Google sub) with the
outbound secret (links.out_secret: TRC_CAIRN_OUT_SECRET, SP-329 Q1-1 — not the one Cairn calls here
with; no Cairn user token exists or is kept):

  first push   POST <TRC_CAIRN_URL>/api/internal/trc/trips?sub=   → 201 {ok, data: {trip: {id, …}}}
               the trip id is kept with the event (table cairn_push in the tenant's app DB)
  later        PUT  <TRC_CAIRN_URL>/api/internal/trc/trips/<id>?sub=   the same trip updated
               (Cairn's POST does not deduplicate, so a kept id is never POSTed again)

The body (payload): {name, gpx (the stored file as text, byte for byte), pace_config: {model:
{kind: "curve", curve}} (pace_config, below), points}. The curve is SP-653's cairn-curve v1 of the
saved calculation (cairn_curve.for_saved), sent as it is; the points are the
nights of that calculation (its day ends, curve.day_splits_km), each with a visit on the day it
ends and the next. Never an `eta` (Cairn's convention 43: times are Cairn's own estimate) — and
Cairn refuses one (400).

Who may: links.allowed(user, CAIRN, "link:gpx_sync") — linked, and this feature switched on by
the person (links.FEATURES: each feature asks on its own; the push dialog's 「同意並推送」 records
the same consents row as the switch in 設定) — and a paid plan here (link_calc.require_plan).
Cairn's own answers become plain hints: 403 its free plan, 429 today's pushes used up, 404 no Cairn
account for this Google account or the trip deleted there (the kept id is forgotten, the next push
creates a new trip — never by itself), anything else "Cairn can't be reached".
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

import httpx

from backend.i18n import N_, UserError, _

log = logging.getLogger("backend.links")

SCOPE = "link:gpx_sync"
TABLE = "cairn_push"
DDL = (f"CREATE TABLE IF NOT EXISTS {TABLE} (id INTEGER PRIMARY KEY, event_id VARCHAR(64) NOT NULL UNIQUE, "
       "trip_id VARCHAR(64) NOT NULL, gpx_sha256 VARCHAR(64), pushed_at DATETIME)")
TIMEOUT_S = 20.0                       # a GPX of a few MB goes up in one request
CAIRN_NOT_FOUND_SUB = "no cairn account for this sub"     # Cairn's TRC_NOT_LINKED message
NAME_NEAR_KM = 0.5                     # a camp / hut / Cairn night this close to a day end names it
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_LOCKS: dict = {}
_LOCKS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------- the kept trip id

def _db() -> Optional[Path]:
    try:
        from backend.db.database import db_path
        return db_path()
    except Exception:                       # noqa: BLE001
        return None


def get(eid: str) -> Optional[dict]:
    """{trip_id, gpx_sha256, at} of the event's last push, None when never pushed."""
    p = _db()
    if p is None or not p.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            r = con.execute(f"SELECT trip_id, gpx_sha256, pushed_at FROM {TABLE} WHERE event_id=?", (eid,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:                   # no table yet
        return None
    return {"trip_id": r[0], "gpx_sha256": r[1], "at": r[2]} if r else None


def history() -> list[dict]:
    """Every race pushed (設定 › Cairn, SP-666), newest first: {event_id, name, date, gone, trip_id,
    at, url}. A race since deleted from the season plan stays listed (its trip is still in Cairn):
    gone, no name."""
    from backend.engine import cairn as CA
    from backend.engine.planning import Plan
    p = _db()
    if p is None or not p.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = con.execute(f"SELECT event_id, trip_id, pushed_at FROM {TABLE} "
                               "ORDER BY pushed_at DESC, id DESC").fetchall()
        finally:
            con.close()
    except sqlite3.Error:                   # no table yet: nothing pushed
        return []
    if not rows:
        return []
    try:
        events = {e.id: e for e in Plan.load().events}
    except Exception:                       # noqa: BLE001 — the list shows without the names
        events = {}
    out = []
    for eid, tid, at in rows:
        ev = events.get(eid)
        out.append({"event_id": eid, "name": (ev.name if ev else "") or "", "date": ev.date if ev else None,
                    "gone": ev is None, "trip_id": tid, "at": at, "url": CA.route_url(tid)})
    return out


def _write(sql: str, args: tuple) -> None:
    p = _db()
    if p is None:
        raise UserError(N_("沒有資料庫"), code="NO_DB", status=500)
    con = sqlite3.connect(str(p))
    try:
        con.execute(DDL)
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def remember(eid: str, trip_id: str, sha: str) -> None:
    at = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    _write(f"INSERT INTO {TABLE} (event_id, trip_id, gpx_sha256, pushed_at) VALUES (?, ?, ?, ?) "
           "ON CONFLICT(event_id) DO UPDATE SET trip_id=excluded.trip_id, gpx_sha256=excluded.gpx_sha256, "
           "pushed_at=excluded.pushed_at", (eid, trip_id, sha, at))


def forget(eid: str) -> None:
    p = _db()
    if p is None or not p.exists():
        return
    try:
        _write(f"DELETE FROM {TABLE} WHERE event_id=?", (eid,))
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------------------- what is sent

def pace_config(curve: dict) -> dict:
    """The trip's pace_config carrying the curve — the one place its shape is decided (Cairn
    SP-656 / SP-657 store {model: {kind: "curve", curve}}; the curve goes as SP-653 builds it,
    start_times with nulls, rest_hint as an object and fallback_check included: Cairn takes it
    through)."""
    return {"model": {"kind": "curve", "curve": curve}}


def night_points(curve: dict, names=()) -> list[dict]:
    """The nights of the calculation as Cairn points: one per day end (curve.day_splits_km, on
    Cairn's own km), the day it ends and the next as its visits (Cairn's visits carry no kind from
    here: arrive / depart are Cairn's; no time ever). `names`: (km, name) of camps / huts / the
    nights marked in Cairn — the nearest within NAME_NEAR_KM names the night."""
    total_km = float(curve.get("total_m") or 0) / 1000.0
    out = []
    for i, km in enumerate(curve.get("day_splits_km") or [], start=1):
        km = float(km)
        if not 0 < km < total_km:
            continue
        near = [(abs(k - km), n) for k, n in names if n and abs(k - km) <= NAME_NEAR_KM]
        name = min(near)[1] if near else _("第 {n} 晚", n=i)
        out.append({"ref": f"night-{i}", "name": str(name)[:200], "distance_m": round(km * 1000.0, 1),
                    "visits": [{"day": i}, {"day": i + 1}]})
    return out


def _names(eid: str, scale: float) -> list[tuple[float, str]]:
    """(km on Cairn's measure, name) of the camp / hut waypoints and the nights marked in Cairn."""
    from backend.engine import event_gpx as EG
    out = []
    try:
        pv = EG.preview(eid) or {}
    except Exception:                       # noqa: BLE001 — a name is only a label
        pv = {}
    for x in pv.get("candidates") or []:
        if x.get("name"):
            out.append((float(x["km"]) * scale, str(x["name"])))
    for x in ((pv.get("source") or {}).get("nights") or []):
        if x.get("name"):
            out.append((float(x["km"]) * scale, str(x["name"])))
    return out


def _event(eid: str):
    from backend.engine.planning import Plan
    if not isinstance(eid, str) or not _ID.match(eid):
        raise UserError(N_("找不到這場賽事"), code="NOT_FOUND", status=404)
    for e in Plan.load().events:
        if e.id == eid:
            return e
    raise UserError(N_("找不到這場賽事"), code="NOT_FOUND", status=404)


def _curve(eid: str) -> dict:
    from backend.engine.racepower import cairn_curve as CC
    try:
        return CC.for_saved(eid)
    except CC.CurveError as e:
        raise UserError(str(e), code=e.code, status=409)


def _curve_of(body: dict) -> dict:
    return body["pace_config"]["model"]["curve"]


def payload(eid: str) -> dict:
    """The body of the push for the current tenant's event `eid` (UserError when it can't go)."""
    from backend.engine import event_gpx as EG
    ev = _event(eid)
    curve = _curve(eid)
    data = EG.read_bytes(eid)
    if data is None:
        raise UserError(N_("這場賽事沒有存 GPX"), code="NO_GPX", status=409)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise UserError(N_("這份 GPX 不是 UTF-8 文字檔，疊石 Cairn 收不了"), code="GPX_NOT_TEXT", status=409)
    # Cairn hashes the text it gets: it must be the very file the curve was computed on
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != str(curve.get("gpx_sha256", "")).lower():
        raise UserError(N_("賽事的 GPX 換過了：用新的 GPX 重算一次再推"), code="GPX_CHANGED", status=409)
    row = EG.get(eid) or {}
    trc_km = float(row.get("km") or 0)
    scale = (float(curve["total_m"]) / 1000.0 / trc_km) if trc_km > 0 else 1.0
    return {"name": (ev.name or row.get("filename") or eid)[:200], "gpx": text,
            "pace_config": pace_config(curve),
            "points": night_points(curve, _names(eid, scale))}


# ---------------------------------------------------------------------------- who may

def status(user, eid: str) -> dict:
    """What the race page shows for the event: may the button show, is it linked / allowed /
    paid, was it pushed (the trip and when), can it go now (or why not)."""
    from backend import membership as MS
    from backend.accounts import links as LK
    from backend.engine import cairn as CA
    c = LK.CAIRN
    _event(eid)
    linked = LK.is_linked(user, c)
    m = MS.member()
    out = {"available": bool(LK.out_secret(c) and LK.base_url(c) and user.google_sub),
           "linked": linked, "allowed": linked and LK.allowed(user, c, SCOPE),
           "plan_ok": m.role in (MS.SUBSCRIBER, MS.OWNER), "upgrade": m.role == MS.USER,
           "consent_text": _(LK.SCOPE_TEXT[SCOPE]), "link_url": LK.link_url(c),
           "allow_url": LK.link_url(c, SCOPE), "pushed": None, "ready": False, "why": None}
    got = get(eid)
    if got:
        out["pushed"] = {"trip_id": got["trip_id"], "at": got["at"], "url": CA.route_url(got["trip_id"])}
    try:
        _curve(eid)
        out["ready"] = True
    except UserError as e:
        out["why"] = e.message()
    return out


def _lock(user, eid: str) -> threading.Lock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault((user.id, eid), threading.Lock())


def push(user, eid: str, consent: bool = False) -> dict:
    """Create or update the event's trip in Cairn. {action: created | updated, trip_id, url}."""
    from backend.accounts import link_calc as LC, links as LK
    c = LK.CAIRN
    url, key = LK.base_url(c), LK.out_secret(c)
    if not (url and key and user.google_sub):
        raise UserError(N_("推到疊石 Cairn 還沒開放"), code="LINK_UNAVAILABLE", status=409)
    if not LK.is_linked(user, c):
        raise UserError(N_("先在設定連結疊石 Cairn 才能推"), code="NOT_LINKED", status=409, link_url=LK.link_url(c))
    _event(eid)
    LC.require_plan(SCOPE)
    if not LK.allowed(user, c, SCOPE):
        if not consent:
            raise UserError(N_("推之前要你同意：{text}"), code="CONSENT_REQUIRED", status=403,
                            text=_(LK.SCOPE_TEXT[SCOPE]), scope=SCOPE)
        LK.set_feature(user, c, SCOPE, True)
    lock = _lock(user, eid)
    if not lock.acquire(blocking=False):
        raise UserError(N_("正在推，請稍候"), code="BUSY", status=409)
    try:
        body = payload(eid)
        sha = str(_curve_of(body)["gpx_sha256"]).lower()
        kept = get(eid)
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        params = {"sub": user.google_sub}
        try:
            with httpx.Client(transport=LK.TRANSPORT, timeout=TIMEOUT_S, follow_redirects=False) as cl:
                if kept:
                    r = cl.put(f"{url}/api/internal/trc/trips/{kept['trip_id']}", params=params, json=body,
                               headers=headers)
                else:
                    r = cl.post(f"{url}/api/internal/trc/trips", params=params, json=body, headers=headers)
        except httpx.HTTPError as e:
            log.info("Cairn push: %s", type(e).__name__)
            raise UserError(N_("疊石 Cairn 暫時連不上，稍後再試"), code="CAIRN_DOWN", status=502)
        trip_id = _answer(r, eid, updating=bool(kept))
        if kept and trip_id is None:
            trip_id = kept["trip_id"]
        remember(eid, trip_id, sha)
        log.info("event pushed to Cairn (%s)", "updated" if kept else "created")
        from backend.engine import cairn as CA
        return {"action": "updated" if kept else "created", "trip_id": trip_id, "url": CA.route_url(trip_id),
                "points": len(body["points"])}
    finally:
        lock.release()


def _answer(r: httpx.Response, eid: str, updating: bool) -> Optional[str]:
    """Cairn's answer → the trip id, or the plain hint for its refusal."""
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code in (200, 201):
        trip = ((j.get("data") or {}).get("trip") or {}) if isinstance(j, dict) else {}
        tid = trip.get("id")
        if isinstance(tid, str) and _ID.match(tid):
            return tid
        if updating:
            return None
        raise UserError(N_("疊石 Cairn 的回應看不懂，稍後再試"), code="CAIRN_DOWN", status=502)
    msg = j.get("message") if isinstance(j, dict) else None
    log.info("Cairn push: HTTP %d", r.status_code)
    if r.status_code == 403:
        # SP-666 Q1-2: Cairn's own plan names (一般／輕度／重度會員, the 設定 › Cairn tab's)
        raise UserError(N_("你在疊石 Cairn 的方案不能推行程：要 Cairn 的輕度會員以上"), code="CAIRN_PLAN", status=403)
    if r.status_code == 429:
        raise UserError(N_("今天推到疊石 Cairn 的次數用完了，明天再推"), code="CAIRN_QUOTA", status=429)
    if r.status_code == 404:
        if msg == CAIRN_NOT_FOUND_SUB or not updating:
            raise UserError(N_("疊石 Cairn 找不到你的帳號：先用同一個 Google 帳號登入 Cairn 一次"),
                            code="CAIRN_NO_ACCOUNT", status=409)
        forget(eid)
        raise UserError(N_("疊石 Cairn 上的這條行程找不到了（可能已經刪掉）。再按一次會建一條新的。"),
                        code="CAIRN_TRIP_GONE", status=409)
    if r.status_code == 400:
        raise UserError(N_("疊石 Cairn 不收這份資料：{why}"), code="CAIRN_REJECTED", status=422,
                        why=str(msg or "")[:200])
    raise UserError(N_("疊石 Cairn 暫時連不上，稍後再試"), code="CAIRN_DOWN", status=502)
