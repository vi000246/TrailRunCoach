"""
Plans, roles and scopes (SP-330; the tiers and prices are SP-321's).

One table answers "may this person do X now": the API (require_scope / use_quota dependencies),
the scheduler (data-source scopes), and the pages (GET /api/v1/session returns the scopes; the
shell locks `[data-scope]` controls and shows the upgrade hint on a 403). Nothing checks a plan
name anywhere else.

    membership.has("calc:backtest")          # the current tenant
    Depends(require_scope("charts:365d"))    # 403 {code: SCOPE_REQUIRED, scope, upgrade}
    Depends(use_quota("calc"))               # 429 {code: QUOTA_EXCEEDED, …} past the plan's limit

Roles (first wave: user, subscriber, owner; coach and group_leader are defined, not granted to
anyone yet — SP-56 / SP-329 are the second wave):

  owner        user 1 = the self-hosted app's owner; every scope, no quota (also the admin)
  subscriber   a user whose plan is "subscriber" and not expired (plan_expires_at)
  user         everyone else signed in (the free tier)
  demo         the demo instance (its own caps in tenancy.DEMO_CAPS still apply)

An admin (SP-351 R2): the owner, and anyone the admin page made one (accounts.db users.admin).
Being an admin adds ADMIN_SCOPES (the admin page) to their role's scopes and nothing else — not
the owner's own scopes (計算模式 `admin`, the WKO5 client, TrainingPeaks' website login), not a
plan: their plan and quotas stay their own.

Expiry / cancellation = downgrade: the role falls back to `user`, the scopes with it; no data is
deleted (full-resolution data past the free tier's retention follows SP-317's monthly archive,
with a reminder before the first one).

Data sources are scopes too, and each can be switched off for the whole server
(site_settings `sources.off`, list of scopes; COROS also by its own switch, SP-385): the
scheduler then skips it and the settings page says 暫停中. TrainingPeaks' website login and the
WKO5 client are the owner's only (owner 2026-10-12: TP's terms §11(e), §23).

A test account (SP-611, accounts/testaccounts.py) has its role's scopes but no data source and no
automatic sync (TEST_DENIED): it never connects a real platform. Its plan can still be set on the
admin page, to try either tier.

Cross-project (疊石 Cairn / inReachLiveTrack, SP-329, accounts/links.py): it calls /api/v1/link/*
with a service token (accounts.db api_tokens) that carries `link:*` scopes, issued only to a user
who linked the two accounts in 設定; `check_service_token` refuses one without the scope asked
for. No role grants a link scope: only a token does.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import HTTPException

OWNER, SUBSCRIBER, USER, COACH, GROUP_LEADER, DEMO = "owner", "subscriber", "user", "coach", "group_leader", "demo"
FREE, PAID = "free", "subscriber"

# ---------------------------------------------------------------------------- the scopes

SCOPES: dict[str, str] = {
    # always free (SP-321: safety is never paywalled)
    "plan:view": "看課表、改課表、手動記錄、活動列表",
    "plan:edit": "改課表、手動記錄",
    "safety": "安全提示、關門判斷、傷病紅燈、熱與寒冷提醒",
    "import:fit": "手動匯入 FIT（SP-323）",
    "export:data": "下載我的資料（SP-386）",
    "charts:90d": "近 90 天圖表",
    "calc:use": "賽事計算機",
    "plan:auto": "自動調課表（夜間同步後）",
    "sync:auto_nightly": "每天夜間自動同步一次",
    # subscription
    "charts:365d": "365 天圖表",
    "charts:full": "全歷史圖表",
    "history:full": "全解析度保留 2／3／5／10 年（自選）",
    "calc:unlimited": "計算機不限次數",
    "calc:backtest": "回測",
    "plan:auto_realtime": "有新活動就即時調課表",
    "sync:auto_realtime": "有新活動就處理",
    "offline:full": "完整離線快取（SP-314）",
    # data sources (each can be switched off for the server)
    "sync:coros_credential": "COROS 帳密同步（非官方，SP-385）",
    "sync:garmin_trial": "Garmin 試運行（SP-91）",
    "sync:intervals": "intervals.icu（SP-346）",
    "sync:coros_mcp": "COROS 官方 MCP（SP-348）",
    "sync:tp_web": "TrainingPeaks 網站登入（只限站主）",
    # the owner's own
    "wko5": "對照資料夾",
    "tp:wko5_client": "TrainingPeaks 的桌面軟體 client",
    "admin": "全站設定與使用者管理（SP-351）",
    "admin:config": "管理頁：全站設定、資料來源開關",
    "admin:users": "管理頁：使用者、候補名單",
    # second wave: defined, granted to nobody yet
    "coach:read_students": "教練看學員資料（SP-56）",
    "group:calc_participants": "開團者幫報名者算能力值（SP-329）",
    # cross-project (疊石 Cairn / inReachLiveTrack → TrailRunCoach, SP-329; accounts/links.py)
    "link:account": "疊石 Cairn 讀帳號與方案狀態",
    "link:gpx_sync": "疊石 Cairn 同步 GPX",
    "link:calc": "疊石 Cairn 讀存過的計算與配速曲線",
    "link:weather": "疊石 Cairn 讀路線天氣（SP-556）",
    "link:participant_ability": "疊石 Cairn 讀報名者能力值（要報名者本人另外同意）",
}
SOURCE_SCOPES = ("sync:coros_credential", "sync:garmin_trial", "sync:intervals", "sync:coros_mcp", "sync:tp_web")
SYNC_SOURCE_SCOPE = {"coros": "sync:coros_credential", "garmin": "sync:garmin_trial", "intervals": "sync:intervals",
                     "tp": "sync:tp_web"}      # runner's sources
# never a test account's (SP-611): no real platform, nothing synced
TEST_DENIED = frozenset(SOURCE_SCOPES) | {"sync:auto_nightly", "sync:auto_realtime"}
LINK_SCOPES = ("link:account", "link:gpx_sync", "link:calc", "link:weather", "link:participant_ability")

_FREE = frozenset({"plan:view", "plan:edit", "safety", "import:fit", "export:data", "charts:90d",
                   "calc:use", "plan:auto", "sync:auto_nightly",
                   "sync:coros_credential", "sync:garmin_trial", "sync:intervals", "sync:coros_mcp"})
_PAID = _FREE | {"charts:365d", "charts:full", "history:full", "calc:unlimited", "calc:backtest",
                 "plan:auto_realtime", "sync:auto_realtime", "offline:full"}
_OWNER_ONLY = frozenset({"wko5", "tp:wko5_client", "sync:tp_web", "admin", "admin:config", "admin:users"})
ADMIN_SCOPES = ("admin:config", "admin:users")

ROLE_SCOPES: dict[str, frozenset] = {
    USER: _FREE,
    SUBSCRIBER: _PAID,
    OWNER: frozenset(SCOPES) - set(LINK_SCOPES) - {"coach:read_students", "group:calc_participants"},
    COACH: _PAID | {"coach:read_students"},                 # second wave: not granted yet
    GROUP_LEADER: _PAID | {"group:calc_participants"},      # second wave: not granted yet
    DEMO: frozenset({"plan:view", "plan:edit", "safety", "charts:90d", "charts:365d", "charts:full",
                     "calc:use", "calc:unlimited"}),
}
# first wave: the roles a user row can have
ACTIVE_ROLES = (USER, SUBSCRIBER, OWNER)

# ---------------------------------------------------------------------------- the plans (SP-321)

@dataclass(frozen=True)
class Quota:
    key: str
    limit: int
    period: str          # "day" | "month"
    label: str


QUOTAS: dict[str, dict[str, Quota]] = {
    FREE: {
        "calc": Quota("calc", 10, "month", "計算機每月 10 次"),
        "charts": Quota("charts", 5, "day", "圖表每天 5 個"),
    },
    PAID: {
        "backtest": Quota("backtest", 2, "month", "回測每月 2 次"),
    },
}
CHART_DAYS = {"charts:90d": 90, "charts:365d": 365}       # beyond 365: charts:full

# the plan × feature table, for the pages and the docs (docs/plans/membership.md mirrors it)
PLAN_TABLE = [
    ("看課表、改課表、手動記錄、活動列表、近 90 天圖表", "✅（圖表每天 ≤ 5 個）", "✅"),
    ("安全提示、關門判斷、傷病紅燈、熱與寒冷提醒", "✅ 永遠免費", "✅"),
    ("手動匯入 FIT、下載我的資料", "✅", "✅"),
    ("自動同步", "每天夜間一次", "有新活動就處理"),
    ("自動調課表", "夜間同步後", "即時"),
    ("計算機", "每月 10 次（同一天同一條路線只算 1 次）", "無限"),
    ("365 天與全歷史圖表", "—", "✅"),
    ("回測", "—", "每月 2 次、全站排隊"),
    ("全解析度保留", "1 年（之後只留摘要）", "自選 2／3／5／10 年"),
    ("離線快取", "省空間", "完整"),
    ("教練、開團", "—", "第二波另議"),
]

# ---------------------------------------------------------------------------- who is asking

@dataclass(frozen=True)
class Member:
    role: str
    plan: str
    user_id: Optional[int]
    expires_at: Optional[str] = None
    admin: bool = False                # SP-351 R2: the admin page's scopes on top of the role's
    test: bool = False                 # a test account (SP-611)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def role_of_user(user, now: Optional[dt.datetime] = None) -> str:
    """owner | subscriber (plan subscriber, not expired) | user. Expired = downgraded."""
    if user.is_owner:
        return OWNER
    if user.plan == PAID:
        exp = user.plan_expires_at
        if exp is None:
            return SUBSCRIBER
        try:
            t = dt.datetime.fromisoformat(exp)
        except ValueError:
            return USER
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        if t > (now or _now()):
            return SUBSCRIBER
    return USER


def member(tenant=None, now: Optional[dt.datetime] = None) -> Member:
    from backend import tenancy
    t = tenant or tenancy.current()
    if t.kind == tenancy.OWNER:
        return Member(OWNER, PAID, 1, admin=True)
    if t.is_demo:
        return Member(DEMO, FREE, None)
    uid = user_id_of(t)
    u = None
    if uid is not None:
        try:
            from backend.accounts import store
            u = store.get_user(uid)
        except Exception:                        # noqa: BLE001 — unreadable accounts.db: the free tier
            u = None
    if u is None:
        return Member(USER, FREE, uid)
    role = role_of_user(u, now)
    return Member(role, PAID if role in (SUBSCRIBER, OWNER) else FREE, uid, u.plan_expires_at,
                  admin=bool(getattr(u, "is_admin", False)), test=bool(getattr(u, "is_test", False)))


def user_id_of(t) -> Optional[int]:
    if t.id == "owner":
        return 1
    if t.id.startswith("u") and t.id[1:].isdigit():
        return int(t.id[1:])
    return None


def sources_off() -> set:
    """Data-source scopes switched off for the whole server (site_settings + the COROS switch +
    the Garmin trial's switch, SP-91)."""
    from backend import site_settings as SS
    off = {s for s in (SS.get("sources.off") or []) if s in SOURCE_SCOPES}
    from backend.sync import coros_limits as CL
    if not CL.enabled():
        off.add("sync:coros_credential")
    from backend.sync import garmin_limits as GL
    if not GL.enabled():
        off.add("sync:garmin_trial")
    return off


def scopes(tenant=None, now: Optional[dt.datetime] = None) -> frozenset:
    m = member(tenant, now)
    out = ROLE_SCOPES.get(m.role, frozenset()) - sources_off()
    if m.test:
        out = out - TEST_DENIED                  # SP-611: no data source, no automatic sync
    return out | frozenset(ADMIN_SCOPES) if m.admin else out      # SP-351 R2


def role_allows(scope: str) -> bool:
    """The current person's role holds `scope`, whether or not the server switched it off (a
    connection made while paused; SP-385) — never a test account's data source (SP-611)."""
    m = member()
    if m.test and scope in TEST_DENIED:
        return False
    return scope in ROLE_SCOPES.get(m.role, frozenset())


def has(scope: str, tenant=None) -> bool:
    return scope in scopes(tenant)


def set_source(scope: str, on: bool, by: str = "owner") -> list:
    """Switch a data source off / on for the whole server (an admin: SP-351's page, api/admin.py)."""
    from backend import site_settings as SS
    if scope not in SOURCE_SCOPES:
        raise ValueError(f"not a data source scope: {scope}")
    if scope == "sync:coros_credential":
        from backend.sync import coros_limits as CL
        CL.set_enabled(on, by=by)
    if scope == "sync:garmin_trial":
        from backend.sync import garmin_limits as GL
        GL.set_enabled(on, by=by)
    off = set(SS.get("sources.off") or [])
    (off.discard if on else off.add)(scope)
    SS.update({"sources.off": sorted(off) or None})
    return sorted(sources_off())


# ---------------------------------------------------------------------------- the API's checks

def scope_error(scope: str) -> HTTPException:
    m = member()
    paid = scope in ROLE_SCOPES[SUBSCRIBER]
    return HTTPException(403, {"code": "SCOPE_REQUIRED", "scope": scope, "label": SCOPES.get(scope, scope),
                               "role": m.role, "upgrade": paid and m.role == USER,
                               "paused": scope in sources_off()})


def require(scope: str) -> None:
    if not has(scope):
        raise scope_error(scope)


def require_scope(scope: str) -> Callable:
    """A FastAPI dependency: 403 SCOPE_REQUIRED without the scope."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope}")

    def dep() -> None:
        require(scope)
    dep.__name__ = f"require_{scope.replace(':', '_')}"
    return dep


def chart_scope(days_back: float) -> str:
    """The scope a chart range needs: ≤ 90 days, ≤ 365 days, else the full history."""
    if days_back <= CHART_DAYS["charts:90d"] + 1:
        return "charts:90d"
    if days_back <= CHART_DAYS["charts:365d"] + 1:
        return "charts:365d"
    return "charts:full"


def require_workout_age(ds, w) -> None:
    """One activity's own analysis (活動判讀, 活動比較; SP-713) reaches as far back as the charts:
    403 SCOPE_REQUIRED (with the upgrade hint for the free tier) past the plan's range. The
    overview's week / month totals are not this: the free tier sees all of them."""
    require(chart_scope(float(ds.today) - float(w.day)))


# ---------------------------------------------------------------------------- quotas

def _period_key(period: str, now: dt.datetime) -> str:
    local = now.astimezone(dt.timezone(dt.timedelta(hours=8)))      # Taiwan: the counters reset at local midnight
    return local.strftime("%Y-%m-%d") if period == "day" else local.strftime("%Y-%m")


def quota_for(key: str, m: Optional[Member] = None, now: Optional[dt.datetime] = None) -> Optional[Quota]:
    """The plan's quota, its limit as the admin set it (site_config quota.<plan>.<key>, SP-351),
    plus this user's 本月額外次數 for a monthly quota (the admin page's user row, SP-713)."""
    m = m or member()
    if m.role in (OWNER, DEMO):
        return None
    q = QUOTAS.get(m.plan, {}).get(key)
    if q is None:
        return None
    from backend import site_config as SC
    ck = f"quota.{m.plan}.{key}"
    if ck in SC.ITEMS:
        limit = SC.get(ck)
        if limit != q.limit:
            q = Quota(q.key, int(limit), q.period, q.label)
    extra = extra_this_month(m.user_id, now) if q.period == "month" else 0
    if extra:
        q = Quota(q.key, q.limit + extra, q.period, q.label)
    return q


def month_key(now: Optional[dt.datetime] = None) -> str:
    """This month in Taiwan ('YYYY-MM'): the monthly counters' and the extra uses' period."""
    return _period_key("month", now or _now())


def extra_this_month(user_id: Optional[int], now: Optional[dt.datetime] = None) -> int:
    """The extra uses an admin gave `user_id` for this month (SP-713; 0 = none). Added to every
    monthly quota of their plan (calculator for the free tier, back-test for a subscriber)."""
    if user_id is None:
        return 0
    try:
        from backend.accounts import store
        return store.quota_extra_get(int(user_id), month_key(now))
    except Exception:                        # noqa: BLE001 — unreadable accounts.db: no extra
        return 0


def usage(key: str, now: Optional[dt.datetime] = None, m: Optional[Member] = None) -> dict:
    """{used, limit, period, resets} of this tenant's quota `key` (limit None = unlimited); a
    monthly quota also says `extra` (the admin's 本月額外次數, already inside `limit`)."""
    m = m or member()
    now = now or _now()
    q = quota_for(key, m, now)
    if q is None or m.user_id is None:
        return {"used": 0, "limit": None, "period": None}
    from backend.accounts import store
    pk = _period_key(q.period, now)
    out = {"used": store.usage_get(m.user_id, key, pk), "limit": q.limit, "period": q.period, "label": q.label,
           "resets": pk}
    if q.period == "month":
        out["extra"] = extra_this_month(m.user_id, now)
    return out


def consume(key: str, item: Optional[str] = None, now: Optional[dt.datetime] = None) -> dict:
    """Count one use of quota `key` (a distinct `item` counts once per period, e.g. one chart
    dashboard a day); 429 QUOTA_EXCEEDED past the limit. Unlimited roles count nothing."""
    m = member()
    now = now or _now()
    q = quota_for(key, m, now)
    if q is None or m.user_id is None:
        return {"used": 0, "limit": None}
    from backend.accounts import store
    pk = _period_key(q.period, now)
    used = store.usage_add(m.user_id, key, pk, q.limit, item)
    if used is None:
        raise HTTPException(429, {"code": "QUOTA_EXCEEDED", "quota": key, "label": q.label, "limit": q.limit,
                                  "period": q.period, "upgrade": m.role == USER})
    return {"used": used, "limit": q.limit}


def refund(key: str, got: Optional[dict] = None, now: Optional[dt.datetime] = None) -> None:
    """Take back the use consume() just counted (`got` = its answer; nothing counted, nothing back)."""
    if got is not None and got.get("limit") is None:
        return
    m = member()
    now = now or _now()
    q = quota_for(key, m, now)
    if q is None or m.user_id is None:
        return
    from backend.accounts import store
    store.usage_refund(m.user_id, key, _period_key(q.period, now))


def use_quota(key: str) -> Callable:
    """A FastAPI dependency: counts one use, 429 QUOTA_EXCEEDED past the plan's limit."""
    def dep() -> None:
        consume(key)
    dep.__name__ = f"quota_{key}"
    return dep


# ---------------------------------------------------------------------------- the calculator's count (SP-713)
#
# One use of the calculator = one route a day: however often the page recomputes while the inputs
# change (it re-plans 250 ms after every edit), and whichever entry computes it — /plan, /share,
# /export/plan, /predict, /estimate — the same route on the same (Taiwan) day counts once. The
# route is, most stable first: the GPX's sha1 (course_id, also an event's stored GPX), the plan
# event, else the typed course (type, km, gain, days) hashed.

def calc_route(body) -> str:
    c = getattr(body, "course", None)
    if c is not None and getattr(c, "course_id", None):
        return "gpx:" + str(c.course_id)
    eid = (getattr(c, "event_id", None) if c is not None else None) or getattr(body, "event_id", None)
    if eid:
        return "event:" + str(eid)
    man = (getattr(c, "manual", None) if c is not None else None) or {}

    def num(v, nd):
        try:
            return round(float(v), nd)
        except (TypeError, ValueError):
            return None
    parts = [getattr(body, "type", None), num(man.get("km", getattr(body, "distance_km", None)), 2),
             num(man.get("gain", getattr(body, "gain_m", None)), 0), getattr(body, "days", None)]
    import hashlib
    import json
    return "typed:" + hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:16]


