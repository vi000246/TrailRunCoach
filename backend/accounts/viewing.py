"""
代看 (SP-791) and the coach's edits (SP-792): a coach looks at one student's data the way the student
sees it — the login gate (tenancy_mw) puts the student's tenant on the request while the coach stays
the signed-in user (login.current_user) — but only through these rules, default deny both ways:

  reads (GET / HEAD)   READ_OPEN only (the pages and data of 總覽, 課表, 圖表, 活動, 路線, 賽事, 賽事計算機
                       and 設定's 生理數據 tab), never READ_BLOCK; READ_PERM under the student's switch
                       (SP-822: "injury", the injury log and a run's pain, off unless turned on; while it is
                       off the plan's texts say 「傷病」 for what the injury is, SP-948 redact). Everything
                       else — the account, the platform connections and their passwords, deleting,
                       下載我的資料 and every file download, payment, 疊石 Cairn, the admin page, the debug
                       API, the backups, the calendar feed's address — 403 COACH_VIEW_BLOCKED (a page: back
                       to the overview, which says why)
  writes               CALC (computations that store nothing: the race calculator, the step editor's
                       previews), STOP and signing out; SP-792's WRITE_OPEN, each under the student's
                       switch (coaching.PERMS: "plan" 課表／賽事／不排課日, "training" 門檻與訓練設定) —
                       403 COACH_PERM_OFF while that switch is off. Any other write 403 COACH_VIEW_READONLY —
                       inside an open route too where it would write the injury log (SP-947: the
                       suggestion box's 「好了」 on 「看起來好了？」, injury_done; owner_only() on the row)
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
# closed even where READ_OPEN would let it through: file downloads (下載資料), the weather key, the shares,
# Cairn's curve, the calendar feed's address
READ_BLOCK = _rx(
    r".*/file", r".*\.zip",
    r"/api/v1/racepower/(?:weather/key|shares.*|saved/[^/]+/cairn-curve)", r"/api/v1/plan/calendar.*",
)
# SP-822: reads opened by a switch of the student's (coaching.PERMS) — closed (COACH_VIEW_BLOCKED) while it
# is off. "injury": the injury log (its page, list, analysis, timeline, days) and a run's pain — health
# records SP-789's 「教練可以看」 never listed, so off until the student turns it on. Read only: every
# write of theirs stays COACH_VIEW_READONLY. The pain marks inside the activity answers follow the same
# switch (hides_injury, api/wko5views.py).
READ_PERM: list[tuple[str, re.Pattern]] = [
    ("injury", _rx(r"/api/v1/app/injuries(?:/meta|/analysis|/timeline|/page|/[^/]+/days)?",
                   r"/api/v1/app/workouts/[^/]+/pain")),
]
# the fields of an activity that carry its pain mark (activity_tags: pain, area, side, score, the event)
INJURY_FIELDS = ("pain", "pain_area", "pain_side", "pain_score", "injury_id", "pain_state")
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


def hides_injury() -> bool:
    """SP-822: this request views a student who has not let their coach see the injury log — the answers
    that carry a run's pain mark (the activity list, an activity's card) leave INJURY_FIELDS out."""
    v = current()
    return v is not None and hides_injury_of(v.perms)


def readonly() -> Optional[dict]:
    """SP-947: the 403 body of a write only the student may make (the injury log), inside a route the gate
    lets through on a switch (WRITE_OPEN) — None when nobody is viewed. Whatever the switches: 「看傷病紀錄」
    opens reads, never a write."""
    return None if current() is None else refusal(Verdict(False, "COACH_VIEW_READONLY"))


OWNER_ONLY = N_("只有學員本人能標成好了")


def owner_only() -> Optional[str]:
    """SP-947: what the suggestion box shows a viewing coach in place of 「好了」 on 「看起來好了？」 (None when
    nobody is viewed)."""
    from backend.i18n import _
    return None if current() is None else _(OWNER_ONLY)


# ---------------------------------------------------------------------------- SP-948: the injury words
# With 「看傷病紀錄」 off, the texts the plan and its cards build from the injury log (the week notes 「右膝進行中
# （第 5 天…）」, a session's 「右膝・膝前痛進行中：…改成平路輕鬆跑」, the light 「疼痛黃燈（右膝）：上一次 6/10…」,
# 「傷停 12 天（右膝，傷病紀錄 #12）」, a cold's rule, 賽前完備 「進行中：右膝」, …) still say the reason to a viewing
# coach but not what it is: the body part, the injury type, the pain score and the illness type become 「傷病」
# (SP-947 Q1 = b). Most of them are stored in the plan when it is built (plan_store: one cache whoever asks), so
# this is done on the way out — the gate rewrites the JSON answers of REDACT_READS (redact_body); the plan is
# computed as before, the student and a coach with the switch on read the original. The words come from the
# student's own injury log, in both languages (a stored zh-TW text is shown in English on the way out); the
# box's injury rows (INJURY_ROWS: questions about the student's own body) are left out of the coach's box.
INJURY_ROWS = frozenset({"injury_rest", "injury_hold", "injury_done", "injury_pattern"})
REDACT_READS = _rx(r"/api/v1/overview/.+", r"/api/v1/app/views/.+", r"/api/v1/app/races(?:/.*)?",
                   r"/api/v1/plan(?:/.*)?")
# fields an answer carries that its page doesn't show but that name the injury: weekplan's light (its label,
# condition, the walk-run episodes) goes; reentry's injury / illness ({id, label, illness}) are emptied
_DROP_KEYS = frozenset({"injury_light"})
_EMPTY_IN = {"reentry": ("injury", "illness")}
WORD = N_("傷病")
WORD_EN = "injury"          # en: the catalog's 「傷病」 is a page title (Injuries), not a word inside a sentence
FEVER = N_("發燒")          # inside 「發燒好了之後的第一次跑」「恢復跑（發燒後第一次）」
# a pain score (engine/injuries._mark_light) -> what it says without the score
SCORES = (
    (N_("{s}/10，{n}/10 以上"), N_("疼痛分數 {n}/10 以上")),
    (N_("上一次 {s}/10，超過{name}的 {lim}/10"), N_("上一次的疼痛分數超過上限")),
    (N_("上一次 {s}/10，比再前一次（{p}/10）高 {d} 分"), N_("上一次的疼痛分數比再前一次高")),
    (N_("上一次 {s}/10，比再前一次（{p}/10）高"), N_("上一次的疼痛分數比再前一次高")),
    (N_("上一次 {s}/10"), N_("上一次有標疼痛分數")),
)
_NUMERIC = ("s", "n", "p", "d", "lim")
# a condition's study names it (膝前痛 Esculier 2016, 髂脛束 Fredericson 2005: injuries.CONDITION_NOTE)
_CITES = re.compile(r"\s*[（(](?:Esculier 2016|Fredericson 2005)[）)]")
_CASE = object()            # a _TIDY replacement: the word, its first letter's case kept
# what is left around a 「傷病」 put in
_TIDY = {
    "zh-TW": [(re.compile(r"傷病紀錄 #\d+（傷病）"), _CASE),
              (re.compile(r"（傷病，傷病紀錄 #\d+(?:；傷病，#\d+)?）"), ""),
              (re.compile(r"傷病(?:[・：、]傷病(?!紀錄))+"), _CASE)],
    "en": [(re.compile(r"\binjury log #\d+ \(injury\)", re.I), _CASE),
           (re.compile(r"\s*\(injury, injury log #\d+(?:; injury, #\d+)?\)", re.I), ""),
           (re.compile(r"\b(injury)(?:\s*(?:·|:|,)\s*injury\b(?! log))+", re.I), r"\1"),
           (re.compile(r"\ba injury\b"), "an injury")],
}
_TIDY_ANY = [(re.compile(r"\s*[（(]\s*[）)]"), ""), (re.compile(r"[；;，,]\s*(?=[）)。]|$)"), ""),
             (re.compile(r"；{2,}"), "；"), (re.compile(r"(?<=\S) {2,}"), " ")]


def hides_injury_of(perms: Optional[dict]) -> bool:
    return (perms or {}).get("injury") is not True


def redacts(method: str, path: str, perms: Optional[dict]) -> bool:
    """SP-948: the gate rewrites this read's answer (redact_body) — a viewing coach without 「看傷病紀錄」."""
    return method.upper() in READS and hides_injury_of(perms) and bool(REDACT_READS.fullmatch(path))


