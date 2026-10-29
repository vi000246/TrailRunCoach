"""
賽事管理「從我的疊石 Cairn 行程匯入」(SP-538, the "pull" half; SP-655 / 816 are the push).

The person's own Cairn trips, read server to server as the signed-in person (their Google sub) with
the outbound secret (links.out_secret: TRC_CAIRN_OUT_SECRET, as cairn_push.py — no Cairn user token
exists or is kept; no share link needed, SP-537's stays for someone else's trip):

  GET <TRC_CAIRN_URL>/api/internal/trc/trips?sub=        {ok, data: {sub, trips: [summary]}}
  GET <TRC_CAIRN_URL>/api/internal/trc/trips/<id>?sub=   {ok, data: {trip: summary, gpx, gpx_sha256,
                                                          pace_config, points: [point]}}; not theirs → 404

  summary  {id, name, version, source: trailruncoach | cairn, days, distance_m, gain_m, updated_at,
            group?}   (group: the trip carries a group's timetable — SP-582: never written back)
  point    {ref, name, kind: overnight | poi, distance_m, lat, lon, visits: [{day}]}

Cairn's API (its SP-657 branch) is not final: every name of it is read in this module only —
PATH_*, summary() and _detail() — so a change there is a change here.

An import stores the race's GPX as SP-537's does (event_gpx source kind "cairn", gpx_id = the trip
id, which is what PUT /trips/<id> takes: SP-816 pushes back onto it) plus `version` and via
"account". 「重新同步」: the trip's version in the list differs from the one kept → the race page
says so; pressing it imports the same trip again (held()).
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

import httpx

from backend.i18n import N_, UserError

log = logging.getLogger("backend.links")

PATH_LIST = "/api/internal/trc/trips"
PATH_TRIP = "/api/internal/trc/trips/{id}"
LIST_TIMEOUT_S = 10.0
TRIP_TIMEOUT_S = 20.0                  # a GPX of a few MB comes down in one answer
MAX_TRIPS = 500
VIA = "account"                        # source.via of an import from the person's own trips
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


# ---------------------------------------------------------------------------- Cairn's shapes (one place)

def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _version(v) -> Optional[str]:
    """A trip's version as kept and compared (a number or a string in Cairn's answer)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s[:40] or None


def summary(x) -> Optional[dict]:
    """One trip of Cairn's list (or a trip's own summary), the known keys typed; None when it has no
    usable id."""
    if not isinstance(x, dict):
        return None
    tid = x.get("id")
    if not isinstance(tid, str) or not _ID.match(tid):
        return None
    days = x.get("days")
    return {"id": tid, "name": str(x.get("name") or "")[:200], "version": _version(x.get("version")),
            "source": x.get("source") if x.get("source") in ("trailruncoach", "cairn") else None,
            "days": days if isinstance(days, int) and not isinstance(days, bool) and days > 0 else None,
            "distance_m": _num(x.get("distance_m")), "gain_m": _num(x.get("gain_m")),
            "updated_at": str(x["updated_at"])[:40] if x.get("updated_at") else None,
            "group": bool(x.get("group"))}


def _detail(data) -> dict:
    """A trip's answer: {trip: summary, gpx: str, gpx_sha256, points: [{name, km, day, overnight, lat,
    lng}]} — the overnight points only (kind overnight), by distance. `overnight`: the night n the point
    is Cairn's own stop for — a visit on day n and one on day n + 1 (SP-1016: pushing back, TRC's night n
    there is left to it); None when its visits don't say so."""
    if not isinstance(data, dict) or not isinstance(data.get("gpx"), str):
        raise _unreadable()
    trip = summary(data.get("trip"))
    if trip is None:
        raise _unreadable()
    nights = []
    for p in data.get("points") or []:
        if not isinstance(p, dict) or p.get("kind") != "overnight":
            continue
        days = [v["day"] for v in p.get("visits") or []
                if isinstance(v, dict) and isinstance(v.get("day"), int) and not isinstance(v.get("day"), bool)]
        lon = p.get("lon", p.get("lng"))
        m = _num(p.get("distance_m"))
        night = next((d for d in sorted(set(days)) if d >= 1 and d + 1 in days), None)
        nights.append({"name": str(p.get("name") or "")[:60], "km": m / 1000.0 if m is not None else None,
                       "day": min(days) if days else None, "overnight": night,
                       "lat": _num(p.get("lat")), "lng": _num(lon)})
    nights.sort(key=lambda n: (n["km"] is None, n["km"] or 0.0, n["day"] or 0))
    sha = data.get("gpx_sha256")
    return {"trip": trip, "gpx": data["gpx"],
            "gpx_sha256": sha.lower() if isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha) else None,
            "nights": nights}


