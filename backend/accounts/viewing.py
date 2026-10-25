"""
代看 (SP-791) and the coach's edits (SP-792): a coach looks at one student's data the way the student
sees it — the login gate (tenancy_mw) puts the student's tenant on the request while the coach stays
the signed-in user (login.current_user) — but only through these rules, default deny both ways:

  reads (GET / HEAD)   READ_OPEN only (the pages and data of 總覽, 課表, 圖表, 活動, 路線, 賽事, 賽事計算機
                       and 設定's 生理數據 tab), never READ_BLOCK. Everything else — the account, the
                       platform connections and their passwords, deleting, 下載我的資料 and every file
                       download, payment, 疊石 Cairn, the injury log, the admin page, the debug API, the
                       backups, the calendar feed's address — 403 COACH_VIEW_BLOCKED (a page: back to the
                       overview, which says why)
  writes               CALC (computations that store nothing: the race calculator, the step editor's
                       previews), STOP and signing out; SP-792's WRITE_OPEN, each under the student's
                       switch (coaching.PERMS: "plan" 課表／賽事／不排課日, "training" 門檻與訓練設定) —
                       403 COACH_PERM_OFF while that switch is off. Any other write 403 COACH_VIEW_READONLY
  every request        the link checked again (coaching: active, both accounts enabled, the coach still a
                       coach): withdrawn -> 403 COACH_VIEW_ENDED from the very next request (a page: the
                       coach's own 教練 tab, which says so)
  plan and counts      the coach's (membership.member: their role, plan and quotas — never the owner's own
                       scopes or an admin's), not the student's
  the tenant's caps    none, plus CAPS of the switches that are on (plan.write / push / upload.gpx,
                       thresholds.write): the shell locks the other controls the way it does in the demo
  the answers          marked X-TRC-Viewing: the coach's offline copy (offline_core.js) never keeps them
  the record           coach_audit (store): 代看 started / ended and every write that went through (which
                       one, and from the request and the answer a day, a name, the values set) — the
                       student reads it in 設定 › 教練; the schedule shows the latest
The owner's data (user 1) is never viewed: that tenant holds the site's own resources (SP-789's 建議另開).

What the gate does with a session that views someone: viewing.resolve() -> a View (or ENDED), then
viewing.check() per request. A route nobody listed here is closed while viewing — a new router stays
shut until someone decides it is safe (test_coach_view_sp791 walks every route of the app).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.i18n import N_

log = logging.getLogger("backend.viewing")

START = "/api/v1/coach/view"            # POST START/<student id>
STOP = "/api/v1/coach/view/stop"
LOGOUT = "/api/v1/account/logout"
SESSION = "/api/v1/session"
HOME = "/api/v1/overview/page"          # where 代看 opens, and where a closed page sends the coach
COACH_HOME = "/api/v1/app/settings"     # + "#coach": the coach's own students (回到我自己, a withdrawal)

MESSAGES = {
    "COACH_VIEW_BLOCKED": N_("代看學員時不能開這裡：帳號、平台連線與密碼、下載資料、付款、疊石 Cairn、傷病紀錄只有學員本人能用"),
    "COACH_VIEW_READONLY": N_("代看學員時只能看，不能改這裡"),
    "COACH_PERM_OFF": N_("學員關掉了「{what}」，教練現在不能改"),
    "COACH_VIEW_ENDED": N_("學員已撤回授權（或教練關係已結束），代看已經結束"),
}


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("(?:" + "|".join(patterns) + ")")


_P = "/api/v1/overview/plan"
# pages the static route renders by name (/api/v1/static/<name>.html) that 代看 may open
_PAGES = "activity|actcompare|charts|compliance|overview|plan|racepower|races|routelib|routes|schedule|settings|templates"

READ_OPEN = _rx(
    r"/", r"/overview", r"/activities", r"/achievements", r"/activities/[^/]+",
    r"/manual(?:/.*)?", r"/about", r"/legal(?:/[^/]+)?", r"/sw\.js", r"/manifest\.webmanifest",
    r"/api/v1/session", r"/api/v1/region",
    r"/api/v1/static/(?!.*\.html$).+", rf"/api/v1/static/(?:{_PAGES})\.html",
    r"/api/v1/overview/.+",
    r"/api/v1/app/(?:dataset/status|views|views/[^/]+/dashboards/[^/]+/charts/[^/]+|render/ready|workouts"
    r"|workouts/[^/]+/(?:review|activity|kind|samples|zone-sets|segments)|activities|activities/(?:page|auto|stats"
    r"|pack-defaults|hints)|race-activities|sports|athlete|plan-range|primary-sport|viewer|settings|races"
    r"|races/(?:page|detail|readiness/[^/]+)|activity-compare|activity-compare/(?:page|activities))",
    r"/api/v1/achievements(?:/page)?",
    r"/api/v1/plan(?:/page|/events/[^/]+/gpx(?:/preview)?|/thresholds|/profile|/profile/detect|/profile/hint-level"
    r"|/hr-profile|/power-zones|/pmc-start|/race-pace|/threshold-(?:estimate|history|check))?",
    r"/api/v1/racepower/(?:page|inputs|peaks|weather|grade-model|cadence-check|heat-status|altitude-acclimatisation"
    r"|hike-meta|solo-hikes|goal-basis|backtest|surface/[^/]+|saved/[^/]+)",
    r"/api/v1/routes(?:/status|/page|/uploaded/page|/[^/]+|/[^/]+/compare)?",
    r"/api/v1/intro/.+",
    r"/api/v1/wko5/.+",                 # the old prefix: a 308 to /api/v1/app/…, checked again there
)
# closed even where READ_OPEN would let it through: file downloads (下載資料), the injury log and a run's
# pain (health records the student never shared: SP-789's 「教練可以看」 lists training, plan and races)
READ_BLOCK = _rx(
    r".*/file", r".*\.zip", r"/api/v1/app/injuries.*", r"/api/v1/app/workouts/[^/]+/pain",
    r"/api/v1/racepower/(?:weather/key|shares.*|saved/[^/]+/cairn-curve)", r"/api/v1/plan/calendar.*",
)
# writes that store nothing (the demo's NO_SANDBOX and the editor's helpers): open to any viewer;
# the calculator's count is the coach's (membership)
CALC = _rx(
    r"/api/v1/racepower/(?:predict|estimate|course|plan)",
    _P + r"/(?:steps-preview|steps/derive|steps/check|steps/route|steps/from-route|sessions/suggest"
    r"|blackouts/preview|prefs/conflicts|equivalence/design)",
)

# SP-792: (switch, what is recorded, methods, path) — the only writes a coach makes in a student's data
WRITE_OPEN: list[tuple[str, str, frozenset, re.Pattern]] = [(perm, action, frozenset(m.split()), re.compile(p)) for perm, action, m, p in [
    ("plan", "session_add", "POST", _P + r"/sessions"),
    ("plan", "session_swap", "POST", _P + r"/sessions/swap"),
    ("plan", "expired_delete", "POST", _P + r"/sessions/expired/delete"),
    ("plan", "session_revert", "POST", _P + r"/sessions/(?P<uid>[^/]+)/revert"),
    ("plan", "session_link", "POST DELETE", _P + r"/sessions/(?P<uid>[^/]+)/link"),
    ("plan", "session_edit", "PATCH", _P + r"/sessions/(?P<uid>[^/]+)"),
    ("plan", "session_delete", "DELETE", _P + r"/sessions/(?P<uid>[^/]+)"),
    ("plan", "test_schedule", "POST", _P + r"/test-suggestions/schedule"),
    ("plan", "suggestion", "POST", _P + r"/suggestions/(?P<what>dismiss|accept|restore)"),
    ("plan", "reconcile", "POST", _P + r"/reconcile"),
    ("plan", "prefs", "PUT", _P + r"/prefs"),
    ("plan", "push", "POST", _P + r"/push-coros"),
    ("plan", "unpush", "DELETE", _P + r"/push-coros"),
    ("plan", "blackouts", "PUT", _P + r"/blackouts"),
    ("plan", "rest_day_move", "POST", _P + r"/rest-days/move"),
    ("plan", "rest_day", "POST", _P + r"/rest-days"),
    ("plan", "rest_day_delete", "DELETE", _P + r"/rest-days/(?P<day>[^/]+)"),
    ("plan", "event_save", "PUT", r"/api/v1/plan/events"),
    ("plan", "event_delete", "DELETE", r"/api/v1/plan/events/(?P<eid>[^/]+)"),
    ("plan", "event_gpx", "POST DELETE", r"/api/v1/plan/events/(?P<eid>[^/]+)/gpx"),
    ("plan", "event_gpx", "PUT", r"/api/v1/plan/events/(?P<eid>[^/]+)/gpx/(?:splits|group)"),
    ("plan", "phases", "PUT", r"/api/v1/plan/phases"),
    ("training", "thresholds", "PUT", r"/api/v1/plan/thresholds"),
    ("training", "thresholds_apply", "POST", r"/api/v1/plan/thresholds/apply-estimate"),
    ("training", "hr_profile", "PUT", r"/api/v1/plan/hr-profile"),
    ("training", "power_zones", "PUT", r"/api/v1/plan/power-zones"),
    ("training", "pmc_start", "PUT", r"/api/v1/plan/pmc-start"),
    ("training", "race_pace", "PUT", r"/api/v1/plan/race-pace"),
]]
# the tenant caps each switch gives the view (the shell unlocks those controls; nothing else does)
CAPS = {"plan": ("plan.write", "push", "upload.gpx"), "training": ("thresholds.write",)}

READS = ("GET", "HEAD")


# ---------------------------------------------------------------------------- the request's view

@dataclass(frozen=True)
class View:
    coach: Any                  # store.User: the signed-in coach
    student: Any                # store.User
    link: dict                  # the active coach_links row
    perms: dict                 # coaching.perms_of(link)
    tenant: Any                 # the student's tenant, its caps cut down (caps_for)


class _Ended:
    """The session views a student it may not any more (withdrawn, ended, disabled, not a coach)."""

    def __repr__(self) -> str:
        return "ENDED"


ENDED = _Ended()
_VIEW: ContextVar[Optional[View]] = ContextVar("trc_coach_view", default=None)


def current() -> Optional[View]:
    """The view of this request (set by the gate), None when nobody is viewed."""
    return _VIEW.get()


def set_view(v: Optional[View]):
    return _VIEW.set(v)


def reset(tok) -> None:
    _VIEW.reset(tok)


def caps_for(perms: dict) -> frozenset:
    return frozenset(cap for perm, caps in CAPS.items() if perms.get(perm) for cap in caps)


def resolve(user, token: Optional[str]):
    """The session's view, read from accounts.db now: None (views nobody), ENDED, or a View."""
    if user is None or not token:
        return None
    from backend.accounts import coaching as CO, store, tenants as TN
    sid = store.coach_view_of(token, user.id)
    if sid is None:
        return None
    try:
        student, link = CO._checked(user, sid)
    except CO.NotYourStudent:
        return ENDED
    if student.is_owner or int(student.id) == 1:
        return ENDED
    perms = CO.perms_of(link)
    t = dataclasses.replace(TN.tenant_of(student), caps=caps_for(perms))
    return View(coach=user, student=student, link=link, perms=perms, tenant=t)


