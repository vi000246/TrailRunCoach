"""
A multi-day trip imported from 疊石 Cairn by its share link (SP-537; research SP-522).

One way only — Cairn → TrailRunCoach (Cairn's rule 43: one machine times a stretch of trail). The
user pastes the link Cairn's 「分享」 gives (`https://map.yichlin.com/?gpx=<id>&share=<token>`, its
lib/share.ts shareUrl); the server reads, with the token, what anyone holding that link may read:

    GET <origin>/api/gpx/<id>?share=<token>          the route's GPX (application/gpx+xml)
    GET <origin>/api/gpx/<id>/meta?share=<token>     {ok, data: GpxMeta}: name, start_date (YYYY-MM-DD)…
    GET <origin>/api/pois?gpxId=<id>&share=<token>   {ok, data: [Poi]}: name, lat, lng, visits

A revoked / regenerated / wrong token answers 404 "no gpx <id>" (Cairn never tells "not yours" from
"not there"). The token is used for these three reads only: never stored, logged or sent back.

The nights (Cairn shared/src/schema.ts PoiVisitSchema, app/src/lib/eta-plan.ts): a visit is
{day: int ≥ 0 | null, kind: arrive | depart | pass, distanceM: m along Cairn's track | null (a point
anchored by coordinates), …}. 「☾ 在此過夜」 at a camp writes an `arrive` visit on day N there (and,
once the next morning's start time is picked, a `depart` visit on day N + 1). So a night is

    an `arrive` visit on day N when the trip goes on after it (a visit on a later day), or
    a `depart` visit on day N + 1 (not the first day) with no such arrive on day N.

The day end is where that is on OUR track: the POI's coordinates matched to the track (every pass,
course.wpt_passes), the pass nearest Cairn's distanceM when there is one, else the first pass after
the previous night; a POI off the track falls back to distanceM. The days' km / climb are then ours
(course.build_course smoothing), as for any GPX with day ends — never Cairn's numbers.
"""
from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

from backend.i18n import _

DEFAULT_ORIGIN = "https://map.yichlin.com"          # Cairn's only production host (= WX.CAIRN_ORIGIN)
ORIGIN_ENV = "TRC_CAIRN_ORIGIN"                     # another Cairn (tests: a local fake server)
TIMEOUT_S = 20.0
UA = "TrailRunCoach Cairn import (SP-537)"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_LINK = re.compile(r"https?://[^\s，。；、）)<>\"']+", re.I)


class CairnError(ValueError):
    """A message for the user; `kind`: link | gone | unreachable | bad."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def origin() -> str:
    return (os.environ.get(ORIGIN_ENV) or DEFAULT_ORIGIN).rstrip("/")


def route_url(gpx_id: str) -> str:
    """The route in Cairn without the token (what is kept: the owner opens it signed in)."""
    return f"{DEFAULT_ORIGIN}/?gpx={gpx_id}"


def parse_link(text: Optional[str]) -> tuple[str, str]:
    """(gpx id, share token) of a Cairn share link (pasted alone or inside some text). Only Cairn's
    host is accepted — the server never fetches a host the user typed."""
    host = urlparse(origin()).netloc.lower()
    for m in _LINK.finditer(text or ""):
        u = urlparse(m.group(0))
        if u.netloc.lower() != host:
            continue
        q = parse_qs(u.query)
        gid = (q.get("gpx") or [""])[0]
        tok = (q.get("share") or [""])[0]
        if not _ID.match(gid):
            continue
        if not _TOKEN.match(tok):
            raise CairnError("link", _("這個連結沒有分享碼（share=）：請在 Cairn 路線的「分享」複製完整的連結"))
        return gid, tok
    raise CairnError("link", _("不是疊石 Cairn 的分享連結：要像 https://…/?gpx=…&share=…（在 Cairn 路線的「分享」複製）"))


# ---- reading Cairn -----------------------------------------------------------------------------

def _get(client, path: str, params: dict):
    import httpx
    try:
        r = client.get(origin() + path, params=params)
    except httpx.HTTPError:
        raise CairnError("unreachable", _("連不上疊石 Cairn，請稍後再試"))
    if r.status_code in (401, 403, 404, 410):
        raise CairnError("gone", _("連結失效：這條路線的分享可能已經停止或重新產生，請到 Cairn 重新複製分享連結"))
    if r.status_code != 200:
        raise CairnError("unreachable", _("疊石 Cairn 暫時無法回應（HTTP {code}），請稍後再試", code=r.status_code))
    return r


def _data(r) -> object:
    try:
        js = r.json()
    except ValueError:
        raise CairnError("bad", _("疊石 Cairn 的回應讀不出來"))
    if not isinstance(js, dict) or js.get("ok") is not True:
        raise CairnError("bad", _("疊石 Cairn 的回應讀不出來"))
    return js.get("data")


def fetch(gpx_id: str, token: str, *, transport=None) -> dict:
    """{"gpx": bytes, "meta": dict, "pois": list} of the shared route. `transport`: an httpx transport
    (tests); redirects are not followed (the token goes to Cairn's host only)."""
    import httpx

    from backend.engine.racepower import gpx as GPX
    q = {"share": token}
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False, transport=transport,
                      headers={"User-Agent": UA}) as c:
        r = _get(c, f"/api/gpx/{gpx_id}", q)
        data = r.content
        if len(data) > GPX.MAX_BYTES:
            raise CairnError("bad", _("這條路線的 GPX 太大"))
        try:
            meta = _data(_get(c, f"/api/gpx/{gpx_id}/meta", q)) or {}
        except CairnError as e:
            if e.kind == "gone":
                raise
            meta = {}                         # the name / start date are a nicety
        pois = _data(_get(c, "/api/pois", {"gpxId": gpx_id, **q}))
    return {"gpx": data, "meta": meta if isinstance(meta, dict) else {}, "pois": pois if isinstance(pois, list) else []}


