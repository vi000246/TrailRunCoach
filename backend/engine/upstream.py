"""
A local copy pulls the athlete's settings and 課表 from the production server (SP-373): one way
only — production is the truth, this copy never sends anything back (GET only, through the
production server's debug API, docs/debug-api.md §「本機從正式站同步」).

  * configured from this machine's environment only, never the DB or the repo:
      TRC_UPSTREAM_URL     the production address (https://…; http only for localhost)
      TRC_UPSTREAM_TOKEN   a debug API token made on production with export:config and export:plan
                           (or TRC_UPSTREAM_TOKEN_FILE, SP-387)
      TRC_PROXY_USER / TRC_PROXY_PASSWORD   when production sits behind the password proxy
    A redirect is never followed (it could carry the token elsewhere).
  * PARTS: "config" = GET /api/v1/debug/export/config, "plan" = GET /api/v1/debug/export/plan.
  * `pull(db, what, apply=False)` is a preview: what would change, nothing written. apply=True writes
      config  every exported settings key this version knows (not LOCAL_PREFIXES / LOCAL_KEYS: this
              copy's own debug API switch, its COROS push); plan.json (profile, weights, thresholds, events, manual phases);
              injury_events (replaced, their ids kept: activity_tags.injury_id points at them);
              activity_tags (replaced); the terrain overrides (matched by file name; a file this
              copy does not have is counted, not created)
      plan    plan_sessions replaced by production's rows (plan_store.save), deletion markers too
    then turns this copy's automatic COROS push off (plan.auto.push = False: two servers must never
    push to one COROS account) and keeps the outcome in `upstream.last_pull`.
  * not pulled: the activity files (this copy syncs its own), the event GPX files, the 範本, the race
    calculator, the COROS push records, anything export/config leaves out (passwords, tokens …).
"""
from __future__ import annotations

import datetime as dt
import logging
import urllib.error
import urllib.parse
from dataclasses import asdict
from typing import Any, Optional

from sqlalchemy import Date, DateTime, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.i18n import _

log = logging.getLogger("backend.upstream")

URL_ENV = "TRC_UPSTREAM_URL"
TOKEN_ENV = "TRC_UPSTREAM_TOKEN"
PROXY_USER_ENV = "TRC_PROXY_USER"
PROXY_PASSWORD_ENV = "TRC_PROXY_PASSWORD"
PARTS = ("config", "plan")
PATHS = {"config": "/api/v1/debug/export/config", "plan": "/api/v1/debug/export/plan"}
SCHEMA_VERSION = 1
LOCAL_PREFIXES = ("debug.", "upstream.")        # this copy's own settings: never taken from production
LOCAL_KEYS = ("plan.auto.push",)                # off on a copy that pulls (production does the pushing)
LAST_KEY = "upstream.last_pull"
TIMEOUT_S = 120.0


class UpstreamError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


# ---------------------------------------------------------------------------- the environment

def env() -> Optional[dict]:
    """{url, token, proxy_user, proxy_password} when TRC_UPSTREAM_URL and the token are both set."""
    import os
    from backend.settings.env_secrets import secret_env
    url = (os.environ.get(URL_ENV) or "").strip()
    token = secret_env(TOKEN_ENV)
    if not url or not token:
        return None
    return {"url": url, "token": token, "proxy_user": (os.environ.get(PROXY_USER_ENV) or "").strip() or None,
            "proxy_password": secret_env(PROXY_PASSWORD_ENV)}


