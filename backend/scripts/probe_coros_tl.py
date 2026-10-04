"""
Read-only probe: where does COROS expose its Training Load (TL), and how
does it line up with the app's own TSS? (SP-37; feeds SP-38, which has to
send a COROS load target for a planned workout.)

Run it yourself, from the repo root, with the app's venv:

    python -m backend.scripts.probe_coros_tl --dry-run       # offline: what it would call / read
    python -m backend.scripts.probe_coros_tl                 # read-only probe (logged-in session)
    python -m backend.scripts.probe_coros_tl --local-only    # FITs + app TSS only, no COROS calls
    python -m backend.scripts.probe_coros_tl --allow-post-reads   # also the two POST *query* reads

What it reads
  1. COROS activity list   GET /activity/query (the app's sync call), every
     item's load-like fields (trainingLoad, aerobicEffect, ...);
  2. activity detail        /activity/detail/query for the --detail most
     recent activities (GET; COROS's web app sends it as POST, so it may
     only answer with --allow-post-reads);
  3. the FITs already downloaded (~/.wko5coach/fit/coros/): session fields,
     developer fields and unknown messages that could hold a load;
  4. planned workouts       GET /training/schedule/query (calendar programs
     carry trainingLoad) + GET /training/program/detail for the workouts the
     app pushed (coros_plan_push), joined to the app's planned TSS. With
     --allow-post-reads also the workout library (POST /training/program/query).
  Each activity is joined to the app's own TSS (power / rTSS / hrTSS,
  tss_source; the same Dataset the charts read) and HR-zone time on the
  COROS LTHR zones (for the zone-weight model of fit_tss_tl.py).

Safety
  * Uses the token the app stored at login (coros_client._get_token_and_base,
    auto_relogin=False): it never logs in (a login would end the session the
    app holds), never prints the token, and stops at the first "token invalid".
  * Every request goes through ReadOnlyCoros, which only knows the read paths
    in READ_GET / READ_POST; nothing creates, edits or deletes a workout,
    schedule entry or activity. POST is used only for the two query reads in
    READ_POST, and only with --allow-post-reads. The app DB is only read.
  * The output (default ~/.wko5coach/research/coros-tl/<timestamp>/, outside
    the repo) holds dates, sport codes, durations, distances, the app's TSS
    and the numeric COROS load-like fields — no token, account / activity
    ids, names, notes or GPS.

Output: activities.csv, planned.csv, summary.json; then
    python -m backend.scripts.fit_tss_tl <out>/activities.csv --planned <out>/planned.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

# read-only COROS endpoints; anything else is refused by ReadOnlyCoros
READ_GET = ("/activity/query", "/activity/detail/query", "/training/schedule/query",
            "/training/program/detail")
# read-only queries COROS's web app sends as POST (opt-in: --allow-post-reads)
READ_POST = ("/activity/detail/query", "/training/program/query")

LIST_PAGE = 20                  # same page size as the sync (coros_client.PAGE_SIZE)
PAUSE_S = 0.4                   # between calls: gentle on the unofficial API
SCHEDULE_CHUNK_DAYS = 28
DT_CAP_S = 10.0                 # a sample gap longer than this (pause) counts as this
# FIT messages never scanned (identity / device / definitions), and the size an
# unknown or developer value may have to be kept: a load is well below it, an
# id / serial / timestamp is not
FIT_SKIP_MESSAGES = frozenset({"record", "file_id", "file_creator", "device_info", "developer_data_id",
                               "field_description", "user_profile", "device_settings", "hrv", "gps_metadata"})
FIT_UNKNOWN_MAX = 50000.0

# a field is "load-like" when one of its name words is here ...
LOAD_WORDS = frozenset({"load", "tl", "trimp", "tss", "effect", "stress", "fatigue", "epoc", "te",
                        "recovery", "aerobic", "anaerobic", "intensity", "performance", "score",
                        "stamina", "strain", "training"})
# ... and none of these (identity / position / text: never written out)
DENY_WORDS = frozenset({"id", "ids", "name", "names", "user", "account", "email", "url", "lat", "lon",
                        "lng", "latitude", "longitude", "gps", "position", "nick", "avatar", "phone",
                        "serial", "token", "remark", "note", "notes", "title", "device", "address",
                        "city", "image", "pic", "photo"})
# plain activity basics from the list item (no names / ids / positions)
LIST_BASICS = ("sportType", "totalTime", "workoutTime", "distance", "avgHr", "avgHeartRate", "ascent",
               "calorie", "avgSpeed", "avgPower")
# per exercise of a planned program: structure only, no names / notes
EXERCISE_KEYS = ("exerciseType", "isGroup", "groupId", "sets", "targetType", "targetValue",
                 "intensityType", "intensityValue", "intensityValueExtend", "isIntensityPercent",
                 "intensityPercent", "intensityPercentExtend", "hrType", "intensityDisplayUnit")
PROGRAM_BASICS = ("sportType", "trainingLoad", "estimatedTime", "duration", "estimatedDistance", "distance",
                  "targetType", "targetValue", "exerciseNum", "totalSets", "estimatedType")

APP_COLS = ("n", "date", "in_app", "sport", "sport_type", "coros_sport_type", "duration_s", "moving_s",
            "distance_km", "climbing_m", "tss", "tss_source", "if", "hrtss", "hrif", "np", "avg_hr",
            "hr_coverage", "app_lthr", "coros_lthr", "zone_lthr",
            "z1_s", "z2_s", "z3_s", "z4_s", "z5_s", "z6_s")
PLANNED_COLS = ("n", "date", "origin", "execute_status", "coros_sport_type", "tl_schedule", "tl_detail",
                "estimated_time_s", "duration_s", "distance", "app_kind", "app_minutes", "app_planned_tss",
                "exercises")


# ---------------------------------------------------------------------------
# pure helpers (unit tested: backend/tests/test_coros_tl_probe.py)
# ---------------------------------------------------------------------------

def name_words(name: str) -> list[str]:
    """trainingLoad -> [training, load]; total_training_effect -> [...];
    unknown_143 -> [unknown, 143]."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return [w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w]


