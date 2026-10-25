"""
Coaches and their students (SP-789; SP-56 settled the shape: C, the mixed way). The rows are
accounts.db's (store.py: coach_profiles, coach_links, coach_invites); this module has the rules.

Who is a coach: an admin makes someone one on the admin page (no payment yet, SP-56 Q5 (b); the
coach plan will be a subscription of its own later). Being a coach is apart from the person's own
plan (free or subscriber for their own data): membership's COACH role stays unused until that plan.

Becoming someone's coach takes the student's yes, whoever asked (SP-56 Q2 (c)):
  - the coach sends their invite link (one per coach, INVITE_TTL_DAYS, any number of students may use
    it; only the sha256 of its token is kept) -> the student opens it signed in, sees who it is and
    presses 接受 -> active at once (the coach asked by sending it)
  - the student types the coach's code (CODE_LEN characters, on the coach's page; not a secret: it
    only asks) -> pending -> the coach accepts (active) or declines
A student has at most MAX_COACHES_PER_STUDENT live links (pending or active; 1 in the first version:
two coaches would fight over one plan) — the table itself is many to many. A coach has at most their
own max_students active students (SP-793; DEFAULT_MAX_STUDENTS unless the admin page set another):
the yes that would take one more is refused (COACH_FULL) and says why; checked in the same write
transaction as the change.

The student withdraws whenever they like, the coach lets a student go, the admin taking the coach
away ends all of theirs. Every accept and every end of an active link is a consents row of the
student (action "coach_accept" / "coach_end", client "coach": who, which link, who asked, who ended
it), so 下載我的資料 has the history (accounts/export.py also lists the links themselves).

Reading a student's data — THE rule for SP-790 (the coach's dashboard), SP-791 (代看) and SP-792
(the coach editing a plan): only through student_of() / student_tenant() below, on every request.
They read accounts.db again each time (store.active_coach_link: the link active, both accounts
enabled, the coach still a coach), so a withdrawal counts from the very next request — 403
NOT_YOUR_STUDENT, the same answer whether the id is someone else's student, nobody's, pending, ended
or no account at all. Never `tenants.tenant_of(store.get_user(<an id from the request>))`.
`perms` on the link is where SP-792's switches go (PERMS; the student turns each on or off), read with
allows() / perms_of(). 代看 (SP-791, accounts/viewing.py) binds the login gate's tenant to the student's
with the coach still the signed-in user (login.current_user), so the audit sees who did it.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from backend.accounts import store
from backend.i18n import N_, UserError, _
from backend.security import ratelimit as RL

log = logging.getLogger("backend.coaching")

DEFAULT_MAX_STUDENTS = 4               # SP-793: a coach's students unless the admin page set another
MAX_STUDENTS_LIMIT = 1000              # the most the admin page may set
MAX_COACHES_PER_STUDENT = 1            # SP-56 Q3: one coach per student in the first version
INVITE_TTL_DAYS = 30
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0 O 1 I: read aloud and typed by hand
CODE_LEN = 8
# SP-792: what a student allows on top of reading, each a switch of their own (SP-56 Q3: the coach may,
# the student turns it off). A link that never had its switches set (perms '{}') has PERMS_DEFAULT.
# The routes each switch opens are accounts/viewing.WRITE_OPEN's.
PERMS: dict[str, str] = {
    "plan": N_("改課表、賽事與不排課日"),
    "training": N_("改門檻與訓練設定（LTHR、CP、區間）"),
}
PERMS_DEFAULT: dict[str, bool] = {"plan": True, "training": True}

# a student typing codes that are nobody's (guessing): this many a user in 10 minutes
CODE_FAILS = RL.Buckets(rate=10, per=600)

REFUSED = {
    "SELF": (N_("不能當自己的教練"), 409),
    "NOT_A_COACH": (N_("這位不是教練（或已經不是了）"), 409),
    "NO_STUDENT": (N_("這個帳號不能加入教練"), 409),
    "HAS_COACH": (N_("你已經有一位教練了。先在「我的教練」解除，才能換教練"), 409),
    "REQUEST_PENDING": (N_("你已經向一位教練送出邀請，等對方回覆或先取消，才能再找另一位"), 409),
    "COACH_FULL": (N_("這位教練的學員已滿（{n} 位），請教練先解除一位學員，或請管理員調高上限"), 409),
    "NO_REQUEST": (N_("這個邀請已經不在了"), 409),
    "NO_LINK": (N_("這段教練關係已經不在了"), 409),
}


class NotYourStudent(UserError):
    def __init__(self):
        super().__init__(N_("你不是這位學員的教練，或學員已經撤回授權"), code="NOT_YOUR_STUDENT", status=403)


class NotACoach(UserError):
    def __init__(self):
        super().__init__(N_("只有教練能用這個功能"), code="NOT_A_COACH", status=403)


def refused(e: store.CoachRefused, coach_id: Optional[int] = None) -> UserError:
    msg, status = REFUSED.get(e.code, (N_("無法完成"), 409))
    params = {"n": max_students(coach_id)} if e.code == "COACH_FULL" and coach_id else {}
    return UserError(msg, code=e.code, status=status, **params)


def reset_limits() -> None:
    CODE_FAILS.reset()


# ---------------------------------------------------------------------------- reading a student (the gate)

def _checked(coach, student_id) -> tuple:
    try:
        sid = int(student_id)
    except (TypeError, ValueError):
        raise NotYourStudent() from None
    link = store.active_coach_link(coach.id, sid) if coach is not None and 0 < sid <= 2 ** 62 else None
    if link is None:
        raise NotYourStudent()
    u = store.get_user(sid)
    if u is None or u.disabled:
        raise NotYourStudent()
    return u, link


def student_of(coach, student_id) -> store.User:
    """The student `student_id` of `coach` (the signed-in user), read from accounts.db now; raises
    NotYourStudent (403) unless their link is active, both accounts are enabled and the coach is
    still a coach. The id from a request goes through here and nowhere else."""
    return _checked(coach, student_id)[0]


def perms_of(link: Optional[dict]) -> dict[str, bool]:
    """A link's switches, every one of PERMS (PERMS_DEFAULT for the ones never set); all off for no link."""
    if not link:
        return {k: False for k in PERMS}
    try:
        raw = json.loads(link.get("perms") or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {k: bool(raw[k]) if isinstance(raw.get(k), bool) else PERMS_DEFAULT.get(k, False) for k in PERMS}


def student_info(coach, student_id) -> dict:
    """GET /api/v1/coach/students/<id>: who, since when, what they allow (SP-792's switches)."""
    u, link = _checked(coach, student_id)
    return {"student_id": u.id, "name": _name(u.display_name, u.id), "since": link["accepted_at"],
            "link_id": link["id"], "perms": perms_of(link)}


async def student_tenant(coach, student_id):
    """student_of(), then that student's tenant, its folder ready — what SP-790 / SP-791 read in."""
    from starlette.concurrency import run_in_threadpool
    from backend.accounts import tenants as TN
    u = await run_in_threadpool(student_of, coach, student_id)
    t = TN.tenant_of(u)
    await TN.ensure_ready(t)
    return t


@asynccontextmanager
async def in_student(coach, student_id):
    """`async with in_student(coach, sid) as student:` the student's tenant is the current one inside
    (tenancy.use), after the check."""
    from backend import tenancy
    t = await student_tenant(coach, student_id)
    with tenancy.use(t):
        yield t


def allows(coach, student_id, perm: str) -> bool:
    """SP-792: the student allowed `perm` (one of PERMS) to their coach — False for anything else."""
    if perm not in PERMS:
        return False
    link = store.active_coach_link(coach.id, int(student_id)) if coach is not None else None
    return perms_of(link)[perm] if link is not None else False


def set_perms(student, link_id, changes) -> dict:
    """The student turns switches of an active link of theirs on or off (SP-792): `changes` {perm: bool}
    of PERMS only (400 otherwise); the others stay as they are. A consents row records the new set."""
    if not isinstance(changes, dict) or not changes or any(k not in PERMS or not isinstance(v, bool)
                                                           for k, v in changes.items()):
        raise UserError(N_("這個設定不存在"), code="BAD_PERM", status=400)
    link = next((r for r in store.coach_links_of(student.id, "student") if int(r["id"]) == int(link_id)
                 and r["status"] == "active"), None)
    if link is None:
        raise refused(store.CoachRefused("NO_LINK"))
    perms = {**perms_of(link), **changes}
    try:
        store.set_coach_perms(int(link_id), student.id, perms)
    except store.CoachRefused as e:
        raise refused(e) from None
    log.info("coach permissions changed by the student")
    return perms


# ---------------------------------------------------------------------------- coaches

def is_coach(user) -> bool:
    return user is not None and store.coach_profile(user.id) is not None


def require_coach(user) -> dict:
    p = store.coach_profile(user.id) if user is not None else None
    if p is None:
        raise NotACoach()
    return p


def max_students(coach_id: Optional[int]) -> int:
    p = store.coach_profile(coach_id) if coach_id else None
    return DEFAULT_MAX_STUDENTS if p is None or p.get("max_students") is None else int(p["max_students"])


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _i in range(CODE_LEN))


