"""
課表訂閱: the stored plan (table plan_sessions, engine/plan_store.py) as an
iCalendar feed (RFC 5545) that Google Calendar (「從網址新增」) and the iPhone
calendar (「新增訂閱行事曆」, then its widget) subscribe to.

    GET /share/calendar/<token>.ics        (api/calendar_feed.py; no login)

Events:
  - one all-day VEVENT per session from WINDOW_PAST days ago to WINDOW_FUTURE days
    ahead (sessions have a day, not a time); states active / done / missed — deleted
    and superseded rows and 課表待確認 (kind notice, a reminder) are left out, so a
    deleted session simply disappears from the feed
  - rest days and 不排課日期 have no session, so they get no event (the calendar shows
    workouts only); an edited / custom session the user kept on a blocked day is shown
  - UID = the session's uid (stable for its whole life: an edit or a move to another
    day keeps it; reconcile keeps it when it regenerates the same gen_key)
  - LAST-MODIFIED = the row's updated_at (moves only when the row changed,
    plan_store._fill), SEQUENCE = its whole seconds since SEQ_EPOCH (always increasing)
  - SUMMARY = the title, 「✓ 」 in front when done, 「✗ 」 when missed
  - DESCRIPTION = minutes / TSS (the actual ones too when done), distance / climb, the
    target text, the saved structure, the detail, and the 課表 page link that opens the
    session (schedule.html ?day=&uid=)

The feed never reconciles (it is public and must stay cheap): it shows what is
stored, i.e. the weeks the app has generated so far.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
from typing import Iterable, Optional
from urllib.parse import urlencode, urlsplit

from backend.i18n import _

WINDOW_PAST = 14                  # days of history (done ✓ / missed ✗)
WINDOW_FUTURE = 56                # 8 weeks ahead
STATES = ("active", "done", "missed")
SKIP_KINDS = ("notice",)
REFRESH = "PT1H"                  # REFRESH-INTERVAL / X-PUBLISHED-TTL hint (clients may ignore it)
SEQ_EPOCH = dt.datetime(2026, 1, 1)
PRODID = "-//TrailRunCoach//Plan calendar//EN"
SCHEDULE_PATH = "/api/v1/overview/plan/schedule/page"
FEED_PREFIX = "/share/calendar/"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,64}")


# ---------------------------------------------------------------------------
# token
# ---------------------------------------------------------------------------

def new_token() -> str:
    return secrets.token_urlsafe(24)


def clean_origin(origin: Optional[str]) -> Optional[str]:
    """`https://host[:port]` of an address, or None when it isn't an http(s) origin."""
    if not isinstance(origin, str) or not origin.strip():
        return None
    u = urlsplit(origin.strip())
    if u.scheme not in ("http", "https") or not u.netloc or "@" in u.netloc:
        return None
    return f"{u.scheme}://{u.netloc}"


def validate_setting(v) -> None:
    if not (isinstance(v, dict) and isinstance(v.get("token"), str) and TOKEN_RE.fullmatch(v["token"])):
        raise ValueError("plan.calendar must be {token, origin} or null")
    if v.get("origin") is not None and clean_origin(v["origin"]) != v["origin"]:
        raise ValueError("plan.calendar.origin must be an http(s) origin or null")


def token_ok(given: str, stored) -> bool:
    """Constant-time compare with the stored token (False when the feed is off)."""
    tok = stored.get("token") if isinstance(stored, dict) else None
    if not tok or not isinstance(given, str):
        return False
    return secrets.compare_digest(given.encode(), tok.encode())


def feed_path(token: str) -> str:
    return f"{FEED_PREFIX}{token}.ics"


def edit_url(base: str, s: dict) -> str:
    return f"{base.rstrip('/')}{SCHEDULE_PATH}?" + urlencode({"day": s["day"], "uid": s["uid"]})


# ---------------------------------------------------------------------------
# RFC 5545 text
# ---------------------------------------------------------------------------