def is_denied(name: str) -> bool:
    return any(w in DENY_WORDS for w in name_words(name))


def is_load_like(name: str) -> bool:
    ws = name_words(name)
    return bool(ws) and any(w in LOAD_WORDS for w in ws) and not any(w in DENY_WORDS for w in ws)


def as_number(v: Any) -> Optional[float]:
    """A finite float from a number or a numeric string; None otherwise (bools too)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def load_fields(obj: Any, prefix: str = "", depth: int = 0, max_depth: int = 5,
                list_items: int = 2) -> dict[str, float]:
    """{dotted path: number} of every numeric load-like field in a JSON value.
    Dict keys that are all digits (ids used as keys) are not descended; list
    items are visited up to `list_items` (path `key[i]`)."""
    out: dict[str, float] = {}
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            k = str(k)
            if k.isdigit() or is_denied(k):
                continue
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.update(load_fields(v, path, depth + 1, max_depth, list_items))
            elif is_load_like(k):
                x = as_number(v)
                if x is not None:
                    out[path] = x
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:list_items]):
            if isinstance(v, (dict, list)):
                out.update(load_fields(v, f"{prefix}[{i}]", depth + 1, max_depth, list_items))
    return out


def key_paths(obj: Any, prefix: str = "", depth: int = 0, max_depth: int = 4) -> set[str]:
    """The field names of a JSON value (no values): `a.b`, lists as `a[]`,
    digit-only keys as `<n>`. For the summary's inventory."""
    out: set[str] = set()
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            k = "<n>" if str(k).isdigit() else str(k)
            path = f"{prefix}.{k}" if prefix else k
            out.add(path)
            out |= key_paths(v, path, depth + 1, max_depth)
    elif isinstance(obj, list):
        for v in obj[:3]:
            out |= key_paths(v, f"{prefix}[]", depth + 1, max_depth)
    return out


def list_basics(item: dict) -> dict[str, float]:
    out = {}
    for k in LIST_BASICS:
        x = as_number(item.get(k))
        if x is not None:
            out[f"list.{k}"] = x
    return out


def coros_day(v: Any) -> Optional[str]:
    """COROS YYYYMMDD (int / str) -> ISO date."""
    s = str(v or "")
    if len(s) == 8 and s.isdigit():
        try:
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:])).isoformat()
        except ValueError:
            return None
    return None


def label_of_file(name: str) -> Optional[str]:
    """COROS FIT file names start with the activity labelId (`<labelId>_<date>_<sport>.fit`)."""
    head = Path(name).name.split("_", 1)[0]
    return head if head.isdigit() else None


def zone_edges(lthr: float, ratios: Iterable[float]) -> list[int]:
    """The five inner COROS zone edges in bpm, rounded as COROS shows them
    (engine/hr_profile.py)."""
    return [int(round(lthr * r)) for r in ratios]