def norm_code(raw) -> Optional[str]:
    """What a person typed -> a code (upper case, spaces and dashes dropped), None when it can't be one."""
    if not isinstance(raw, str) or len(raw) > 40:
        return None
    c = "".join(ch for ch in raw.upper() if ch not in " -")
    return c if len(c) == CODE_LEN and all(ch in CODE_ALPHABET for ch in c) else None


def set_coach(uid: int, on: bool, by: Optional[int] = None) -> bool:
    """The admin page's switch. Taking it away ends every link of theirs (store.set_coach)."""
    for _try in range(8):
        try:
            changed = store.set_coach(uid, on, code=new_code() if on else None, by=by)
            if changed:
                log.info("coach %s", "granted" if on else "taken away")
            return changed
        except sqlite3.IntegrityError:          # a code someone has: draw again
            continue
    raise RuntimeError("no free coach code")


def set_max(uid: int, n: Optional[int]) -> None:
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_STUDENTS_LIMIT):
        raise ValueError(f"max_students must be 0..{MAX_STUDENTS_LIMIT} or null")
    store.set_coach_max(uid, n)


def reset_code(coach) -> str:
    require_coach(coach)
    for _try in range(8):
        code = new_code()
        try:
            store.set_coach_code(coach.id, code)
            return code
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("no free coach code")