# ---------------------------------------------------------------------------- what may go through

@dataclass(frozen=True)
class Verdict:
    ok: bool
    code: Optional[str] = None          # when refused (MESSAGES)
    perm: Optional[str] = None          # COACH_PERM_OFF: which switch
    action: Optional[str] = None        # a write that goes into the student's log
    params: dict = field(default_factory=dict)


def check(method: str, path: str, perms: dict) -> Verdict:
    method = method.upper()
    if method in READS:
        if READ_BLOCK.fullmatch(path) or not READ_OPEN.fullmatch(path):
            return Verdict(False, "COACH_VIEW_BLOCKED")
        return Verdict(True)
    if method == "OPTIONS":
        return Verdict(True)
    if method == "POST" and (path in (STOP, LOGOUT) or CALC.fullmatch(path)):
        return Verdict(True)
    for perm, action, methods, rx in WRITE_OPEN:
        m = rx.fullmatch(path)
        if m and method in methods:
            if not perms.get(perm):
                return Verdict(False, "COACH_PERM_OFF", perm=perm)
            return Verdict(True, action=action, params={k: v for k, v in m.groupdict().items() if v})
    return Verdict(False, "COACH_VIEW_READONLY")


def refusal(v: Verdict) -> dict:
    """The 403's body, in the UserError shape the pages read ({detail: {code, message, params}})."""
    from backend.accounts import coaching as CO
    from backend.i18n import _
    params = {"what": _(CO.PERMS[v.perm])} if v.perm in CO.PERMS else {}
    return {"code": v.code, "detail": {"code": v.code, "message": _(MESSAGES[v.code], **params), "params": params}}