def zone_seconds(hr, dts, edges: list[int]) -> tuple[list[float], float, Optional[float]]:
    """([s in Z1..Z6], HR coverage = share of the recorded time with HR, time-weighted avg HR).
    Zone i+1 starts at edges[i]; a sample gap is capped at DT_CAP_S."""
    import numpy as np
    n = min(len(hr), len(dts))
    h = np.asarray([np.nan if v is None else v for v in list(hr)[:n]], dtype=float)
    d = np.asarray([np.nan if v is None else v for v in list(dts)[:n]], dtype=float)
    ok = np.isfinite(d) & (d > 0)
    d = np.where(ok, np.minimum(d, DT_CAP_S), 0.0)
    total = float(d.sum())
    has = ok & np.isfinite(h) & (h > 0)
    with_hr = float(d[has].sum())
    z = np.searchsorted(np.asarray(sorted(edges), dtype=float), h[has], side="right")
    zs = [float(d[has][z == i].sum()) for i in range(6)]
    cov = with_hr / total if total > 0 else 0.0
    avg = float((h[has] * d[has]).sum() / with_hr) if with_hr > 0 else None
    return zs, cov, avg


def exercise_summary(program: dict) -> str:
    """Compact JSON of a program's step structure (EXERCISE_KEYS only)."""
    rows = []
    for ex in program.get("exercises") or []:
        if isinstance(ex, dict):
            rows.append({k: ex.get(k) for k in EXERCISE_KEYS if ex.get(k) is not None})
    return json.dumps(rows, separators=(",", ":"), ensure_ascii=False)


def fit_candidates(messages: Iterable[tuple[str, list[tuple[str, Any, bool]]]]) -> tuple[dict, dict]:
    """From one FIT's messages [(message name, [(field, value, is_dev)])]: the
    numeric fields that could hold a load ({`fit.<msg>.<field>`: value}) and
    the message-name counts. Candidates: in `session` every load-like,
    unknown or developer field; in any other message that occurs once, its
    load-like / unknown / developer fields. FIT_SKIP_MESSAGES are never read,
    and an unknown / developer value above FIT_UNKNOWN_MAX is dropped (ids,
    serials and timestamps are that large; a load is not)."""
    counts: dict[str, int] = {}
    msgs = []
    for name, fields in messages:
        counts[name] = counts.get(name, 0) + 1
        if name not in FIT_SKIP_MESSAGES:
            msgs.append((name, fields))
    out: dict[str, float] = {}
    for name, fields in msgs:
        if name != "session" and counts[name] != 1:
            continue
        for f, v, dev in fields:
            f = str(f)
            if is_denied(f):
                continue
            known_load = is_load_like(f) and not f.startswith("unknown")
            if not (dev or f.startswith("unknown") or known_load):
                continue
            x = as_number(v)
            if x is None or (not known_load and abs(x) > FIT_UNKNOWN_MAX):
                continue
            out.setdefault(f"fit.{name}.{f}", x)
    return out, counts


def pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / math.sqrt(sxx * syy)


def _median(v: list[float]) -> Optional[float]:
    v = sorted(v)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def summarize(rows: list[dict], min_n: int = 5) -> dict:
    """Which COROS load-like columns exist, how many rows have them, and a first
    look: Pearson r and median ratio TL/TSS per tss_source (and vs hrTSS on
    every row), plus the FIT fields that track the best COROS column."""
    cols: dict[str, int] = {}
    for r in rows:
        for k, v in r.items():
            if k.startswith(("list.", "detail.", "fit.")) and v is not None:
                cols[k] = cols.get(k, 0) + 1
    tl_cols = sorted([c for c in cols if c.startswith(("list.", "detail.")) and is_load_like(c.split(".")[-1])],
                     key=lambda c: -cols[c])
    groups = sorted({r.get("tss_source") for r in rows if r.get("tss_source")})
    first_look = {}
    for c in tl_cols:
        res = {}
        for g in groups + ["hrtss(all)"]:
            xk = "hrtss" if g == "hrtss(all)" else "tss"
            sub = [r for r in rows if (g == "hrtss(all)" or r.get("tss_source") == g)
                   and as_number(r.get(xk)) is not None and as_number(r.get(c)) is not None]
            if len(sub) < min_n:
                continue
            xs = [as_number(r[xk]) for r in sub]
            ys = [as_number(r[c]) for r in sub]
            ratios = [y / x for x, y in zip(xs, ys) if x > 0]
            res[g] = {"n": len(sub), "r": _round(pearson(xs, ys)), "median_tl_per_tss": _round(_median(ratios))}
        if res:
            first_look[c] = res
    # FIT fields that look like the COROS TL (high r against the best COROS column)
    fit_match = []
    if tl_cols:
        best = tl_cols[0]
        for c in cols:
            if not c.startswith("fit."):
                continue
            sub = [(as_number(r.get(c)), as_number(r.get(best))) for r in rows]
            sub = [p for p in sub if p[0] is not None and p[1] is not None]
            if len(sub) < min_n:
                continue
            rr = pearson([p[0] for p in sub], [p[1] for p in sub])
            if rr is not None and abs(rr) >= 0.8:
                fit_match.append({"field": c, "vs": best, "n": len(sub), "r": _round(rr),
                                  "median_ratio": _round(_median([p[0] / p[1] for p in sub if p[1]]))})
        fit_match.sort(key=lambda d: -abs(d["r"]))
    return {"columns": dict(sorted(cols.items(), key=lambda kv: -kv[1])), "tl_columns": tl_cols,
            "first_look": first_look, "fit_fields_like_tl": fit_match[:20]}