def new_invite(coach) -> tuple[str, str]:
    """A new invite link's token (the older link stops working) and when it ends. The token is in
    this answer only."""
    require_coach(coach)
    token = secrets.token_urlsafe(32)
    exp = store.put_coach_invite(coach.id, token, INVITE_TTL_DAYS * 86400)
    log.info("coach invite link made")
    return token, exp


def invite_url(token: str) -> str:
    """Where the invite link takes the student: 設定 › 教練, which asks them to accept. The token in a
    query parameter whose name the request log masks (applog: *token*), so it survives the sign-in."""
    from urllib.parse import quote
    from backend.accounts import login as LG
    return ((LG._env(LG.ENV_PUBLIC_URL) or "").rstrip("/")
            + f"/api/v1/app/settings?coach_token={quote(token, safe='')}#coach")


def _name(name: Optional[str], uid: int) -> str:
    return name or _("跑者 #{id}", id=uid)


def coach_view(coach) -> dict:
    """The coach's own page: their code, invite link (when it ends; never the token), students."""
    p = require_coach(coach)
    rows = store.coach_links_of(coach.id, "coach")
    used = sum(1 for r in rows if r["status"] == "active")
    return {"is_coach": True, "code": p["code"], "invite": store.coach_invite_of(coach.id),
            "max_students": max_students(coach.id), "used": used,
            "full": used >= max_students(coach.id),
            "students": [{"link_id": r["id"], "student_id": r["student_id"],
                          "name": _name(r["other_name"], r["student_id"]), "status": r["status"],
                          "initiated_by": r["initiated_by"], "since": r["accepted_at"] or r["created_at"],
                          "disabled": bool(r["other_disabled"])} for r in rows]}


def student_view(user) -> dict:
    """The student's 我的教練: their live links (never the coach's e-mail or account id)."""
    rows = store.coach_links_of(user.id, "student")
    # SP-792: the switches, each with its words (an active link only: a request allows nothing yet)
    return {"max": MAX_COACHES_PER_STUDENT, "is_coach": is_coach(user),
            "perm_labels": {k: _(v) for k, v in PERMS.items()},
            "coaches": [{"link_id": r["id"], "name": _name(r["other_name"], r["coach_id"]), "status": r["status"],
                         "initiated_by": r["initiated_by"], "since": r["accepted_at"] or r["created_at"],
                         "perms": perms_of(r) if r["status"] == "active" else {k: False for k in PERMS},
                         "can_see": _("你的訓練紀錄、圖表、課表與賽事")} for r in rows]}


def pending_requests(coach) -> Optional[dict]:
    """SP-823: the students waiting for this coach to say yes (they typed the code; an enabled account),
    {n, at: the newest request's time} — the shell's line on every page; None when there are none."""
    rows = [r for r in store.coach_links_of(coach.id, "coach")
            if r["status"] == "pending" and r["initiated_by"] == "student" and not r["other_disabled"]]
    if not rows:
        return None
    return {"n": len(rows), "at": max(r["created_at"] for r in rows)}


