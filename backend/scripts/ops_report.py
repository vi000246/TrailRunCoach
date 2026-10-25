"""
每日營運報表 (SP-388 完成條件 2、3): what the server spent on whom, from the logs it already writes —
nothing is measured anew, nothing is sent anywhere.

  * every job of the server's queue (backend/jobs.py) writes one perf.log line with its tenant, its
    class, its CPU seconds and how long it waited (`job` lines, SP-394 第 6 條); perf.log rotates daily
    and keeps PERF_KEEP_DAYS (31) days;
  * COROS / Garmin pushing back goes to sync_events.jsonl (sync/events.py, SP-385 / SP-91 D): 429 / 403,
    pauses, the breaker, the server cooldown, and when an account recovered.

The report, per day (Asia/Taipei, the app's day):
  1. 每人每天 CPU 秒 — per tenant (u<id> / owner), all jobs;
  2. 免費層超標 — the month so far per tenant on the free plan against FREE_CPU_S_PER_MONTH (每月 CPU
     預算 10 分，docs/research/membership-cost-tiers.md §5; the NAS's own CPU seconds), the ones over it
     and the ones past WARN_SHARE of it;
  3. 回測排隊 — the `backtest` jobs: how many, their wait (median, longest);
  4. 非官方來源被擋 — COROS / Garmin: 429 / 403 / pauses / breaker / cooldown counts and, for each
     account paused that day, how long until it recovered;
  5. 會員數 (SP-388 完成條件 4) — the members against the sign-up cap (accounts/login.max_users: the
     admin page's members.max_users, else WKO5COACH_MAX_USERS, else 100), flagged from MEMBERS_WARN_SHARE
     (80 %): the owner's cue to raise the cap or move the server before sign-up starts refusing people.

    python -m backend.scripts.ops_report                      # yesterday, as text (markdown)
    python -m backend.scripts.ops_report --date 2026-10-18 --json
    python -m backend.scripts.ops_report --days 7             # the last 7 days, one block each
    python -m backend.scripts.ops_report --logs <folder> --events <sync_events.jsonl> --accounts-db <accounts.db>

Read-only: the log files and, for the plans, accounts.db (opened read-only; without it every tenant
counts as free). Run it on the NAS (docker exec trailruncoach python -m backend.scripts.ops_report),
where the logs are.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

TZ = dt.timezone(dt.timedelta(hours=8))            # the app's day (jobs._account)
FREE_CPU_S_PER_MONTH = 600.0                       # 每月 CPU 預算 10 分 (membership-cost-tiers.md §5, 推估)
WARN_SHARE = 0.8                                   # 「接近」: 80 % of the budget
BLOCK_EVENTS = ("http_429", "http_403", "account_paused", "breaker_tripped", "server_cooldown", "disabled")
RECOVERED = ("account_resumed", "account_recovered")
MEMBERS_WARN_SHARE = 0.8                           # 會員數到封頂的 80 % (SP-388 完成條件 4)

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})[^\t]*\tjob\t([\d.]+)\t(.*)$")
_FIELD = re.compile(r" (\w+)=(\S+)")


# ---------------------------------------------------------------------------- reading

def perf_files(folder: Path) -> list[Path]:
    """perf.log and its daily rotations (perf.log.YYYY-MM-DD), oldest first."""
    files = sorted(p for p in folder.glob("perf.log.*") if p.is_file())
    cur = folder / "perf.log"
    return files + ([cur] if cur.is_file() else [])


def jobs(files: Iterable[Path]) -> list[dict]:
    """The `job` lines: {day, label, seconds, tenant, cls, cpu, wait, status}. The perf.log time is the
    server's local time — the NAS container runs on Asia/Taipei like the app's day."""
    out = []
    for f in files:
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            rest = m.group(4)
            fields = dict(_FIELD.findall(" " + rest))
            label = _FIELD.split(" " + rest)[0].strip()
            try:
                cpu = float(fields.get("cpu", 0) or 0)
                wait = float(fields.get("wait", 0) or 0)
            except ValueError:
                continue
            out.append({"day": m.group(1), "label": label, "seconds": float(m.group(3)),
                        "tenant": fields.get("tenant") or "?", "cls": fields.get("cls") or "?",
                        "cpu": cpu, "wait": wait, "status": fields.get("status")})
    return out


def events(path: Optional[Path]) -> list[dict]:
    """sync_events.jsonl (and its .1), each with its local day."""
    out = []
    if path is None:
        return out
    for p in (path.with_name(path.name + ".1"), path):
        try:
            text = p.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or not isinstance(rec.get("at"), (int, float)):
                continue
            rec["day"] = dt.datetime.fromtimestamp(rec["at"], TZ).date().isoformat()
            out.append(rec)
    out.sort(key=lambda r: r["at"])
    return out


def plans(db: Optional[Path], today: dt.date) -> dict[str, str]:
    """tenant id (u<id>) → "paid" / "free" / "test" from accounts.db (read-only); {} without it."""
    if db is None or not db.is_file():
        return {}
    out = {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for uid, plan, exp, test in con.execute("SELECT id, plan, plan_expires_at, is_test FROM users"):
                paid = (plan or "free") != "free" and (not exp or str(exp)[:10] >= today.isoformat())
                out[f"u{uid}"] = "test" if test else "paid" if paid else "free"
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return out


def member_count(db: Optional[Path]) -> Optional[int]:
    """The member cap's count from accounts.db, read-only (as accounts/store.count_users: every
    account but the test ones); None without it."""
    if db is None or not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return int(con.execute("SELECT count(*) FROM users WHERE is_test = 0").fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        return None


def member_signal(count: Optional[int], cap: Optional[int]) -> Optional[dict]:
    """{count, cap, share, near, full}: near = at MEMBERS_WARN_SHARE of the cap or past it; None when
    either is unknown (owner mode: no accounts)."""
    if count is None or not cap:
        return None
    share = count / cap
    return {"count": count, "cap": cap, "share": round(share, 3), "near": share >= MEMBERS_WARN_SHARE,
            "full": count >= cap}


# ---------------------------------------------------------------------------- the report

def report(day: dt.date, js: list[dict], evs: list[dict], plan_of: dict[str, str],
           members: Optional[dict] = None) -> dict:
    d = day.isoformat()
    month = d[:7]
    cpu_day: dict = defaultdict(float)
    n_day: dict = defaultdict(int)
    cpu_month: dict = defaultdict(float)
    for j in js:
        if j["day"] == d:
            cpu_day[j["tenant"]] += j["cpu"]
            n_day[j["tenant"]] += 1
        if j["day"][:7] == month and j["day"] <= d:
            cpu_month[j["tenant"]] += j["cpu"]
    per_tenant = [{"tenant": t, "cpu_s": round(cpu_day[t], 1), "jobs": n_day[t], "plan": plan_of.get(t, _guess(t))}
                  for t in sorted(cpu_day, key=lambda t: -cpu_day[t])]
    free = []
    for t, s in sorted(cpu_month.items(), key=lambda kv: -kv[1]):
        if plan_of.get(t, _guess(t)) != "free":
            continue
        share = s / FREE_CPU_S_PER_MONTH
        if share >= WARN_SHARE:
            free.append({"tenant": t, "cpu_s_month": round(s, 1), "share": round(share, 2), "over": share >= 1.0})
    waits = [j["wait"] for j in js if j["day"] == d and j["cls"] == "backtest"]
    backtest = {"jobs": len(waits),
                "wait_median_s": round(statistics.median(waits), 1) if waits else None,
                "wait_max_s": round(max(waits), 1) if waits else None}
    sources = {}
    for src in ("coros", "garmin"):
        mine = [e for e in evs if e.get("source") == src]
        today_evs = [e for e in mine if e["day"] == d]
        counts = {k: sum(1 for e in today_evs if e.get("event") == k) for k in BLOCK_EVENTS}
        recov = []
        for e in today_evs:
            if e.get("event") != "account_paused":
                continue
            back = next((x for x in mine if x["at"] > e["at"] and x.get("event") in RECOVERED
                         and x.get("tenant") == e.get("tenant")), None)
            recov.append({"tenant": e.get("tenant"), "reason": e.get("reason"),
                          "recovered_after_h": round((back["at"] - e["at"]) / 3600.0, 1) if back else None})
        sources[src] = {"counts": {k: v for k, v in counts.items() if v}, "paused": recov}
    return {"day": d, "cpu_total_s": round(sum(cpu_day.values()), 1), "tenants": per_tenant,
            "free_over_budget": free, "free_budget_s": FREE_CPU_S_PER_MONTH, "backtest": backtest,
            "sources": sources, "members": members}


def _guess(tenant: str) -> str:
    """Without accounts.db: the owner is the owner, everyone else counts as free."""
    return "owner" if tenant == "owner" else "free"


def text(r: dict) -> str:
    lines = [f"## 營運報表 {r['day']}", "", f"CPU 合計 {r['cpu_total_s']:.0f} 秒（NAS）", "",
             "### 每人每天 CPU 秒", ""]
    if r["tenants"]:
        lines += ["| 租戶 | 方案 | CPU 秒 | 工作數 |", "|---|---|---:|---:|"]
        lines += [f"| {t['tenant']} | {t['plan']} | {t['cpu_s']:.1f} | {t['jobs']} |" for t in r["tenants"]]
    else:
        lines.append("（這天沒有工作紀錄）")
    lines += ["", f"### 免費層超標（每月 {r['free_budget_s'] / 60:.0f} CPU 分，{WARN_SHARE:.0%} 起列出）", ""]
    if r["free_over_budget"]:
        lines += [f"- {f['tenant']}：本月 {f['cpu_s_month'] / 60:.1f} 分（{f['share']:.0%}）" + ("，**超過**" if f["over"] else "")
                  for f in r["free_over_budget"]]
    else:
        lines.append("沒有")
    b = r["backtest"]
    lines += ["", "### 回測排隊", ""]
    lines.append(f"{b['jobs']} 次；等待中位數 {b['wait_median_s']} 秒、最久 {b['wait_max_s']} 秒" if b["jobs"] else "這天沒有回測")
    lines += ["", "### 非官方來源被擋", ""]
    for src, label in (("coros", "COROS"), ("garmin", "Garmin")):
        s = r["sources"][src]
        c = "、".join(f"{k} {v} 次" for k, v in s["counts"].items()) or "沒有"
        lines.append(f"- {label}：{c}")
        for p in s["paused"]:
            back = f"{p['recovered_after_h']} 小時後恢復" if p["recovered_after_h"] is not None else "還沒恢復"
            lines.append(f"  - {p['tenant']} 暫停（{p['reason'] or '—'}），{back}")
    m = r.get("members")
    if m:
        lines += ["", f"### 會員數（封頂的 {MEMBERS_WARN_SHARE:.0%} 起提醒）", ""]
        line = f"{m['count']} / {m['cap']} 人（{m['share']:.0%}）"
        if m["full"]:
            line += "，**已滿**：新註冊會進候補名單"
        elif m["near"]:
            line += "，**接近封頂**：考慮調高上限（管理 › 全站設定 › 會員封頂）或升級主機"
        lines.append(line)
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.scripts.ops_report", description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday, Asia/Taipei)")
    ap.add_argument("--days", type=int, default=1, help="this many days up to --date (default 1)")
    ap.add_argument("--logs", help="the folder of perf.log (default: the app's log folder)")
    ap.add_argument("--events", help="sync_events.jsonl (default: the server's)")
    ap.add_argument("--accounts-db", help="accounts.db for the plans (default: the server's)")
    ap.add_argument("--json", action="store_true", help="JSON instead of text")
    a = ap.parse_args(argv)
    today = dt.datetime.now(TZ).date()
    last = dt.date.fromisoformat(a.date) if a.date else today - dt.timedelta(days=1)
    if a.logs:
        folder = Path(a.logs)
    else:
        from backend import applog
        pf = applog.perf_file()
        if pf is None:
            print("perf.log is off (WKO5COACH_PERF_LOG=0 or no log file)", file=sys.stderr)
            return 1
        folder = pf.parent
    if a.events:
        ev_path: Optional[Path] = Path(a.events)
    else:
        from backend.sync import events as EV
        ev_path = EV.path()
    if a.accounts_db:
        db: Optional[Path] = Path(a.accounts_db)
    else:
        from backend import accounts
        db = accounts.accounts_db_path()
    js, evs, plan_of = jobs(perf_files(folder)), events(ev_path), plans(db, last)
    count = member_count(db)
    members = member_signal(count, cap() if count is not None else None)
    out = [report(last - dt.timedelta(days=i), js, evs, plan_of, members) for i in range(max(1, a.days) - 1, -1, -1)]
    if a.json:
        print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=1))
    else:
        print("\n".join(text(r) for r in out))
    return 0


def cap() -> Optional[int]:
    """The sign-up cap (accounts/login.max_users); None when it cannot be read."""
    try:
        from backend.accounts import login
        return login.max_users()
    except Exception:                                  # noqa: BLE001 — the report goes on without it
        return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