def _round(x: Optional[float], nd: int = 3) -> Optional[float]:
    return None if x is None else round(x, nd)


def write_csv(path: Path, rows: list[dict], fixed: tuple) -> list[str]:
    """Fixed columns first, then every extra column (sorted). Returns the header."""
    extra = sorted({k for r in rows for k in r} - set(fixed))
    header = list(fixed) + extra
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in header})
    return header


# ---------------------------------------------------------------------------
# COROS (read-only)
# ---------------------------------------------------------------------------

class TokenRejected(RuntimeError):
    pass


class ReadOnlyCoros:
    """The only way this script talks to COROS: GET on READ_GET, POST on
    READ_POST when allowed. Never raises for a refused read (returns
    (None, why)); raises TokenRejected when COROS says the token is invalid."""

    def __init__(self, token: str, base: str, user_id: str, allow_post: bool = False,
                 pause_s: float = PAUSE_S):
        self._token, self._base, self._user_id = token, base, user_id
        self.allow_post = allow_post
        self.pause_s = pause_s
        self.calls: list[tuple[str, str, str]] = []     # (method, path, outcome) — no params

    def __repr__(self) -> str:                          # never shows the token
        return f"ReadOnlyCoros(base={self._base!r}, allow_post={self.allow_post})"

    async def read(self, method: str, path: str, *, params: Optional[dict] = None,
                   body: Any = None) -> tuple[Any, str]:
        method = method.upper()
        if method == "GET":
            if path not in READ_GET:
                raise PermissionError(f"not a read path: GET {path}")
        elif method == "POST":
            if path not in READ_POST or not self.allow_post:
                raise PermissionError(f"POST {path} is not allowed (read queries need --allow-post-reads)")
        else:
            raise PermissionError(f"{method} is never used")
        from backend.sync import http
        from backend.sync.coros_client import _headers, token_invalid
        if self.calls and self.pause_s:
            await asyncio.sleep(self.pause_s)
        try:
            async with http.client(timeout=30) as c:
                r = await c.request(method, self._base + path, params=params,
                                    json=body if method == "POST" else None,
                                    headers=_headers(self._token, self._user_id))
        except Exception as e:                           # noqa: BLE001 — reported, not raised
            self.calls.append((method, path, type(e).__name__))
            return None, type(e).__name__
        if r.status_code in (401, 403):
            self.calls.append((method, path, f"HTTP {r.status_code}"))
            raise TokenRejected(f"HTTP {r.status_code}")
        if r.status_code != 200:
            self.calls.append((method, path, f"HTTP {r.status_code}"))
            return None, f"HTTP {r.status_code}"
        try:
            b = r.json()
        except ValueError:
            self.calls.append((method, path, "not JSON"))
            return None, "not JSON"
        if b.get("result") != "0000":
            why = f"result={b.get('result')} {str(b.get('message') or '')[:80]}".strip()
            self.calls.append((method, path, why))
            if token_invalid(b):
                raise TokenRejected(why)
            return None, why
        self.calls.append((method, path, "ok"))
        return b.get("data"), "ok"


async def list_activities(api: ReadOnlyCoros, since: dt.date, until: dt.date) -> tuple[list[dict], str]:
    items: list[dict] = []
    page = 1
    while True:
        data, why = await api.read("GET", "/activity/query", params={
            "size": LIST_PAGE, "pageNumber": page,
            "startDay": since.strftime("%Y%m%d"), "endDay": until.strftime("%Y%m%d")})
        if data is None:
            return items, why
        got = (data.get("dataList") or data.get("list") or []) if isinstance(data, dict) else []
        items.extend(x for x in got if isinstance(x, dict))
        if len(got) < LIST_PAGE:
            return items, "ok"
        page += 1


async def activity_detail(api: ReadOnlyCoros, label: str, sport_type: Any) -> tuple[Optional[dict], str]:
    params = {"labelId": label, "sportType": sport_type, "screenW": 944, "screenH": 1414}
    data, why = await api.read("GET", "/activity/detail/query", params=params)
    if data is None and api.allow_post:
        data, why = await api.read("POST", "/activity/detail/query", params=params)
    return (data if isinstance(data, dict) else None), why