def escape(text: str) -> str:
    """TEXT value escaping (§3.3.11)."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line: str) -> str:
    """Content lines of at most 75 octets (§3.1): CRLF + one space before each
    continuation; never splits a UTF-8 character."""
    out, cur, n, limit = [], [], 0, 75
    for ch in line:
        b = len(ch.encode("utf-8"))
        if n + b > limit:
            out.append("".join(cur))
            cur, n, limit = [], 0, 74          # the leading space takes one octet
        cur.append(ch)
        n += b
    out.append("".join(cur))
    return "\r\n ".join(out)


def _date(d: str) -> str:
    return d.replace("-", "")


def _utc(t: dt.datetime) -> str:
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t.strftime("%Y%m%dT%H%M%SZ")


def sequence(updated: Optional[dt.datetime]) -> int:
    if updated is None:
        return 0
    if updated.tzinfo is not None:
        updated = updated.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return max(0, int((updated - SEQ_EPOCH).total_seconds()))


def summary(s: dict) -> str:
    mark = {"done": "✓ ", "missed": "✗ "}.get(s.get("state"), "")
    return mark + (s.get("title") or "").strip()


def _structure(steps) -> str:
    if not isinstance(steps, dict) or not steps.get("items"):
        return ""
    from backend.engine import workout_steps as WS
    try:
        return WS.structure_text(steps)
    except (KeyError, TypeError, ValueError):
        return ""


def description(s: dict, link: Optional[str]) -> str:
    lines = []
    plan = [_("{m} 分", m=int(s.get("minutes") or 0))] if s.get("minutes") else []
    if s.get("tss"):
        plan.append(f"TSS {round(float(s['tss']))}")
    if plan:
        lines.append(_("計畫：{text}", text=" · ".join(plan)))
    act = s.get("done_by") if s.get("state") == "done" and isinstance(s.get("done_by"), dict) else None
    if act:
        got = []
        if act.get("moving_s"):
            got.append(_("{m} 分", m=round(float(act["moving_s"]) / 60)))
        if act.get("tss"):
            got.append(f"TSS {round(float(act['tss']))}")
        if got:
            lines.append(_("實際：{text}", text=" · ".join(got)))
    dist = []
    if s.get("distance_km"):
        dist.append(f"{float(s['distance_km']):.1f} km")
    if s.get("climb_m"):
        dist.append(_("爬升 {m} m", m=round(float(s["climb_m"]))))
    if dist:
        lines.append(" · ".join(dist))
    for t in (s.get("target"), _structure(s.get("steps")), s.get("detail")):
        t = (t or "").strip()
        if t and t not in lines:
            lines.append(t)
    if link:
        lines += ["", _("在課表打開這堂課：{url}", url=link)]
    return "\n".join(lines)


def in_window(s: dict, today: dt.date) -> bool:
    d = s.get("day")
    if not d or s.get("state") not in STATES or s.get("kind") in SKIP_KINDS:
        return False
    lo = (today - dt.timedelta(days=WINDOW_PAST)).isoformat()
    hi = (today + dt.timedelta(days=WINDOW_FUTURE)).isoformat()
    return lo <= d <= hi


def build(sessions: Iterable[tuple[dict, Optional[dt.datetime]]], today: dt.date, base: Optional[str],
          now: Optional[dt.datetime] = None, host: str = "trailruncoach") -> str:
    """The VCALENDAR text (CRLF lines, folded). `sessions`: (plan_store.to_dict row,
    its updated_at); rows outside the window / state are skipped here."""
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = _utc(now)
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
           "X-WR-CALNAME:" + escape(_("課表（TrailRunCoach）")),
           f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}", f"X-PUBLISHED-TTL:{REFRESH}"]
    rows = sorted(((s, u) for s, u in sessions if in_window(s, today)), key=lambda x: (x[0]["day"], x[0]["uid"]))
    for s, updated in rows:
        start = dt.date.fromisoformat(s["day"])
        link = edit_url(base, s) if base else None
        ev = ["BEGIN:VEVENT", f"UID:{s['uid']}@{host}", f"DTSTAMP:{stamp}",
              f"DTSTART;VALUE=DATE:{_date(s['day'])}",
              f"DTEND;VALUE=DATE:{_date((start + dt.timedelta(days=1)).isoformat())}",
              f"SEQUENCE:{sequence(updated)}"]
        if updated is not None:
            ev.append(f"LAST-MODIFIED:{_utc(updated)}")
        ev += ["SUMMARY:" + escape(summary(s)), "DESCRIPTION:" + escape(description(s, link)),
               "TRANSP:TRANSPARENT", "STATUS:CONFIRMED"]
        if link:
            ev.append(f"URL:{link}")
        ev.append("END:VEVENT")
        out += ev
    out.append("END:VCALENDAR")
    return "".join(fold(x) + "\r\n" for x in out)
