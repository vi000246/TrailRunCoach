"""
Cross-product events and the sign-up source (SP-765; research SP-571 §4.3, docs/research/sp571-cross-promotion.md).

TrailRunCoach had no product events (accounts.db's usage table is the free tier's quotas, SP-330):
xp_events counts, per user and day, the four events both products name alike —

    xp-card-shown     the 疊石 Cairn card was on screen (SP-763: 賽事管理's editor, the calculator's days)
    xp-card-click     its 「打開疊石 Cairn」 was pressed
    xp-card-dismiss   its 「不用了」 was pressed
    xp-linked         the account was linked with Cairn (設定 › 已連結的服務, SP-329)

— and users.signup_source keeps where a new account came from: "cairn" when the sign-up started from a
page whose address carries from=cairn (Cairn's 「用 TrailRunCoach 算這條」 link, SP-764: the sign-in's
next survives Google and the terms); "share" from a race share page's 「用自己的資料算」 (/share/<id>,
login.join_link("share"); SP-389 Q1-2, owner 2026-10-20); none otherwise. Counted only for a signed-in user (owner mode and the demo have no
account): nothing about what was on the card, the route or the page beyond the event's name.

The monthly funnel and the 30-day retention: backend/scripts/xp_funnel.py.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

SHOWN, CLICK, DISMISS, LINKED = "xp-card-shown", "xp-card-click", "xp-card-dismiss", "xp-linked"
EVENTS = (SHOWN, CLICK, DISMISS, LINKED)
CLIENT_EVENTS = (SHOWN, CLICK, DISMISS)        # what a page may report; xp-linked is the server's own
SOURCES = ("cairn", "share")                   # from=<source> values a sign-up keeps
MAX_PER_DAY = 200                              # one user, one event, one day: more is a page in a loop
TZ = dt.timezone(dt.timedelta(hours=8))        # the app's day (ops_report, jobs._account)


def today() -> str:
    return dt.datetime.now(TZ).date().isoformat()


def record(user, event: str) -> bool:
    """Count `event` for `user` today; False when not counted (no user, unknown event, the day's cap,
    accounts.db unreadable — an event never breaks the page that sends it)."""
    if user is None or event not in EVENTS:
        return False
    from backend.accounts import store
    try:
        return store.xp_add(user.id, event, today(), MAX_PER_DAY) is not None
    except Exception as e:                 # noqa: BLE001 — a count is never worth a failed request
        log.warning("xp event not recorded: %s", type(e).__name__)
        return False


def source_of(next_url: Optional[str]) -> Optional[str]:
    """The sign-up source in a sign-in's next (a path on this site): from=cairn → "cairn"; else None."""
    if not next_url or not isinstance(next_url, str):
        return None
    try:
        q = parse_qs(urlsplit(next_url).query)
    except ValueError:
        return None
    src = (q.get("from") or [""])[0].strip().lower()
    return src if src in SOURCES else None