# ---- the nights ----------------------------------------------------------------------------------

def _int(v) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def night_visits(pois: list) -> list[dict]:
    """The overnight visits, one per night, by day: [{day (the day that ends), poi, km_cairn}]."""
    visits = []
    for p in pois or []:
        if not isinstance(p, dict):
            continue
        for v in p.get("visits") or []:
            if isinstance(v, dict) and _int(v.get("day")) is not None and v.get("kind") in ("arrive", "depart", "pass"):
                visits.append((p, v))
    if not visits:
        return []
    days = [v["day"] for _p, v in visits]
    first, last = min(days), max(days)
    nights: dict[int, dict] = {}
    for p, v in visits:                                   # arrive on day N, the trip goes on
        if v["kind"] == "arrive" and v["day"] < last:
            n = v["day"]
            if n not in nights:
                nights[n] = {"day": n, "poi": p, "km_cairn": _num(v.get("distanceM"))}
    for p, v in visits:                                   # depart on day N + 1 with no arrive on day N
        if v["kind"] == "depart" and v["day"] > first and v["day"] - 1 not in nights:
            n = v["day"] - 1
            nights[n] = {"day": n, "poi": p, "km_cairn": _num(v.get("distanceM"))}
    out = [nights[k] for k in sorted(nights)]
    for x in out:
        if x["km_cairn"] is not None:
            x["km_cairn"] /= 1000.0
    return out


def place_nights(nights: list[dict], track) -> list[dict]:
    """The nights on our track: [{night, day, km, name}], km along OUR distance, strictly increasing."""
    import numpy as np

    from backend.engine.racepower import course as CO
    td = CO.track_distance(track)
    d = td["d"]
    lat = np.asarray(track.lat, float)[td["keep"]]
    lon = np.asarray(track.lon, float)[td["keep"]]
    total = float(d[-1]) / 1000.0
    out, prev = [], 0.0
    for x in nights:
        p = x["poi"]
        plat, plon = _num(p.get("lat")), _num(p.get("lng"))
        passes = CO.wpt_passes(lat, lon, d, plat, plon) if plat is not None and plon is not None else []
        kms = [float(d[j]) / 1000.0 for j in passes]
        km = None
        if kms and x["km_cairn"] is not None:
            km = min(kms, key=lambda k: abs(k - x["km_cairn"]))
        elif kms:
            km = next((k for k in kms if k > prev + 0.05), kms[-1])
        elif x["km_cairn"] is not None:
            km = x["km_cairn"]
        if km is None or not (0.05 < km < total - 0.05) or km <= prev + 0.05:
            continue
        out.append({"night": len(out) + 1, "day": x["day"], "km": round(km, 2),
                    "name": str(p.get("name") or "")[:60]})
        prev = km
    return out


# ---- the timetable (SP-576 / 577; research SP-575 §1–§2) -------------------------------------------
#
# Every visit with a day and a time (`eta` 'HH:MM', local wall clock, no zone) is a row of the trip's
# timetable. Cairn says who wrote each time (`etaSource`): `estimate` = its own pace model, `user` = typed
# (or the start time picked in its estimate panel); `restMinutes` = the stop there (its estimate chain adds
# it before leaving, except at the day's first row). Read only — nothing is ever written back to Cairn.

