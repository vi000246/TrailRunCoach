"""
本月訂閱收入 against the 免辦稅籍 line (SP-803; SP-321 拍板 2026-10-07: keep each month's sales under
NT$50,000 — 勞務's 營業稅起徵點, no tax registration needed from 2025 — and ask an accountant whether to
register before the month's income gets close to it).

From this app's own records only — nothing is asked of Polar, no key is read: the subscription rows the
webhook and the sync keep (accounts.db billing_subscriptions, backend/billing/service.py). The app keeps
no payment amounts, so the figure is an estimate on the list prices (billing.PRICES) of each row's
interval:

  charged      a subscription whose current period started this month (Asia/Taipei, the app's month)
               up to `now` was charged then: current_period_end minus one month / one year (in UTC,
               the inverse of Polar's own arithmetic; a period that began on the 29th–31st may land a
               day early)
  upcoming     an active one (or a trial) renewing later this month, not set to end: charged by then
  projected    charged + upcoming — what the month ends at if nothing changes; NEAR is on this one,
               so the owner hears it before the month crosses the line, not after

Both doubts lean towards a higher figure (an early nudge to ask the accountant costs nothing; a late one
can): a row revoked or past due after this month's charge still counts. Not seen here: a second charge
of one account in the same month (only its latest subscription is kept), refunds, Polar's actual
amounts, and inReachLiveTrack's sales through the same Polar organization (SP-321) — the line is the
owner's, the figure this app's.

A database that can't be read is "unknown" (None from rows()), never NT$0.
"""
from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from backend import billing as B
from backend.billing.entitlement import parse

TZ = dt.timezone(dt.timedelta(hours=8))          # the app's month (ops_report.TZ)
TAX_LINE_TWD = 50_000                            # 每月銷售未達 NT$50,000 免辦稅籍 (SP-321, 2026-10-07)
WARN_SHARE = 0.8                                 # 「接近」: 80 % of the line, like the member cap (SP-388)
CHARGED = ("active", "past_due", "canceled", "revoked")     # paid when the period began (not a trial)
RENEWING = ("active", "trialing")


def _now() -> dt.datetime:
    return dt.datetime.now(TZ)


def _minus(t: dt.datetime, interval: str) -> Optional[dt.datetime]:
    """One calendar month / year before `t` (the 31st → the month's last day; 29 Feb → 28 Feb)."""
    if interval == B.MONTH:
        y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    elif interval == B.YEAR:
        y, m = t.year - 1, t.month
    else:
        return None
    return t.replace(year=y, month=m, day=min(t.day, calendar.monthrange(y, m)[1]))


def month_of(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """[first instant, first instant of the next) of `now`'s month, Asia/Taipei."""
    local = now.astimezone(TZ)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, nxt


def estimate(rows: Iterable[dict], now: dt.datetime, prices: Optional[dict] = None) -> dict:
    """{month, charged, upcoming, projected, paid, renewing, line, share, near, over} in TWD (pure)."""
    prices = prices or B.PRICES
    start, end = month_of(now)
    charged = upcoming = paid = renewing = 0
    for r in rows:
        price = prices.get(r.get("interval"))
        period_end = parse(r.get("current_period_end"))
        if not price or period_end is None:
            continue
        began = _minus(period_end, r.get("interval"))
        if r.get("status") in CHARGED and began is not None and start <= began <= now:
            charged += price
            paid += 1
        if r.get("status") in RENEWING and not r.get("cancel_at_period_end") and now < period_end < end:
            upcoming += price
            renewing += 1
    projected = charged + upcoming
    share = projected / TAX_LINE_TWD
    return {"month": start.strftime("%Y-%m"), "charged": charged, "upcoming": upcoming, "projected": projected,
            "paid": paid, "renewing": renewing, "line": TAX_LINE_TWD, "share": round(share, 3),
            "near": share >= WARN_SHARE, "over": charged >= TAX_LINE_TWD}


def rows(db: Optional[Path]) -> Optional[list[dict]]:
    """The subscription rows of real accounts (never a test account's), accounts.db opened read-only;
    None when it can't be read (no file, no table, a locked or broken database)."""
    if db is None or not Path(db).is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cur = con.execute(
                "SELECT b.interval, b.status, b.current_period_end, b.cancel_at_period_end FROM billing_subscriptions b "
                "JOIN users u ON u.id = b.user_id WHERE COALESCE(u.is_test, 0) = 0")
            names = [c[0] for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return None


def signal(db: Optional[Path], now: Optional[dt.datetime] = None) -> dict:
    """estimate() with ok=True, or {ok: False, month} when the records can't be read (unknown, not 0)."""
    now = now or _now()
    rs = rows(db)
    if rs is None:
        return {"ok": False, "month": month_of(now)[0].strftime("%Y-%m"), "line": TAX_LINE_TWD}
    return {"ok": True, **estimate(rs, now)}