def fetch_json(path: str) -> dict:
    """GET `path` on production (blocking; tests replace this). UpstreamError on any failure."""
    from backend.scripts import debug_fetch as F
    cfg = env()
    if cfg is None:
        raise UpstreamError(400, "NOT_CONFIGURED", _("這台沒有設定正式站（環境變數 {url}、{token}）",
                                                      url=URL_ENV, token=TOKEN_ENV))
    try:
        base = F.check_base(cfg["url"])
    except ValueError:
        raise UpstreamError(400, "BAD_URL", _("{url} 要是 https://（只有 localhost 可以用 http）", url=URL_ENV))
    try:
        return F.fetch(base + path, F.headers(cfg["token"], cfg["proxy_user"], cfg["proxy_password"]), TIMEOUT_S)
    except urllib.error.HTTPError as e:
        code = int(e.code or 0)
        if code in (401, 403):
            msg = _("正式站拒絕了這個 token（HTTP {code}）：確認它還有效、有 export:config 和 export:plan", code=code)
        elif code == 404:
            msg = _("正式站回 404：Debug API 沒打開，或正式站還沒更新到這一版")
        else:
            msg = _("正式站回應 HTTP {code}", code=code)
        raise UpstreamError(502, f"UPSTREAM_{code}", msg)
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("upstream pull: %s unreachable (%s)", urllib.parse.urlparse(base).hostname, type(e).__name__)
        # SP-373 Q2 ③: the reason in plain words, never the exception's class name (that stays in the log)
        raise UpstreamError(502, "UNREACHABLE", _("連不到正式站：{why}。確認網址、網路或 VPN 連得上",
                                                  why=unreachable_why(e)))


def unreachable_why(e: BaseException) -> str:
    """Why production could not be reached, in the user's words (the cause of a URLError when it has one)."""
    import socket
    import ssl
    r = getattr(e, "reason", None)
    cause = r if isinstance(r, BaseException) else e
    if isinstance(cause, (TimeoutError, socket.timeout)) or "timed out" in str(r or ""):
        return _("等太久沒有回應")
    if isinstance(cause, ssl.SSLError):
        return _("HTTPS 憑證有問題")
    if isinstance(cause, socket.gaierror):
        return _("找不到這個主機名稱")
    if isinstance(cause, ConnectionRefusedError):
        return _("對方拒絕連線")
    return _("網路連不上")


async def state(db: AsyncSession) -> dict:
    """What the settings page shows: configured?, the production host (never the token), the last pull."""
    from backend.settings.repository import SettingsRepository
    cfg = env()
    host = urllib.parse.urlparse(cfg["url"]).hostname if cfg else None
    return {"configured": cfg is not None, "host": host, "proxy": bool(cfg and cfg["proxy_user"]),
            "parts": list(PARTS), "env": {"url": URL_ENV, "token": TOKEN_ENV},
            "last_pull": await SettingsRepository(db).get(LAST_KEY)}


# ---------------------------------------------------------------------------- reading the exports

def _check(doc: Any, part: str) -> dict:
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise UpstreamError(502, "SCHEMA", _("正式站的匯出格式跟這一版不同：兩邊更新到同一版再試"))
    if part == "config" and not isinstance(doc.get("blocks"), dict):
        raise UpstreamError(502, "SCHEMA", _("正式站的設定匯出缺了內容"))
    if part == "plan" and not isinstance(doc.get("sessions"), list):
        raise UpstreamError(502, "SCHEMA", _("正式站的課表匯出缺了內容"))
    return doc


def _local_key(k: str) -> bool:
    return k in LOCAL_KEYS or any(k.startswith(p) for p in LOCAL_PREFIXES)


def settings_of(doc: dict) -> dict:
    """Every exported settings key (all blocks), minus this copy's own."""
    out: dict = {}
    for block in doc["blocks"].values():
        if isinstance(block, dict) and isinstance(block.get("keys"), dict):
            out.update({k: v for k, v in block["keys"].items() if not _local_key(k)})
    return out


def plan_of(doc: dict):
    """The production plan.json (planning.Plan) rebuilt from export/config's blocks."""
    from backend.engine import planning as P
    b = doc["blocks"]

    def only(cls, d: dict, drop=()) -> dict:
        return {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k not in drop}
    prof = (b.get("profile") or {})
    return P.Plan(
        events=[P.Event(**only(P.Event, e)) for e in (b.get("events") or {}).get("rows") or []],
        phases=[P.Phase(**{**only(P.Phase, p), "auto": False}) for p in (b.get("events") or {}).get("phases") or []],
        thresholds=[P.Threshold(**only(P.Threshold, t)) for t in (b.get("thresholds") or {}).get("rows") or []],
        weights=[P.Weight(**only(P.Weight, w)) for w in prof.get("weights") or []],
        profile=dict(prof.get("plan_profile") or {}))


