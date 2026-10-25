"""
互推漏斗與留存 (SP-765; research SP-571 §4, docs/research/sp571-cross-promotion.md): what the 疊石 Cairn
card did, month by month, from accounts.db alone (xp_events, users.signup_source, sessions) — read-only.

Per month (the app's day, Asia/Taipei; a person counts in the month of their first event of that kind):
  1. 漏斗 — people who saw the card (xp-card-shown), pressed 「打開疊石 Cairn」 (xp-card-click), pressed
     「不用了」 (xp-card-dismiss), linked the account with Cairn (xp-linked); the times the card was shown;
     the click rate (pressed ÷ saw) and the link rate (linked ÷ saw)
  2. 新帳號從哪來 — accounts made that month by signup_source: 'cairn' (a 疊石 Cairn link) and 'share'
     (a race share page's 「用自己的資料算」, SP-389 Q1-2)
  3. 30 天後還在用 — for each group, how many were seen again 30 days or more after they joined it:
       linked     the people who linked with Cairn (from their first xp-linked)
       from_cairn the accounts that came from Cairn (from created_at)
       from_share the accounts that came from a share page (from created_at)
       control    saw the card but never pressed it nor linked (from their first xp-card-shown) — the
                  comparison: the same moment, without the step (SP-571 §4.3: people who link are the
                  keen ones anyway, so never compare with everyone)
     "Seen" = the last sign-in (users.last_login_at) or the last request of any of their sessions
     (sessions.last_seen_at), whichever is later (推估: a session ended long ago is gone from the
     table, so this undercounts rather than overcounts). A group whose 30 days are not over yet shows
     how many are still waiting.
The owner, test accounts and disabled accounts are left out.

    python -m backend.scripts.xp_funnel                          # every month, as text (markdown)
    python -m backend.scripts.xp_funnel --month 2026-11 --json
    python -m backend.scripts.xp_funnel --accounts-db <accounts.db> --today 2026-12-31

Run it on the NAS (docker exec trailruncoach python -m backend.scripts.xp_funnel), where accounts.db is —
or open 管理 › 營運報表: the same report, computed when the tab opens (GET /api/v1/admin/xp-funnel, SP-877).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

TZ = dt.timezone(dt.timedelta(hours=8))            # the app's day (accounts/xp.py)
RETAIN_DAYS = 30
SHOWN, CLICK, DISMISS, LINKED = "xp-card-shown", "xp-card-click", "xp-card-dismiss", "xp-linked"


def _day(iso: Optional[str]) -> Optional[dt.date]:
    """A stored time (ISO, UTC) or day (YYYY-MM-DD) as the app's day."""
    if not iso:
        return None
    try:
        if len(iso) == 10:
            return dt.date.fromisoformat(iso)
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t.astimezone(TZ).date()
    except ValueError:
        return None


def read(db: Path) -> dict:
    """The rows the report needs (one read-only connection)."""
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
        src = "signup_source" if "signup_source" in cols else "NULL AS signup_source"
        test = "is_test" if "is_test" in cols else "0 AS is_test"
        users = {r["id"]: dict(r) for r in con.execute(
            f"SELECT id, role, created_at, last_login_at, disabled_at, {src}, {test} FROM users")}
        seen = {r[0]: r[1] for r in con.execute("SELECT user_id, max(last_seen_at) FROM sessions GROUP BY user_id")} \
            if "sessions" in tables else {}
        events = [dict(r) for r in con.execute("SELECT user_id, event, day, n FROM xp_events")] \
            if "xp_events" in tables else []
    finally:
        con.close()
    return {"users": users, "seen": seen, "events": events}


def _counted(u: dict) -> bool:
    return u["id"] != 1 and u.get("role") != "owner" and not u.get("is_test") and not u.get("disabled_at")


def _last_seen(u: dict, seen: dict) -> Optional[dt.date]:
    days = [d for d in (_day(u.get("last_login_at")), _day(seen.get(u["id"]))) if d]
    return max(days) if days else None


def _retention(start: dict, users: dict, seen: dict, today: dt.date) -> dict:
    """{n, kept, waiting} for people → the day they joined the group."""
    out = {"n": len(start), "kept": 0, "waiting": 0}
    for uid, d0 in start.items():
        due = d0 + dt.timedelta(days=RETAIN_DAYS)
        last = _last_seen(users[uid], seen)
        if last and last >= due:
            out["kept"] += 1
        elif today < due:
            out["waiting"] += 1
    return out