# ---------------------------------------------------------------------------- calling Cairn

def _unreadable() -> UserError:
    return UserError(N_("疊石 Cairn 的回應看不懂，稍後再試"), code="CAIRN_DOWN", status=502)


def _down() -> UserError:
    return UserError(N_("疊石 Cairn 暫時連不上，稍後再試"), code="CAIRN_DOWN", status=502)


def _open(user) -> tuple[str, str]:
    """(Cairn's address, the outbound secret) for a linked person; UserError otherwise."""
    from backend.accounts import links as LK
    c = LK.CAIRN
    url, key = LK.base_url(c), LK.out_secret(c)
    if not (url and key and user.google_sub):
        raise UserError(N_("從疊石 Cairn 匯入還沒開放"), code="LINK_UNAVAILABLE", status=409)
    if not LK.is_linked(user, c):
        raise UserError(N_("先在設定連結疊石 Cairn 才能匯入你的行程"), code="NOT_LINKED", status=409,
                        link_url=LK.link_url(c))
    return url, key


def _get(user, path: str, timeout: float, trip: bool):
    """GET a Cairn internal route as `user`: its `data`, or the plain hint for its refusal (cairn_push's
    wording: timeout / 5xx / 401 → CAIRN_DOWN)."""
    from backend.accounts import links as LK
    url, key = _open(user)
    try:
        with httpx.Client(transport=LK.TRANSPORT, timeout=timeout, follow_redirects=False) as cl:
            r = cl.get(url + path, params={"sub": user.google_sub},
                       headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    except httpx.HTTPError as e:
        log.info("Cairn trips: %s", type(e).__name__)
        raise _down()
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code == 200:
        if not isinstance(j, dict) or j.get("ok") is not True:
            raise _unreadable()
        return j.get("data")
    msg = j.get("message") if isinstance(j, dict) else None
    log.info("Cairn trips: HTTP %d", r.status_code)
    if r.status_code == 404:
        from backend.accounts.cairn_push import CAIRN_NOT_FOUND_SUB
        if msg == CAIRN_NOT_FOUND_SUB:
            raise UserError(N_("疊石 Cairn 找不到你的帳號：先用同一個 Google 帳號登入 Cairn 一次"),
                            code="CAIRN_NO_ACCOUNT", status=409)
        if trip:
            raise UserError(N_("你的疊石 Cairn 裡找不到這條行程（可能已經刪掉）"), code="CAIRN_TRIP_GONE", status=409)
        raise _down()                       # the list route itself missing: Cairn not there yet
    if r.status_code == 403:
        raise UserError(N_("疊石 Cairn 不讓這裡讀你的行程（Cairn 的方案或權限）"), code="CAIRN_PLAN", status=403)
    if r.status_code == 429:
        raise UserError(N_("今天從疊石 Cairn 讀行程的次數用完了，明天再試"), code="CAIRN_QUOTA", status=429)
    raise _down()


def trips(user) -> list[dict]:
    """The person's trips in Cairn, as summary() (newest first as Cairn sends them)."""
    data = _get(user, PATH_LIST, LIST_TIMEOUT_S, trip=False)
    rows = data.get("trips") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise _unreadable()
    if isinstance(data.get("sub"), str) and data["sub"] != user.google_sub:
        log.warning("Cairn trips: an answer for another sub, dropped")
        raise _unreadable()
    return [s for s in (summary(x) for x in rows[:MAX_TRIPS]) if s]


def trip(user, trip_id: str) -> dict:
    """One of the person's trips: _detail()."""
    if not isinstance(trip_id, str) or not _ID.match(trip_id):
        raise UserError(N_("你的疊石 Cairn 裡找不到這條行程（可能已經刪掉）"), code="CAIRN_TRIP_GONE", status=409)
    d = _detail(_get(user, PATH_TRIP.format(id=trip_id), TRIP_TIMEOUT_S, trip=True))
    if d["trip"]["id"] != trip_id:
        raise _unreadable()
    return d


# ---------------------------------------------------------------------------- the import

def import_trip(user, trip_id: str) -> dict:
    """The trip as engine/cairn.import_link gives a share link's (api/plan.py applies either the same
    way): {"data", "filename", "name", "gpx_id", "start_date", "nights", "splits_km", "days",
    "source": {kind, gpx_id, url, name, version, via, group?}, "schedule": None}. The nights are the
    trip's overnight points placed on OUR track (cairn.place_nights), as SP-537's."""
    from backend.engine import cairn as CA
    from backend.engine.racepower import gpx as GPX
    d = trip(user, trip_id)
    data = d["gpx"].encode("utf-8")
    if len(data) > GPX.MAX_BYTES:
        raise UserError(N_("這條行程的 GPX 太大"), code="GPX_TOO_BIG", status=422)
    if d["gpx_sha256"] and hashlib.sha256(data).hexdigest() != d["gpx_sha256"]:
        log.warning("Cairn trips: the GPX does not match its sha256")
        raise _unreadable()
    try:
        track = GPX.parse(data, "cairn.gpx")
    except GPX.GpxError as e:
        raise UserError(N_("Cairn 的 GPX 讀不出來：{e}"), code="CAIRN_BAD_GPX", status=422, e=str(e))
    t = d["trip"]
    name = (t["name"] or track.name or "Cairn")[:120]
    raw = [{"day": n["day"] if n["day"] is not None else i, "km_cairn": n["km"], "overnight": n["overnight"],
            "poi": {"name": n["name"], "lat": n["lat"], "lng": n["lng"]}} for i, n in enumerate(d["nights"], start=1)]
    nights = CA.place_nights(raw, track)
    src = {"kind": "cairn", "gpx_id": t["id"], "url": CA.route_url(t["id"]), "name": name, "via": VIA,
           **({"version": t["version"]} if t["version"] else {}), **({"group": True} if t["group"] else {})}
    return {"schedule": None, "data": data,
            "filename": (re.sub(r"[\\/:*?\"<>|]+", "_", name) or "cairn")[:80] + ".gpx",
            "name": name, "gpx_id": t["id"], "start_date": None, "nights": nights,
            "splits_km": [n["km"] for n in nights], "days": len(nights) + 1, "source": src, "trip": t}


# ---------------------------------------------------------------------------- the races holding a trip

def held() -> dict[str, dict]:
    """{event id: {trip_id, version}} — the season plan's races whose GPX came from a Cairn trip."""
    from backend.engine import event_gpx as EG
    from backend.engine.planning import Plan
    try:
        ids = {e.id for e in Plan.load().events}
        rows = EG.all_rows()
    except Exception:                       # noqa: BLE001 — nothing held: nothing to compare
        return {}
    out = {}
    for eid, row in rows.items():
        src = (row or {}).get("source") or {}
        if eid in ids and src.get("kind") == "cairn" and src.get("gpx_id"):
            out[eid] = {"trip_id": src["gpx_id"], "version": src.get("version")}
    return out


def compare(listed: list[dict], have: dict[str, dict]) -> dict[str, dict]:
    """{event id: {trip_id, have, now, stale}} for each race holding one of the listed trips — stale:
    True the version in Cairn moved, False the same, None this race's import kept no version (a share
    link's, before SP-538)."""
    by_id = {t["id"]: t for t in listed}
    out = {}
    for eid, h in have.items():
        t = by_id.get(h["trip_id"])
        if t is None:
            continue
        stale = None if not h["version"] or not t["version"] else h["version"] != t["version"]
        out[eid] = {"trip_id": h["trip_id"], "have": h["version"], "now": t["version"], "stale": stale}
    return out


def after_push(eid: str, answer) -> None:
    """SP-816 pushed onto the trip the race came from: Cairn's version moved by our own write. When it
    moved by exactly one from the kept version, keep the new one (no 「Cairn 上改過了」 for our own
    push); anything else — a change made in Cairn meanwhile — stays to be synced."""
    from backend.engine import event_gpx as EG
    trip = ((answer or {}).get("data") or {}).get("trip") if isinstance(answer, dict) else None
    new = _version((trip or {}).get("version"))
    row = EG.get(eid) or {}
    old = (row.get("source") or {}).get("version")
    if not (new and old and old.isdigit() and new.isdigit() and int(new) == int(old) + 1):
        return
    try:
        EG.set_source(eid, version=new)
    except EG.EventGpxError:
        pass