def _tpl_rx(entry: str) -> Optional[re.Pattern]:
    """A catalog entry (「上一次 {s}/10，比再前一次（{p}/10）高 {d} 分」; en with 「{d|point|points}」) as a regex
    that finds it inside a text, its fields named."""
    out, seen = [], set()
    for tok in re.split(r"(\{[^{}]*\})", entry):
        if not (tok.startswith("{") and tok.endswith("}")):
            out.append(re.escape(tok))
            continue
        body = tok[1:-1]
        if "|" in body:                         # a count's words
            out.append("(?:" + "|".join(re.escape(w) for w in body.split("|")[1:]) + ")")
            continue
        name = body.split(":")[0].split("!")[0]
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            return None
        if name in seen:
            out.append(f"(?P={name})")
            continue
        seen.add(name)
        out.append(rf"(?P<{name}>[-+]?\d+(?:\.\d+)?)" if name in _NUMERIC else f"(?P<{name}>.+?)")
    return re.compile("".join(out))


def _rules(loc: str, events: list) -> dict:
    """What redact changes in a text of `loc`: {"scores": [(regex, msgid)], "terms": a regex of the words that
    become WORD (the student's injuries as labelled, their conditions, every illness type), "drop": a regex of
    what goes (an illness's sources: they say which one), "word", "loc"}."""
    from backend.engine import injuries as INJ
    from backend.i18n import catalog, translate, use_locale
    cat = catalog(loc)

    def entry(msgid: str) -> str:
        e = cat.get(msgid) if cat else msgid
        return e if isinstance(e, str) and e else msgid
    scores = [(rx, rep) for src, rep in SCORES if (rx := _tpl_rx(entry(src))) is not None]
    terms, raw = set(), set()
    with use_locale(loc):
        for e in events or ():
            if INJ.is_illness(e):
                continue
            terms.add(INJ.full_label(e.get("area"), e.get("side")))
            # 百岳 multi-day's English label (baiyue_multiday: the stored keys)
            raw.add(" ".join(str(x).replace("_", " / ") for x in (e.get("side"), e.get("area")) if x))
            if e.get("condition") in INJ.CONDITIONS and e.get("condition") != "other":
                terms.add(translate(INJ.CONDITIONS[e["condition"]], loc))
        for k in [*INJ.ILLNESS, None]:
            terms.add(INJ.illness_label(k))
        terms |= {translate(v, loc) for v in INJ.ILLNESS.values()} | {translate(FEVER, loc)}
        drop = {translate(INJ.SRC_COLD, loc), translate(INJ.SRC_FEVER, loc)}
    if loc != "zh-TW":
        terms |= raw
    alts = []
    for t in sorted({t.strip() for t in terms if t and t.strip()}, key=len, reverse=True):
        if t.isascii():
            alts.append(rf"(?<![\w-]){re.escape(t)}(?![\w-])")
        elif len(t) == 1:                       # 「膝」 alone (no side): not the 膝 of 「膝主導」
            alts.append(re.escape(t) + r"(?=[進・：:，,、）)\s]|$)")
        else:
            alts.append(re.escape(t))
    return {"scores": scores, "loc": loc,
            "terms": re.compile("|".join(alts), re.I if loc == "en" else 0) if alts else None,
            "drop": re.compile(r"[；;]?\s*(?:" + "|".join(re.escape(d) for d in sorted(drop, key=len, reverse=True)) + ")"),
            "word": WORD_EN if loc == "en" else translate(WORD, loc)}