def coach_preview(coach_user) -> dict:
    """Who the student is about to say yes to (name only)."""
    return {"name": _name(coach_user.display_name, coach_user.id)}


# ---------------------------------------------------------------------------- the changes

def request_by_code(student, raw_code) -> dict:
    """The student typed a coach's code: a pending request. A code that is nobody's counts against
    CODE_FAILS (429 after too many)."""
    coach = lookup_code(student, raw_code)
    try:
        link = store.coach_request(coach.id, student.id, MAX_COACHES_PER_STUDENT)
    except store.CoachRefused as e:
        raise refused(e, coach.id) from None
    log.info("coach request sent")
    return link


def lookup_code(student, raw_code) -> store.User:
    key = f"u{student.id}"
    code = norm_code(raw_code)
    coach = store.coach_by_code(code) if code else None
    if coach is None:
        if not CODE_FAILS.take(key):
            raise UserError(N_("試太多次了，請 10 分鐘後再試"), code="RATE_LIMITED", status=429)
        raise UserError(N_("找不到這個教練代碼"), code="NO_CODE", status=404)
    if coach.id == student.id:
        raise refused(store.CoachRefused("SELF"))
    return coach


def lookup_invite(student, token) -> store.User:
    coach = store.coach_by_invite(token if isinstance(token, str) and len(token) <= 200 else None)
    if coach is None:
        raise UserError(N_("這個邀請連結無效或已過期，請教練重新產生一個"), code="INVITE_INVALID", status=404)
    if coach.id == student.id:
        raise refused(store.CoachRefused("SELF"))
    return coach


def accept_invite(student, token) -> dict:
    coach = lookup_invite(student, token)
    try:
        link = store.coach_accept_invite(coach.id, student.id, MAX_COACHES_PER_STUDENT, DEFAULT_MAX_STUDENTS)
    except store.CoachRefused as e:
        raise refused(e, coach.id) from None
    log.info("coach invite accepted")
    return link


def accept_request(coach, link_id) -> dict:
    require_coach(coach)
    try:
        link = store.coach_accept_request(coach.id, int(link_id), MAX_COACHES_PER_STUDENT, DEFAULT_MAX_STUDENTS)
    except store.CoachRefused as e:
        raise refused(e, coach.id) from None
    log.info("coach request accepted")
    return link


def end_as_student(student, link_id) -> dict:
    try:
        link = store.coach_end(int(link_id), "student", student.id)
    except store.CoachRefused as e:
        raise refused(e) from None
    log.info("coaching ended by the student")
    return link


def end_as_coach(coach, link_id) -> dict:
    """The coach lets a student go or declines a request — even after the admin took the coach role
    away nothing is left to end (set_coach ended them all)."""
    try:
        link = store.coach_end(int(link_id), "coach", coach.id)
    except store.CoachRefused as e:
        raise refused(e) from None
    log.info("coaching ended by the coach")
    return link


# ---------------------------------------------------------------------------- 下載我的資料, the admin page

def export_record(user) -> dict:
    """The person's coaching for 下載我的資料: their coach profile (code, limit), their coaches and
    their students — every link, ended ones too — by name (never an e-mail or a token)."""
    p = store.coach_profile(user.id)

    def row(r) -> dict:
        try:
            perms = json.loads(r.get("perms") or "{}")
        except ValueError:
            perms = {}
        return {"name": r["other_name"], "status": r["status"], "initiated_by": r["initiated_by"], "perms": perms,
                "created_at": r["created_at"], "accepted_at": r["accepted_at"], "ended_at": r["ended_at"],
                "ended_by": r["ended_by"]}
    return {"coach": {"code": p["code"], "max_students": max_students(user.id), "since": p["granted_at"]} if p else None,
            "my_coaches": [row(r) for r in store.coach_links_of(user.id, "student", live_only=False)],
            "my_students": [row(r) for r in store.coach_links_of(user.id, "coach", live_only=False)]}


def admin_rows() -> dict[int, dict]:
    """user id -> {max_students, used, default} of every coach (the admin page's column)."""
    return {uid: {"max_students": DEFAULT_MAX_STUDENTS if p["max_students"] is None else int(p["max_students"]),
                  "custom": p["max_students"] is not None, "used": int(p["used"])}
            for uid, p in store.coach_profiles().items()}