def use_calc(body, now: Optional[dt.datetime] = None) -> dict:
    """Count the calculator for this request's route (once a route a day); 429 past the limit."""
    now = now or _now()
    return consume("calc", item=f"{_period_key('day', now)}/{calc_route(body)}", now=now)


# ---------------------------------------------------------------------------- cross-project tokens

def check_service_token(token: Optional[str], scope: str):
    """inReachLiveTrack (SP-329) calls with `Authorization: Bearer <service token>`: the token's
    user when it carries `scope`; 401 for an unknown / expired token, 403 SCOPE_REQUIRED without
    the scope. The user's own role must still allow what the link scope leads to (checked by the
    endpoint as usual once the request runs as that user)."""
    if scope not in LINK_SCOPES:
        raise ValueError(f"not a link scope: {scope}")
    from backend.accounts import store
    got = store.service_token(token) if token else None
    if got is None:
        raise HTTPException(401, {"code": "TOKEN_INVALID"})
    user, granted = got
    if scope not in granted:
        raise HTTPException(403, {"code": "SCOPE_REQUIRED", "scope": scope, "label": SCOPES[scope]})
    return user


def session_view(tenant=None) -> dict:
    """What GET /api/v1/session tells the pages."""
    m = member(tenant)
    out = {"role": m.role, "plan": m.plan, "scopes": sorted(scopes(tenant)), "paused_sources": sorted(sources_off()),
           # SP-569 ⑤: what a subscription adds — only these may say 「訂閱後可用」 (TrainingPeaks' website
           # login, the data folder, admin: the site's own, a subscription never adds them)
           "upgrade_scopes": sorted(ROLE_SCOPES[SUBSCRIBER] - ROLE_SCOPES[USER])}
    if m.expires_at:
        out["plan_expires_at"] = m.expires_at
    if m.test:
        out["test"] = True            # SP-611: the shell says so on every page
    out["quotas"] = {k: usage(k, m=m) for k in QUOTAS.get(m.plan, {})} if m.role in (USER, SUBSCRIBER) else {}
    return out
