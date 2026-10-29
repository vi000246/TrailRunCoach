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
  imported     SP-816: a GPX imported from a Cairn trip (event_gpx source kind "cairn") is never
               POSTed: the first push PUTs the trip it came from — pace and night points only, not
               its GPX or name — and that id is kept like any other. Cairn's 404 there (not this
               person's trip, or deleted) creates nothing by itself: CAIRN_ORIGIN_GONE, and the
               person may ask for a new trip ({new: true}). SP-1016: a night the trip already has its
               own overnight point for (day n and n + 1, within NAME_NEAR_KM) is not sent there

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
creates a new trip — never by itself), 409 "group schedule: …" the trip is marked the group's in Cairn
(SP-582 / SP-986: read only; CAIRN_GROUP, not "down"), anything else "Cairn can't be reached".
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
CAIRN_GROUP_PREFIX = "group schedule:"     # Cairn's 409 on PUT for a group's timetable (GROUP_SCHEDULE_READ_ONLY)
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


def events_of_trip(trip_id: str) -> list[str]:
    """SP-1018: the current tenant's events pushed to the Cairn trip `trip_id` (any still in the plan or
    not: the caller checks)."""
    p = _db()
    if p is None or not p.exists() or not isinstance(trip_id, str) or not _ID.match(trip_id):
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = con.execute(f"SELECT event_id FROM {TABLE} WHERE trip_id=?", (trip_id,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:                   # no table yet: nothing pushed
        return []
    return [r[0] for r in rows]


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
    scale = _scale(row, curve)
    return {"name": (ev.name or row.get("filename") or eid)[:200], "gpx": text,
            "pace_config": pace_config(curve),
            "points": night_points(curve, _names(eid, scale))}


def _scale(row: dict, curve: dict) -> float:
    """Our km → Cairn's km (the curve's total over the stored GPX's)."""
    trc_km = float(row.get("km") or 0)
    return (float(curve["total_m"]) / 1000.0 / trc_km) if trc_km > 0 else 1.0


def cairn_nights(eid: str, scale: float) -> list[tuple[int, float]]:
    """SP-1016: (night n, km on Cairn's measure) of the overnight points the trip the race's GPX was
    imported from already has — kept with the import (event_gpx source `nights`, their `overnight`: a
    point with a visit on day n and on day n + 1). A night kept before SP-1016 has no `overnight` key: its
    place among the nights (`night`) stands for it. `overnight: null` (not a night by its visits): none."""
    from backend.engine import event_gpx as EG
    try:
        src = (EG.get(eid) or {}).get("source") or {}
    except Exception:                       # noqa: BLE001 — no row / no table: nothing imported
        return []
    out = []
    for x in src.get("nights") or []:
        n = x.get("overnight") if "overnight" in x else x.get("night")
        if isinstance(n, int) and not isinstance(n, bool) and isinstance(x.get("km"), (int, float)):
            out.append((n, float(x["km"]) * scale))
    return out


def without_cairns_nights(points: list[dict], held: list[tuple[int, float]]) -> list[dict]:
    """SP-1016 (使用者 2026-10-29): pushing back onto the person's own trip, TRC's night n is not sent when
    Cairn's own point for night n (`held`, cairn_nights) is at the same place — within NAME_NEAR_KM, the
    radius a night takes its name by. Cairn makes a point of every ref TRC sends (its id hashes the gpx id
    and the ref), so a night of ours there would be a second one; Cairn's own point is never touched (it
    is not ours). A night Cairn has elsewhere (the two plans differ) is sent: both stay, as planned. And
    since Cairn's PUT drops TRC's points not sent again, a duplicate pushed before goes with this push."""
    if not held:
        return points
    out = []
    for p in points:
        m = re.fullmatch(r"night-(\d+)", str(p.get("ref") or ""))
        km = float(p.get("distance_m") or 0) / 1000.0
        if m and any(n == int(m.group(1)) and abs(k - km) <= NAME_NEAR_KM for n, k in held):
            continue
        out.append(p)
    return out


def origin(eid: str) -> Optional[dict]:
    """SP-816: the Cairn trip the event's GPX was imported from (event_gpx source kind "cairn":
    SP-537's share link, SP-676's import from the person's own trips) — {trip_id, url, name} — or
    None for an uploaded GPX. Cairn's trip id is its gpx id, the one PUT /trips/<id> takes."""
    from backend.engine import cairn as CA
    from backend.engine import event_gpx as EG
    try:
        src = (EG.get(eid) or {}).get("source") or {}
    except Exception:                       # noqa: BLE001 — no row / no table: not imported
        return None
    tid = src.get("gpx_id") if src.get("kind") == "cairn" else None
    if not isinstance(tid, str) or not _ID.match(tid):
        return None
    # SP-577 / SP-582: a trip answered 「這是團的時刻表」 is the group's, read only — never written back
    # SP-538: a trip Cairn lists with a group's timetable (summary.group) likewise
    group = bool((src.get("schedule") or {}).get("group")) or src.get("group") is True
    return {"trip_id": tid, "url": src.get("url") or CA.route_url(tid), "name": src.get("name") or "", "group": group}


def target(eid: str, new: bool = False) -> tuple[str, Optional[str]]:
    """Which Cairn trip the next push writes: ("kept", id) the trip pushed before; ("origin", id)
    the trip the GPX came from, not pushed yet (or pushed to that very trip before) — updated, not
    duplicated — unless it was answered 「這是團的時刻表」 (the group's: never written back, a trip
    of our own as before); ("new", None) a trip to create. `new`: the person asked for a new trip after the
    origin could not be updated (not theirs / deleted in Cairn)."""
    kept = get(eid)
    org = None if new else origin(eid)
    if org and org["group"]:                # the group's timetable: read only (SP-575 Q1), a trip of our own
        if kept and kept["trip_id"] == org["trip_id"]:
            return "new", None              # pushed there before it was marked the group's: not again
        org = None
    if kept and not (org and kept["trip_id"] == org["trip_id"]):
        return "kept", kept["trip_id"]
    if org:
        return "origin", org["trip_id"]
    return "new", None


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
    site_ok = bool(LK.out_secret(c) and LK.base_url(c))
    out = {"available": site_ok and bool(user.google_sub),
           # SP-610: an account without Google (e-mail sign-in, a test account) can't link Cairn for now
           "reason": "no_google" if site_ok and not user.google_sub else None,
           "linked": linked, "allowed": linked and LK.allowed(user, c, SCOPE),
           "plan_ok": m.role in (MS.SUBSCRIBER, MS.OWNER), "upgrade": m.role == MS.USER,
           "consent_text": _(LK.SCOPE_TEXT[SCOPE]), "link_url": LK.link_url(c),
           "allow_url": LK.link_url(c, SCOPE), "pushed": None, "ready": False, "why": None,
           # SP-816: the Cairn trip the GPX came from, and whether the next push updates it
           "origin": origin(eid), "target": target(eid)[0]}
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


def push(user, eid: str, consent: bool = False, new: bool = False) -> dict:
    """Create or update the event's trip in Cairn. {action: created | updated, trip_id, url, points,
    origin}. SP-816: a GPX imported from Cairn updates the trip it came from (origin: true) instead
    of creating a second one — only its pace and TRC's own night points: never its GPX file or its
    name (the person's trip; Cairn leaves their points and times alone either way). When Cairn
    answers that trip is not there for this person (someone else's shared trip, or deleted), nothing
    is created by itself (CAIRN_ORIGIN_GONE); `new` = the person then asked for a new trip."""
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
        kind, tid = target(eid, new)
        if kind == "origin":                # the person's own trip: its file and name stay theirs
            body = {k: v for k, v in body.items() if k not in ("gpx", "name")}
            # SP-1016: and the nights it already has a point for stay its own (not a second one of ours)
            from backend.engine import event_gpx as EG
            curve = _curve_of(body)
            body["points"] = without_cairns_nights(body["points"],
                                                   cairn_nights(eid, _scale(EG.get(eid) or {}, curve)))
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        params = {"sub": user.google_sub}
        try:
            with httpx.Client(transport=LK.TRANSPORT, timeout=TIMEOUT_S, follow_redirects=False) as cl:
                if tid:
                    r = cl.put(f"{url}/api/internal/trc/trips/{tid}", params=params, json=body, headers=headers)
                else:
                    r = cl.post(f"{url}/api/internal/trc/trips", params=params, json=body, headers=headers)
        except httpx.HTTPError as e:
            log.info("Cairn push: %s", type(e).__name__)
            raise UserError(N_("疊石 Cairn 暫時連不上，稍後再試"), code="CAIRN_DOWN", status=502)
        trip_id = _answer(r, eid, updating=bool(tid), origin=kind == "origin")
        if tid and trip_id is None:
            trip_id = tid
        remember(eid, trip_id, sha)
        if kind == "origin":                # SP-538: our own write moved the trip's version — not a change to sync
            from backend.accounts import cairn_trips as CT
            try:
                CT.after_push(eid, r.json())
            except ValueError:
                pass
        log.info("event pushed to Cairn (%s)", {"kept": "updated", "origin": "the trip it came from updated",
                                                "new": "created"}[kind])
        from backend.engine import cairn as CA
        return {"action": "updated" if tid else "created", "trip_id": trip_id, "url": CA.route_url(trip_id),
                "points": len(body["points"]), "origin": kind == "origin"}
    finally:
        lock.release()


def _answer(r: httpx.Response, eid: str, updating: bool, origin: bool = False) -> Optional[str]:
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
        if origin:                          # SP-816: someone else's shared trip, or deleted there
            raise UserError(N_("沒辦法更新 Cairn 上原本那條行程：它不是你的（例如別人分享給你的），或已經刪掉了。"
                               "要的話可以在你的 Cairn 另外建一條。"), code="CAIRN_ORIGIN_GONE", status=409)
        raise UserError(N_("疊石 Cairn 上的這條行程找不到了（可能已經刪掉）。再按一次會建一條新的。"),
                        code="CAIRN_TRIP_GONE", status=409)
    if r.status_code == 400:
        raise UserError(N_("疊石 Cairn 不收這份資料：{why}"), code="CAIRN_REJECTED", status=422,
                        why=str(msg or "")[:200])
    if r.status_code == 409 and updating and isinstance(msg, str) and msg.startswith(CAIRN_GROUP_PREFIX):
        # SP-986: the trip is marked 「這是跟團的時刻表」 in Cairn (SP-582) — read only, Cairn refused before
        # writing anything. The trip the GPX came from is now known as the group's: the next push makes a
        # trip of our own (target(), as for one known before); a trip we made and the person marked later
        # stays ours to update once they untick it there.
        if origin:
            from backend.engine import event_gpx as EG
            try:
                EG.set_source(eid, group=True)
            except EG.EventGpxError:
                pass
            raise UserError(N_("Cairn 上這條行程勾了「這是跟團的時刻表」，只能讀、這邊不會寫回去。"
                               "再按一次會在你的 Cairn 另外建一條。"), code="CAIRN_GROUP", status=409)
        raise UserError(N_("Cairn 上這條行程勾了「這是跟團的時刻表」，只能讀、這邊不會蓋掉它。"
                           "要推的話先在 Cairn 取消勾選。"), code="CAIRN_GROUP", status=409)
    raise UserError(N_("疊石 Cairn 暫時連不上，稍後再試"), code="CAIRN_DOWN", status=502)