LEGACY_BEFORE = "2026-08-11"     # Cairn's etaSource is newer: an older route's times all read `user`
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def eta_min(v) -> Optional[int]:
    """'HH:MM' → minutes after midnight; None when not a time."""
    m = _HHMM.match(v) if isinstance(v, str) else None
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def schedule_visits(pois: list) -> list[dict]:
    """The timed visits by (day, time): [{poi, day, eta, min, rest_min, src, kind, km_cairn}]."""
    out = []
    for p in pois or []:
        if not isinstance(p, dict):
            continue
        for v in p.get("visits") or []:
            if not isinstance(v, dict) or _int(v.get("day")) is None:
                continue
            m = eta_min(v.get("eta"))
            if m is None:
                continue
            rest = _int(v.get("restMinutes"))
            km = _num(v.get("distanceM"))
            out.append({"poi": p, "day": v["day"], "eta": v["eta"], "min": m,
                        "rest_min": max(0, rest) if rest is not None else 0,
                        "src": v.get("etaSource") if v.get("etaSource") in ("user", "estimate") else "user",
                        "kind": v.get("kind") if v.get("kind") in ("arrive", "depart", "pass") else "pass",
                        "km_cairn": km / 1000.0 if km is not None else None})
    out.sort(key=lambda x: (x["day"], x["min"], x["km_cairn"] if x["km_cairn"] is not None else 0.0))
    return out


def place_schedule(visits: list[dict], track, cairn_km: Optional[float] = None) -> tuple[list[dict], list[str]]:
    """The timed visits on OUR track: ([{name, day, eta, rest_min, src, kind, km}], [names left out]).
    As place_nights: the POI matched to every pass of the track, the pass nearest Cairn's own km
    (scaled by the two totals) when there is one, else the first pass from the previous row on; a row
    that would go back along the track (or is off it without a km) is left out."""
    import numpy as np

    from backend.engine.racepower import course as CO
    td = CO.track_distance(track)
    d = td["d"]
    lat = np.asarray(track.lat, float)[td["keep"]]
    lon = np.asarray(track.lon, float)[td["keep"]]
    total = float(d[-1]) / 1000.0
    scale = total / cairn_km if cairn_km and abs(total / cairn_km - 1.0) < 0.05 else 1.0
    out, skipped, prev = [], [], 0.0
    for x in visits:
        p = x["poi"]
        name = str(p.get("name") or "")[:60]
        plat, plon = _num(p.get("lat")), _num(p.get("lng"))
        passes = CO.wpt_passes(lat, lon, d, plat, plon) if plat is not None and plon is not None else []
        kms = [float(d[j]) / 1000.0 for j in passes]
        kc = x["km_cairn"] * scale if x["km_cairn"] is not None else None
        km = None
        if kms and kc is not None:
            km = min(kms, key=lambda k: abs(k - kc))
        elif kms:
            km = next((k for k in kms if k >= prev - 0.05), None)
        elif kc is not None:
            km = kc
        if km is None or km < prev - 0.2 or km > total + 0.05:
            skipped.append(name)
            continue
        km = min(max(km, prev), total)
        out.append({"name": name, "day": x["day"], "eta": x["eta"], "rest_min": x["rest_min"], "src": x["src"],
                    "kind": x["kind"], "km": round(km, 3)})
        prev = km
    return out, skipped


def signals(visits: list[dict], meta: dict) -> dict:
    """SP-577: is this the group's timetable? What the data says (SP-575 §1.2):
      user_days — days with ≥ 2 typed times besides the start: a timetable typed in (strong, for a
                  route made on or after LEGACY_BEFORE; older ones read `user` whatever wrote them);
      recruit   — the route is recruiting in Cairn right now (`meta.recruit`, weak: only while open);
      legacy    — made before LEGACY_BEFORE (`uploaded_at`): who wrote the times can't be told.
    `suggest`: the import's 「這是團的時刻表」 starts ticked — a signal and not legacy."""
    by_day: dict[int, list[dict]] = {}
    for v in visits:
        by_day.setdefault(v["day"], []).append(v)
    user_days = sorted(d for d, vs in by_day.items() if sum(1 for v in vs[1:] if v["src"] == "user") >= 2)
    n_user = sum(1 for v in visits if v["src"] == "user")
    up = str(meta.get("uploaded_at") or "")[:10]
    legacy = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", up)) and up < LEGACY_BEFORE
    recruit = bool(meta.get("recruit"))
    return {"n": len(visits), "n_user": n_user, "n_estimate": len(visits) - n_user, "user_days": user_days,
            "recruit": recruit, "legacy": legacy, "suggest": (bool(user_days) or recruit) and not legacy}