def _redact_text(s: str, rules: list) -> str:
    from backend.i18n import translate
    if not s:
        return s
    was = s
    for r in rules:
        loc, word = r["loc"], r["word"]

        def cased(m, word=word):
            return word[:1].upper() + word[1:] if m.group(0)[:1].isupper() else word
        before = s
        for rx, rep in r["scores"]:
            s = rx.sub(lambda m, rep=rep, loc=loc: translate(rep, loc, **m.groupdict()), s)
        s = r["drop"].sub("", s)
        if r["terms"] is not None:
            s = r["terms"].sub(cased, s)
        if s != before:
            for rx, rep in _TIDY.get(loc, ()):
                s = rx.sub(cased if rep is _CASE else rep, s)
    if s == was:                                # nothing of the injury's: the text as it was
        return s
    s = _CITES.sub("", s)
    for rx, rep in _TIDY_ANY:
        s = rx.sub(rep, s)
    return s


def redact(obj: Any, events: Optional[list] = None) -> Any:
    """SP-948: `obj` (an answer) as a viewing coach without 「看傷病紀錄」 reads it — its texts' injury words
    replaced (see above), _DROP_KEYS left out, INJURY_ROWS out of every list. `events`: the student's injury
    log (default: this tenant's)."""
    from backend.i18n import SUPPORTED
    if events is None:
        from backend.engine import injuries as INJ
        events = INJ.load_events()
    rules = [_rules(loc, events) for loc in SUPPORTED]

    def walk(x, key=None):
        if isinstance(x, str):
            return _redact_text(x, rules)
        if isinstance(x, dict):
            return {k: (None if k in _EMPTY_IN.get(key, ()) else walk(v, k)) for k, v in x.items() if k not in _DROP_KEYS}
        if isinstance(x, (list, tuple)):
            return [walk(v) for v in x if not (isinstance(v, dict) and v.get("type") in INJURY_ROWS)]
        return x
    return walk(obj)


def redact_body(body: bytes) -> bytes:
    """A JSON answer's bytes, redacted (redact); anything that isn't JSON as it is."""
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return body
    return json.dumps(redact(doc), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


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
        for perm, rx in READ_PERM:
            if rx.fullmatch(path):
                return Verdict(True) if perms.get(perm) is True else Verdict(False, "COACH_VIEW_BLOCKED")
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