def _plan_parts(plan) -> dict:
    return {"events": sorted((asdict(e) for e in plan.events), key=lambda e: (e["date"], e["id"])),
            "phases": [{k: v for k, v in asdict(p).items() if k != "auto"} for p in plan.phases],
            "thresholds": sorted((asdict(t) for t in plan.thresholds), key=lambda t: t["date"]),
            "weights": sorted(({"date": w.date, "kg": w.kg} for w in plan.weights), key=lambda w: w["date"]),
            "profile": [plan.profile or {}]}


def _coerce(cls, d: dict, skip=()) -> dict:
    """A row of `cls` from exported JSON: known columns only, ISO text back to dates / datetimes."""
    out = {}
    for col in cls.__table__.columns:
        if col.name in skip or col.name not in d:
            continue
        v = d[col.name]
        if isinstance(v, str) and isinstance(col.type, DateTime):
            v = dt.datetime.fromisoformat(v.rstrip("Z"))
        elif isinstance(v, str) and isinstance(col.type, Date):
            v = dt.date.fromisoformat(v[:10])
        out[col.name] = v
    return out


def _same_rows(a: list, b: list, key: str, fields: tuple) -> int:
    """How many rows differ between `a` and `b` (matched on `key`): added + removed + changed."""
    A = {r.get(key): r for r in a}
    B = {r.get(key): r for r in b}
    changed = sum(1 for k in A.keys() & B.keys() if any(A[k].get(f) != B[k].get(f) for f in fields))
    return len(A.keys() ^ B.keys()) + changed


PLAN_FIELDS = ("week_start", "gen_key", "day", "kind", "title", "minutes", "target", "detail", "tss", "origin",
               "edited", "state", "note", "terrain", "distance_km", "climb_m", "protocol", "variant_key",
               "rung_key", "steps", "family")


def _session_ok(s: Any) -> bool:
    return (isinstance(s, dict) and isinstance(s.get("uid"), str) and 0 < len(s["uid"]) <= 40
            and isinstance(s.get("kind"), str) and isinstance(s.get("title"), str)
            and isinstance(s.get("week_start"), str))


# ---------------------------------------------------------------------------- preview + apply