def clean_schedule(sc) -> Optional[dict]:
    """The timetable as kept with a trip (event_gpx source, SP-577): known keys, typed, capped — no token
    ever was in it. None when it isn't one."""
    if not isinstance(sc, dict) or not isinstance(sc.get("points"), list):
        return None
    pts = []
    for p in sc["points"][:200]:
        try:
            if eta_min(p.get("eta")) is None:
                continue
            pts.append({"name": str(p.get("name") or "")[:60], "day": int(p["day"]), "eta": p["eta"],
                        "rest_min": max(0, int(p.get("rest_min") or 0)),
                        "src": p.get("src") if p.get("src") in ("user", "estimate") else "user",
                        "kind": p.get("kind") if p.get("kind") in ("arrive", "depart", "pass") else "pass",
                        "km": round(float(p["km"]), 3)})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if len(pts) < 2:
        return None
    sg = sc.get("signals") if isinstance(sc.get("signals"), dict) else {}
    sig = {k: int(sg[k]) for k in ("n", "n_user", "n_estimate") if isinstance(sg.get(k), int)}
    sig.update({k: bool(sg.get(k)) for k in ("recruit", "legacy", "suggest")})
    sig["user_days"] = [int(x) for x in (sg.get("user_days") or []) if isinstance(x, int)][:20]
    return {"points": pts, "signals": sig, "group": bool(sc.get("group"))}


def schedule_of(pois: list, meta: dict, track) -> Optional[dict]:
    """The trip's timetable, None when no day has two times: {"points", "skipped", "signals", "group"}
    (`group`: the answer to 「這是團的時刻表」, the signals' suggestion until the user changes it)."""
    vis = schedule_visits(pois)
    days: dict[int, int] = {}
    for v in vis:
        days[v["day"]] = days.get(v["day"], 0) + 1
    if not any(n >= 2 for n in days.values()):
        return None
    cairn_km = _num(meta.get("distance_m"))
    pts, skipped = place_schedule(vis, track, cairn_km / 1000.0 if cairn_km else None)
    if not any(b["day"] == a["day"] and eta_min(b["eta"]) != eta_min(a["eta"]) and b["km"] > a["km"]
               for a, b in zip(pts, pts[1:])):
        return None                                   # no stretch with a time: not a timetable
    sig = signals(vis, meta)
    return {"points": pts, "skipped": skipped, "signals": sig, "group": sig["suggest"]}


def import_link(link: str, *, transport=None) -> dict:
    """The trip behind a share link: {"data": GPX bytes, "filename", "name", "gpx_id", "start_date",
    "nights": [...], "splits_km", "days", "source": {kind, gpx_id, url, name}, "schedule": the
    timetable or None (SP-576)} — no token in it.
    Raises CairnError with a message for the user."""
    from backend.engine.racepower import gpx as GPX
    gid, tok = parse_link(link)
    got = fetch(gid, tok, transport=transport)
    try:
        track = GPX.parse(got["gpx"], "cairn.gpx")
    except GPX.GpxError as e:
        raise CairnError("bad", _("Cairn 的 GPX 讀不出來：{e}", e=str(e)))
    meta = got["meta"]
    name = str(meta.get("name") or track.name or "Cairn")[:120]
    nights = place_nights(night_visits(got["pois"]), track)
    sd = meta.get("start_date")
    sched = schedule_of(got["pois"], meta, track)
    return {"schedule": sched, "data": got["gpx"], "filename": (re.sub(r"[\\/:*?\"<>|]+", "_", name) or "cairn")[:80] + ".gpx",
            "name": name, "gpx_id": gid, "start_date": sd if isinstance(sd, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sd) else None,
            "nights": nights, "splits_km": [n["km"] for n in nights], "days": len(nights) + 1,
            "source": {"kind": "cairn", "gpx_id": gid, "url": route_url(gid), "name": name}}


def note(imp: dict) -> Optional[str]:
    """What to tell the user besides the numbers: no night marked in Cairn."""
    if imp["nights"]:
        return None
    return _("這條 Cairn 行程沒有標過夜點（Cairn 的時間推估面板，營地那一列按「☾ 在此過夜」），只匯入了 GPX："
             "多日行程每天先平均分距離（推估），可以在 Cairn 標好再匯入一次，或在剖面上點每天的終點")