async def probe_remote(api: ReadOnlyCoros, since: dt.date, until: dt.date, n_detail: int,
                       plan_from: dt.date, plan_to: dt.date, pushed: list[dict],
                       max_programs: int = 40) -> dict:
    """All COROS reads. Returns {"activities": {label: {...}}, "planned": [...],
    "inventory": {...}, "notes": [...]} — still keyed by labelId in memory;
    ids are dropped when written."""
    notes: list[str] = []
    inv: dict[str, set] = {"list_item": set(), "detail": set(), "schedule_program": set(),
                           "program_detail": set(), "library_program": set()}
    acts: dict[str, dict] = {}
    items, why = await list_activities(api, since, until)
    if why != "ok":
        notes.append(f"activity list stopped: {why}")
    for it in items:
        label = str(it.get("labelId") or "")
        if not label:
            continue
        inv["list_item"] |= key_paths(it, max_depth=2)
        row = {"date": coros_day(it.get("date")), "coros_sport_type": as_number(it.get("sportType")),
               "_sport_raw": it.get("sportType")}
        row.update(list_basics(it))
        row.update({f"list.{k}": v for k, v in load_fields(it).items()})
        acts[label] = row
    # detail of the most recent ones
    recent = sorted(acts.items(), key=lambda kv: kv[1].get("date") or "", reverse=True)[:max(0, n_detail)]
    fails = 0
    for label, row in recent:
        d, why = await activity_detail(api, label, row.get("_sport_raw"))
        if d is None:
            fails += 1
            if fails >= 2 and not any(r.get("_detail") for _, r in recent):
                notes.append(f"activity detail refused ({why}); "
                             + ("" if api.allow_post else "COROS may want POST: rerun with --allow-post-reads"))
                break
            continue
        row["_detail"] = True
        inv["detail"] |= key_paths(d, max_depth=3)
        row.update({f"detail.{k}": v for k, v in load_fields(d).items()})
    # planned workouts on the calendar
    planned: list[dict] = []
    seen_programs: set[str] = set()
    by_iip = {str(p.get("id_in_plan")): p for p in pushed if p.get("id_in_plan")}
    start = plan_from
    while start <= plan_to:
        end = min(plan_to, start + dt.timedelta(days=SCHEDULE_CHUNK_DAYS - 1))
        data, why = await api.read("GET", "/training/schedule/query", params={
            "startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "supportRestExercise": 1})
        start = end + dt.timedelta(days=1)
        if not isinstance(data, dict):
            notes.append(f"schedule query refused ({why})")
            continue
        ents = {str(e.get("idInPlan")): e for e in data.get("entities") or [] if isinstance(e, dict)}
        for p in data.get("programs") or []:
            if not isinstance(p, dict):
                continue
            inv["schedule_program"] |= key_paths(p, max_depth=2)
            iip = str(p.get("idInPlan"))
            e = ents.get(iip, {})
            mine = by_iip.get(iip)
            day = coros_day(e.get("happenDay"))
            if mine and mine.get("day") and day and mine["day"] != day:
                mine = None
            key = f"sched:{day}:{iip}"
            if key in seen_programs:
                continue
            seen_programs.add(key)
            planned.append(_planned_row(p, day, "app_push" if mine else "calendar", e.get("executeStatus"),
                                        mine, tl_schedule=as_number(p.get("trainingLoad"))))
    # detail of the programs the app pushed (library copies)
    n = 0
    for mine in pushed:
        pid = mine.get("program_id")
        if not pid or n >= max_programs:
            continue
        n += 1
        d, why = await api.read("GET", "/training/program/detail", params={"id": pid, "supportRestExercise": 1})
        if not isinstance(d, dict):
            continue
        inv["program_detail"] |= key_paths(d, max_depth=2)
        tl = as_number(d.get("trainingLoad"))
        hit = next((r for r in planned if r.get("_push_key") == mine.get("session_key")), None)
        if hit is not None:
            hit["tl_detail"] = tl
        else:
            planned.append(_planned_row(d, mine.get("day"), "app_push_library", None, mine, tl_detail=tl))
    # the workout library (hand-made workouts): POST query, opt-in
    if api.allow_post:
        data, why = await api.read("POST", "/training/program/query", body={
            "name": "", "supportRestExercise": 1, "startNo": 0, "limitSize": 100, "sportType": 0})
        progs = data if isinstance(data, list) else (data or {}).get("list") if isinstance(data, dict) else None
        if progs is None:
            notes.append(f"library query refused ({why})")
        for p in progs or []:
            if isinstance(p, dict):
                inv["library_program"] |= key_paths(p, max_depth=2)
                planned.append(_planned_row(p, None, "library", None, None,
                                            tl_schedule=as_number(p.get("trainingLoad"))))
    return {"activities": acts, "planned": planned,
            "inventory": {k: sorted(v) for k, v in inv.items()}, "notes": notes}


def _planned_row(p: dict, day: Optional[str], origin: str, execute_status: Any, mine: Optional[dict],
                 tl_schedule: Optional[float] = None, tl_detail: Optional[float] = None) -> dict:
    row = {"date": day, "origin": origin, "execute_status": as_number(execute_status),
           "coros_sport_type": as_number(p.get("sportType")), "tl_schedule": tl_schedule, "tl_detail": tl_detail,
           "estimated_time_s": as_number(p.get("estimatedTime")), "duration_s": as_number(p.get("duration")),
           "distance": as_number(p.get("distance")), "exercises": exercise_summary(p)}
    for k, v in load_fields(p, list_items=0).items():       # program-level load-like fields only
        row[f"program.{k}"] = v
    if mine:
        row.update({"app_kind": mine.get("kind"), "app_minutes": mine.get("minutes"),
                    "app_planned_tss": mine.get("tss"), "_push_key": mine.get("session_key")})
    return row


# ---------------------------------------------------------------------------
# local: the app DB (read), the Dataset, the FITs
# ---------------------------------------------------------------------------

async def _session_and_pushes(athlete_id: int, need_token: bool):
    """(token, base, user_id) or None, and the pushed workouts joined to the plan sessions."""
    from sqlalchemy import select
    from backend.db.database import AsyncSessionLocal
    from backend.db.models import CorosPlanPush
    from backend.engine import plan_store
    async with AsyncSessionLocal() as db:
        cred = None
        if need_token:
            from backend.sync.coros_client import _get_token_and_base
            cred = await _get_token_and_base(db, athlete_id, auto_relogin=False)   # never logs in
        rows = (await db.execute(select(CorosPlanPush).where(CorosPlanPush.athlete_id == athlete_id,
                                                             CorosPlanPush.status == "pushed"))).scalars().all()
        try:
            sessions = {s["uid"]: s for s in await plan_store.load(db, athlete_id)}
        except Exception:                        # noqa: BLE001 — the plan is optional here
            sessions = {}
    pushed = []
    for r in rows:
        s = sessions.get(r.session_key) or {}
        pushed.append({"session_key": r.session_key, "day": r.day, "program_id": r.program_id,
                       "id_in_plan": r.id_in_plan, "kind": s.get("kind"), "minutes": s.get("minutes"),
                       "tss": s.get("tss")})
    return cred, pushed


def _fit_index() -> dict[str, Path]:
    from backend.sync import storage
    from backend.sync.coros_client import LEGACY_COROS_FITS_ROOT
    out: dict[str, Path] = {}
    for root in (storage.source_dir("coros"), LEGACY_COROS_FITS_ROOT):
        if not Path(root).is_dir():
            continue
        for p in Path(root).rglob("*.fit*"):
            lab = label_of_file(p.name)
            if lab and lab not in out:
                out[lab] = p
    return out


def fit_messages(path: Path) -> list[tuple[str, list[tuple[str, Any, bool]]]]:
    import gzip
    import fitdecode
    msgs = []
    try:
        src = gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")
        with src, fitdecode.FitReader(src, error_handling=fitdecode.ErrorHandling.IGNORE) as fr:
            for fm in fr:
                if not isinstance(fm, fitdecode.FitDataMessage):
                    continue
                if fm.name == "record":
                    msgs.append(("record", []))
                    continue
                fields = []
                for f in fm.fields:
                    dev = type(getattr(f, "field_def", None)).__name__ == "DevFieldDefinition"
                    fields.append((f.name, f.value, dev))
                msgs.append((fm.name, fields))
    except Exception:                            # noqa: BLE001 — a broken file is skipped
        return msgs
    return msgs


def app_rows(ds) -> dict[str, dict]:
    """labelId -> the app's numbers for every COROS workout in the Dataset."""
    from backend.engine import hr_profile as HP
    from backend.engine.wko5expr.dataset import day_to_date
    acc = HP.account() or {}
    c_lthr = as_number(acc.get("lthr"))
    ratios = HP.ratios("lthr", acc)
    out = {}
    for w in ds.workouts:
        lab = label_of_file(w.entry.file)
        if not lab:
            continue
        m = w.metrics
        app_lthr = as_number(ds.sport_setting("thr", w))
        z_lthr = c_lthr or app_lthr
        row = {"date": day_to_date(w.day).isoformat(), "in_app": 1, "sport": w.sport, "sport_type": w.sport_type,
               "duration_s": m.get("duration"), "moving_s": m.get("movingduration"),
               "distance_km": m.get("distance"), "climbing_m": m.get("climbing"),
               "tss": _round(m.get("tss"), 2), "tss_source": m.get("tss_source"), "if": _round(m.get("if"), 4),
               "hrtss": _round(m.get("hrtss"), 2), "hrif": _round(m.get("hrif"), 4), "np": _round(m.get("np"), 1),
               "app_lthr": app_lthr, "coros_lthr": c_lthr, "zone_lthr": z_lthr}
        try:
            hr = ds.channel(w.idx, "heartrate")
            dts = ds.channel(w.idx, "deltatime")
        except Exception:                        # noqa: BLE001
            hr = dts = None
        if hr is not None and dts is not None:
            edges = zone_edges(z_lthr, ratios) if z_lthr else [10 ** 6] * 5
            zs, cov, avg = zone_seconds(list(hr), list(dts), edges)
            row["hr_coverage"] = round(cov, 3)
            row["avg_hr"] = _round(avg, 1)
            if z_lthr:
                row.update({f"z{i + 1}_s": round(s, 1) for i, s in enumerate(zs)})
        out[lab] = row
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def default_out() -> Path:
    from backend import tenancy
    return tenancy.home_root() / "research" / "coros-tl" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def dry_run(a, since: dt.date, until: dt.date, plan_from: dt.date, plan_to: dt.date, out: Path) -> None:
    print("dry run: nothing is called, nothing is read from the DB, nothing is written\n")
    if a.local_only:
        print("COROS: no calls (--local-only)")
    else:
        chunks = math.ceil(((plan_to - plan_from).days + 1) / SCHEDULE_CHUNK_DAYS)
        print("COROS calls (with the token the app stored; never a login):")
        print(f"  GET  /activity/query            pages of {LIST_PAGE}, {since} .. {until}")
        print(f"  GET  /activity/detail/query     x up to {a.detail} (most recent activities)"
              + ("; POST fallback" if a.allow_post_reads else ""))
        print(f"  GET  /training/schedule/query   x {chunks} ({plan_from} .. {plan_to})")
        print(f"  GET  /training/program/detail   x up to {a.max_programs} (workouts the app pushed)")
        if a.allow_post_reads:
            print("  POST /training/program/query    x 1 (workout library, read query)")
    print("\nlocal reads: app DB (stored COROS session, coros_plan_push, plan sessions), the COROS Dataset,"
          " the COROS FITs of the listed activities")
    try:
        n = len(_fit_index())
        print(f"  local COROS FITs found: {n}")
    except Exception as e:                       # noqa: BLE001
        print(f"  local FIT folder not readable: {type(e).__name__}")
    print(f"\nwould write: {out / 'activities.csv'}, {out / 'planned.csv'}, {out / 'summary.json'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", help="output folder (default: ~/.wko5coach/research/coros-tl/<timestamp>/)")
    ap.add_argument("--days", type=int, default=730, help="activity list window, days back (default 730)")
    ap.add_argument("--detail", type=int, default=30, help="activity details to read (default 30 most recent)")
    ap.add_argument("--plan-back", type=int, default=56, help="calendar window: days back (default 56)")
    ap.add_argument("--plan-ahead", type=int, default=56, help="calendar window: days ahead (default 56)")
    ap.add_argument("--max-programs", type=int, default=40)
    ap.add_argument("--allow-post-reads", action="store_true",
                    help="also POST /activity/detail/query and /training/program/query (read-only queries)")
    ap.add_argument("--local-only", action="store_true", help="no COROS calls: FITs + app TSS only")
    ap.add_argument("--no-fit", action="store_true", help="skip the FIT scan")
    ap.add_argument("--athlete", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    today = dt.date.today()
    since, until = today - dt.timedelta(days=a.days), today
    plan_from, plan_to = today - dt.timedelta(days=a.plan_back), today + dt.timedelta(days=a.plan_ahead)
    out = Path(a.out).expanduser() if a.out else default_out()
    if a.dry_run:
        dry_run(a, since, until, plan_from, plan_to, out)
        return 0

    t0 = time.monotonic()
    try:
        cred, pushed = asyncio.run(_session_and_pushes(a.athlete, need_token=not a.local_only))
    except ValueError as e:
        msg = str(e).split(":")[0]
        print(f"no usable COROS session ({msg}). Open the app, log in to COROS (or run a sync), "
              "then run this again — or use --local-only.")
        return 2
    remote = {"activities": {}, "planned": [], "inventory": {}, "notes": []}
    calls: list = []
    if not a.local_only:
        api = ReadOnlyCoros(*cred, allow_post=a.allow_post_reads)
        cred = None
        try:
            remote = asyncio.run(probe_remote(api, since, until, a.detail, plan_from, plan_to, pushed,
                                              a.max_programs))
        except TokenRejected as e:
            print(f"COROS refused the stored token ({e}). Log in again in the app, then rerun. Nothing was written.")
            return 2
        calls = api.calls
        print(f"COROS: {len(calls)} read calls, {len(remote['activities'])} activities listed")

    print("building the app's COROS dataset (TSS per activity) ...", flush=True)
    from backend.api.wko5views import _dataset
    ds = _dataset(source="coros")
    mine = app_rows(ds)
    labels = list(remote["activities"]) if remote["activities"] else list(mine)
    fits = {} if a.no_fit else _fit_index()
    rows: list[dict] = []
    msg_counts: dict[str, int] = {}
    for lab in labels:
        r = {"in_app": 0}
        r.update(remote["activities"].get(lab, {}))
        app = mine.get(lab)
        if app:
            date_remote = r.get("date")
            r.update(app)
            if date_remote:
                r["date"] = date_remote
        p = fits.get(lab)
        if p is not None:
            cands, counts = fit_candidates(fit_messages(p))
            r.update(cands)
            for k in counts:
                msg_counts[k] = msg_counts.get(k, 0) + 1
        rows.append({k: v for k, v in r.items() if not k.startswith("_")})
    rows.sort(key=lambda r: r.get("date") or "")
    for i, r in enumerate(rows, start=1):
        r["n"] = i
    planned = [{k: v for k, v in r.items() if not k.startswith("_")} for r in remote["planned"]]
    planned.sort(key=lambda r: (r.get("date") or "9999", r.get("origin") or ""))
    for i, r in enumerate(planned, start=1):
        r["n"] = i

    write_csv(out / "activities.csv", rows, APP_COLS)
    write_csv(out / "planned.csv", planned, PLANNED_COLS)
    summ = summarize(rows)
    summary = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "window": {"activities": [since.isoformat(), until.isoformat()],
                   "calendar": [plan_from.isoformat(), plan_to.isoformat()]},
        "counts": {"activities": len(rows), "in_app": sum(1 for r in rows if r.get("in_app")),
                   "with_fit_scan": sum(1 for lab in labels if lab in fits), "planned": len(planned),
                   "planned_with_tl": sum(1 for r in planned if (r.get("tl_schedule") or r.get("tl_detail")))},
        "calls": _call_counts(calls), "notes": remote["notes"],
        "field_inventory": remote["inventory"],
        "fit_message_files": dict(sorted(msg_counts.items(), key=lambda kv: -kv[1])),
        **summ,
        "seconds": round(time.monotonic() - t0, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print_summary(summary, planned, out)
    return 0


def _call_counts(calls: list) -> dict:
    out: dict[str, dict] = {}
    for m, p, why in calls:
        d = out.setdefault(f"{m} {p}", {})
        d[why] = d.get(why, 0) + 1
    return out


def print_summary(s: dict, planned: list[dict], out: Path) -> None:
    c = s["counts"]
    print(f"\n{c['activities']} activities ({c['in_app']} in the app's dataset, {c['with_fit_scan']} FITs scanned); "
          f"{c['planned']} planned workouts ({c['planned_with_tl']} with a COROS TL)")
    for k, v in s["calls"].items():
        print(f"  {k}: {v}")
    for n in s["notes"]:
        print(f"  note: {n}")
    print("\nCOROS load-like fields (rows with a value):")
    for col in s["tl_columns"][:15]:
        print(f"  {col:45s} {s['columns'][col]}")
    if not s["tl_columns"]:
        print("  none found in the list / detail responses")
    fit_cols = [k for k in s["columns"] if k.startswith("fit.")]
    print(f"\nFIT candidate fields: {len(fit_cols)} ({', '.join(fit_cols[:8])}{' ...' if len(fit_cols) > 8 else ''})")
    for m in s["fit_fields_like_tl"][:5]:
        print(f"  {m['field']} tracks {m['vs']}: r={m['r']} ratio {m['median_ratio']} (n={m['n']})")
    print("\nfirst look, TSS vs TL (Pearson r, median TL/TSS):")
    for col, res in list(s["first_look"].items())[:3]:
        print(f"  {col}")
        for g, d in res.items():
            print(f"    {g:12s} n={d['n']:4d} r={d['r']} TL/TSS={d['median_tl_per_tss']}")
    tl = [r for r in planned if r.get("tl_schedule") or r.get("tl_detail")]
    if planned:
        print(f"\nplanned: {len(tl)}/{len(planned)} with a non-zero COROS trainingLoad"
              f" ({sum(1 for r in planned if r.get('origin', '').startswith('app_push'))} pushed by the app)")
    print(f"\nwrote {out}/activities.csv, planned.csv, summary.json")
    print(f"next: python -m backend.scripts.fit_tss_tl \"{out / 'activities.csv'}\" --planned \"{out / 'planned.csv'}\"")


if __name__ == "__main__":
    sys.exit(main())