async def _config(db: AsyncSession, doc: dict, apply: bool) -> dict:
    from backend.db.models import ActivityTag, InjuryEvent, WorkoutFile
    from backend.engine import planning as P
    from backend.settings.repository import SettingsRepository, UnknownSetting, known
    repo = SettingsRepository(db)
    b = doc["blocks"]
    # settings keys
    changed, skipped = [], []
    for k, v in sorted(settings_of(doc).items()):
        if not known(k):
            skipped.append(k)                  # production is newer than this copy
            continue
        if await repo.get(k) != v:
            changed.append(k)
            if apply:
                try:
                    await repo.set(k, v)
                except (ValueError, UnknownSetting):
                    changed.pop()
                    skipped.append(k)
    # plan.json
    new_plan = plan_of(doc)
    old_plan = await run_in_threadpool(P.Plan.load)
    new_p, old_p = _plan_parts(new_plan), _plan_parts(old_plan)
    plan_diff = {k: {"here": len(old_p[k]), "production": len(new_p[k]), "same": old_p[k] == new_p[k]}
                 for k in new_p if k != "profile"}
    plan_diff["profile"] = {"same": old_p["profile"] == new_p["profile"]}
    # injuries, tags, terrain
    inj = [r for r in (b.get("injuries") or {}).get("rows") or [] if isinstance(r, dict) and r.get("onset_date")]
    tags = [r for r in (b.get("activity_overrides") or {}).get("tags") or []
            if isinstance(r, dict) and r.get("start_local")]
    terrain = [r for r in (b.get("activity_overrides") or {}).get("terrain") or []
               if isinstance(r, dict) and r.get("file") and r.get("terrain")]
    cols_inj = tuple(c.name for c in InjuryEvent.__table__.columns if c.name not in ("created_at", "updated_at"))
    cols_tag = tuple(c.name for c in ActivityTag.__table__.columns
                     if c.name not in ("id", "file", "workout_id", "updated_at"))
    here_inj = [{c: getattr(r, c) for c in cols_inj} for r in (await db.execute(select(InjuryEvent))).scalars()]
    here_tags = [{c: getattr(r, c) for c in cols_tag} for r in (await db.execute(select(ActivityTag))).scalars()]
    files = {}
    for wid, fp, tc, ov in (await db.execute(select(WorkoutFile.id, WorkoutFile.file_path,
                                                    WorkoutFile.trail_classification,
                                                    WorkoutFile.classification_overridden))).all():
        files[str(fp).replace("\\", "/").rsplit("/", 1)[-1]] = (wid, tc, bool(ov))
    t_match = [r for r in terrain if r["file"] in files]
    t_change = [r for r in t_match if files[r["file"]][1:] != (r["terrain"], True)]
    out = {"settings": {"changed": changed, "skipped": skipped},
           "plan_json": plan_diff,
           "injuries": {"here": len(here_inj), "production": len(inj),
                        "differ": _same_rows(here_inj, inj, "id", cols_inj)},
           "activity_tags": {"here": len(here_tags), "production": len(tags),
                             "differ": _same_rows(here_tags, tags, "start_local", cols_tag)},
           "terrain": {"production": len(terrain), "matched": len(t_match), "changed": len(t_change),
                       "missing_here": len(terrain) - len(t_match)}}
    if not apply:
        return out
    await run_in_threadpool(new_plan.save)
    await db.execute(delete(InjuryEvent))
    for r in inj:
        db.add(InjuryEvent(**_coerce(InjuryEvent, r)))
    await db.execute(delete(ActivityTag))
    for r in tags:
        db.add(ActivityTag(**_coerce(ActivityTag, r, skip=("id", "file", "workout_id"))))
    for r in t_change:
        w = await db.get(WorkoutFile, files[r["file"]][0])
        w.trail_classification, w.classification_overridden = r["terrain"], True
    await db.commit()
    try:                                        # thresholds / weights changed: rebuild the cached datasets
        from backend.api.wko5views import plan_changed
        plan_changed(True)
    except Exception:                           # noqa: BLE001 — no dataset built yet: nothing to drop
        pass
    return out


async def _plan(db: AsyncSession, doc: dict, apply: bool) -> dict:
    from backend.engine import plan_store as PS
    rows = [s for s in doc["sessions"] if _session_ok(s)]
    here = [PS.to_dict(r) | {"title": r.title} for r in (await PS._rows(db, 1)).values()]
    H = {s["uid"]: s for s in here}
    N = {s["uid"]: s for s in rows}
    out = {"here": len(here), "production": len(rows), "bad_rows": len(doc["sessions"]) - len(rows),
           "added": len(N.keys() - H.keys()), "removed": len(H.keys() - N.keys()),
           "changed": sum(1 for u in N.keys() & H.keys() if any(N[u].get(f) != H[u].get(f) for f in PLAN_FIELDS))}
    if apply:
        await PS.save(db, rows)
    return out


async def pull(db: AsyncSession, what: list, apply: bool = False) -> dict:
    """Preview (apply False) or apply the pull of `what` (PARTS). Every export is fetched and
    checked before anything is written, so a failed fetch writes nothing."""
    from backend.settings.repository import SettingsRepository
    docs = {}
    for part in PARTS:
        if part in what:
            docs[part] = _check(await run_in_threadpool(fetch_json, PATHS[part]), part)
    result: dict = {"apply": apply, "what": [p for p in PARTS if p in what]}
    if "config" in docs:
        result["config"] = await _config(db, docs["config"], apply)
    if "plan" in docs:
        result["plan"] = await _plan(db, docs["plan"], apply)
    if apply:
        repo = SettingsRepository(db)
        result["push_off"] = await repo.get("plan.auto.push") is not False
        await repo.set("plan.auto.push", False)
        at = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        await repo.set(LAST_KEY, {"at": at, "what": result["what"],
                                  "exported_at": {p: d.get("exported_at") for p, d in docs.items()}})
        await db.commit()
        result["at"] = at
        log.info("upstream pull applied: %s", ", ".join(result["what"]))
    return result