def ended_body() -> dict:
    from backend.i18n import _
    return {"code": "COACH_VIEW_ENDED", "coach_home": COACH_HOME + "#coach",
            "detail": {"code": "COACH_VIEW_ENDED", "message": _(MESSAGES["COACH_VIEW_ENDED"]), "params": {}}}


# ---------------------------------------------------------------------------- the student's log

_KEYS = ("day", "date", "name", "title", "start", "end", "kind", "label", "priority", "distance", "minutes",
         "lthr", "aethr", "cp", "mhr", "rhr", "max_hr", "rest_hr", "model", "system", "ctl", "atl", "scope")
_MAX_TEXT = 80


def _scalar(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str) and v.strip():
        return v.strip()[:_MAX_TEXT]
    return None


def _pick(doc, out: dict) -> None:
    if not isinstance(doc, dict):
        return
    for k in _KEYS:
        if k not in out:
            v = _scalar(doc.get(k))
            if v is not None:
                out[k] = v


def summary(action: str, path_params: dict, req: Any, resp: Any, query: Optional[dict] = None) -> dict:
    """What a write changed, for the student's log: a few short values (a day, a name, the numbers set),
    never the whole request."""
    out: dict = {k: str(v)[:_MAX_TEXT] for k, v in path_params.items() if k in ("day", "what")}
    if isinstance(req, list):
        out["rows"] = len(req)
        rows = [r for r in req if isinstance(r, dict)]
        if rows and action == "thresholds":
            _pick(max(rows, key=lambda r: str(r.get("date") or "")), out)
    else:
        _pick(req, out)
        if action == "session_edit" and isinstance(req, dict):
            out["fields"] = ",".join(sorted(k for k in req if isinstance(k, str))[:8])[:_MAX_TEXT]
        if isinstance(req, dict) and isinstance(req.get("blackouts"), list):
            out["rows"] = len(req["blackouts"])
    for k in ("scope", "day"):
        if query and k not in out and query.get(k):
            out[k] = str(query[k])[:_MAX_TEXT]
    if isinstance(resp, dict):
        for k in ("day", "title", "name"):
            if k not in out and _scalar(resp.get(k)) is not None:
                out[k] = _scalar(resp[k])
    return dict(list(out.items())[:10])


def record(view: View, action: str, params: Optional[dict] = None) -> None:
    """One line in the student's log (never raises: the change itself went through)."""
    from backend.accounts import store
    try:
        store.coach_audit_add(view.student.id, view.coach.id, action, params or {})
    except Exception as e:                  # noqa: BLE001
        log.warning("coach audit failed: %s", type(e).__name__)


def parse_json(body: Optional[bytes]):
    if not body or body[:1] not in (b"{", b"["):
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