def report(data: dict, today: Optional[dt.date] = None, month: Optional[str] = None) -> list[dict]:
    """One dict per month (oldest first), or the one `month` (YYYY-MM)."""
    today = today or dt.datetime.now(TZ).date()
    users = {k: v for k, v in data["users"].items() if _counted(v)}
    seen = data["seen"]
    first: dict = defaultdict(dict)                 # event -> uid -> first day
    shows: dict = defaultdict(int)                  # month -> times shown
    for e in data["events"]:
        if e["user_id"] not in users:
            continue
        d = _day(e["day"])
        if d is None:
            continue
        f = first[e["event"]]
        if e["user_id"] not in f or d < f[e["user_id"]]:
            f[e["user_id"]] = d
        if e["event"] == SHOWN:
            shows[d.strftime("%Y-%m")] += int(e["n"] or 0)
    def came_from(src: str) -> dict:
        return {uid: _day(u["created_at"]) for uid, u in users.items()
                if u.get("signup_source") == src and _day(u["created_at"])}
    from_cairn, from_share = came_from("cairn"), came_from("share")
    acted = set(first[CLICK]) | set(first[LINKED])
    control = {uid: d for uid, d in first[SHOWN].items() if uid not in acted}
    months = sorted({d.strftime("%Y-%m") for f in first.values() for d in f.values()}
                    | {d.strftime("%Y-%m") for d in (*from_cairn.values(), *from_share.values())})
    if month:
        months = [m for m in months if m == month] or [month]
    out = []
    for m in months:
        def of(group: dict) -> dict:
            return {uid: d for uid, d in group.items() if d.strftime("%Y-%m") == m}
        saw, clicked, dismissed, linked = (of(first[k]) for k in (SHOWN, CLICK, DISMISS, LINKED))
        rate = (lambda a, b: round(len(a) / len(b), 3) if b else None)
        out.append({
            "month": m,
            "funnel": {"saw": len(saw), "shown_times": shows.get(m, 0), "clicked": len(clicked),
                       "dismissed": len(dismissed), "linked": len(linked),
                       "click_rate": rate(clicked, saw), "link_rate": rate(linked, saw)},
            "from_cairn": len(of(from_cairn)),
            "from_share": len(of(from_share)),
            "retained_30d": {"linked": _retention(linked, users, seen, today),
                             "from_cairn": _retention(of(from_cairn), users, seen, today),
                             "from_share": _retention(of(from_share), users, seen, today),
                             "control": _retention(of(control), users, seen, today)},
        })
    return out


def _pct(a: int, b: int) -> str:
    return f"{a / b:.0%}" if b else "—"


def text(rows: list[dict]) -> str:
    lines = ["# 互推漏斗（疊石 Cairn 小卡）", ""]
    if not rows:
        return "\n".join(lines + ["還沒有任何紀錄。"]) + "\n"
    for r in rows:
        f = r["funnel"]
        lines += [f"## {r['month']}", "",
                  f"- 看到小卡 {f['saw']} 人（出現 {f['shown_times']} 次）→ 按「打開疊石 Cairn」{f['clicked']} 人"
                  f"（{_pct(f['clicked'], f['saw'])}）→ 連結 Cairn {f['linked']} 人（{_pct(f['linked'], f['saw'])}）；"
                  f"按「不用了」{f['dismissed']} 人",
                  f"- 新帳號：從 Cairn 來 {r['from_cairn']} 個、從賽事分享頁來 {r['from_share']} 個",
                  "- 30 天後還在用："]
        for k, name in (("linked", "連結 Cairn 的人"), ("from_cairn", "從 Cairn 來的新帳號"),
                        ("from_share", "從賽事分享頁來的新帳號"), ("control", "看到小卡但沒按的人（對照）")):
            g = r["retained_30d"][k]
            done = g["n"] - g["waiting"]
            lines.append(f"  - {name}：{g['kept']} / {done}（{_pct(g['kept'], done)}）"
                         + (f"，還有 {g['waiting']} 人未滿 30 天" if g["waiting"] else ""))
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--accounts-db", type=Path, default=None, help="accounts.db (default: the server's)")
    ap.add_argument("--month", default=None, help="one month, YYYY-MM")
    ap.add_argument("--today", default=None, help="count the 30 days up to this day (YYYY-MM-DD)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    db = a.accounts_db
    if db is None:
        from backend import accounts
        db = accounts.accounts_db_path()
    if not Path(db).exists():
        print(f"accounts.db not found: {db}", file=sys.stderr)
        return 2
    rows = report(read(db), dt.date.fromisoformat(a.today) if a.today else None, a.month)
    print(json.dumps(rows, ensure_ascii=False, indent=1) if a.json else text(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
