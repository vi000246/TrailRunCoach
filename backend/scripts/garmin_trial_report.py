"""
The Garmin trial's report (SP-91 part D): reads the sync event log (logs/sync_events.jsonl and its
rotated .1, sync/events.py; source "garmin") and answers the questions the trial is for:

  1. 同步成功率與延遲 — runs by outcome, success rate, run time (median / p90 / max), the time
     between an account's successful syncs
  2. 斷線 — periods when Garmin syncs / logins failed for a reason that is not a block, a missing
     login or a limit (Garmin changed something; the fix is usually a new `garminconnect`), from
     the first failure after a success to the next success, with the package version before / after
  3. 被鎖的帳號 — accounts answered 429 / 403 vs the accounts that used Garmin, how long a lock
     lasted (account_recovered.locked_s), accounts still paused, breaker trips
  4. 同步頻率 — runs per active day per account (manual / automatic) and Garmin requests per day,
     locked accounts vs the others; per day: accounts active, runs, 429 / 403 (one NAS IP, many
     accounts: do more accounts a day mean more locks?)

    python -m backend.scripts.garmin_trial_report [--since 2026-10-12] [--json] [--log PATH]
    python -m backend.scripts.garmin_trial_report --switch off|on        # the owner's server switch

Nothing personal is read or printed: tenants appear by id (u<n>, owner), as the log holds them.
Read-only, except --switch (site.json, the same as PUT /api/v1/auth/garmin/site).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional

SOURCE = "garmin"
OK = ("sync_ok", "sync_partial")
# run-level failures that are no breakage of the Garmin interface: a block / pause / limit / the
# switch (the limits did their job), a login to renew (the user's part)
NOT_BREAKAGE = ("GARMIN_RATE_LIMITED", "GARMIN_PAUSED", "GARMIN_DAILY_LIMIT", "GARMIN_DISABLED",
                "GARMIN_UNAVAILABLE", "GARMIN_AUTH_REQUIRED", "SYNC_BUSY", "SYNC_DISABLED")


def read_events(paths: Iterable[Path], since: Optional[float] = None) -> list[dict]:
    out = []
    for p in paths:
        try:
            lines = Path(p).read_text("utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("source") == SOURCE and (since is None or float(r.get("at") or 0) >= since):
                out.append(r)
    out.sort(key=lambda r: float(r.get("at") or 0))
    return out


def _day(t: float) -> str:
    return dt.datetime.fromtimestamp(float(t)).date().isoformat()


def _pct(xs: list, q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(float(xs[i]), 1)


def _breakage(r: dict) -> bool:
    """A failure that looks like Garmin changed something (not a block, limit or login to renew)."""
    ev = r.get("event")
    if ev == "sync_failed":
        return str(r.get("error") or "") not in NOT_BREAKAGE
    if ev == "login_failed":
        return r.get("kind") == "error"
    return ev == "list_failed"


def report(events: list[dict], now: Optional[float] = None, paused_now: Optional[dict] = None) -> dict:
    now = now if now is not None else dt.datetime.now().timestamp()
    runs = [r for r in events if str(r.get("event", "")).startswith("sync_") and r.get("event") != "sync_skipped"]
    by_outcome = Counter(r["event"][5:] for r in runs)
    ok_runs = [r for r in runs if r["event"] in OK]
    secs = [float(r["secs"]) for r in runs if isinstance(r.get("secs"), (int, float))]

    # 1. success and latency; the gap between an account's successful syncs
    gaps = []
    last_ok: dict = {}
    for r in ok_runs:
        t, tid = float(r["at"]), r.get("tenant")
        if tid in last_ok:
            gaps.append((t - last_ok[tid]) / 3600.0)
        last_ok[tid] = t
    sync = {"runs": len(runs), "by_outcome": dict(by_outcome),
            "success_rate": round(len(ok_runs) / len(runs), 3) if runs else None,
            "run_s": {"median": _pct(secs, 0.5), "p90": _pct(secs, 0.9), "max": _pct(secs, 1.0)},
            "hours_between_ok_syncs": {"median": _pct(gaps, 0.5), "p90": _pct(gaps, 0.9)},
            "downloaded": sum(int(r.get("downloaded") or 0) for r in runs),
            "budget_used_runs": sum(1 for r in runs if r.get("more")),
            "skipped": dict(Counter(str(r.get("code")) for r in events if r.get("event") == "sync_skipped"))}

    # 2. breakage: from the first non-block failure after a success to the next success
    outages, cur = [], None
    for r in events:
        ev = r.get("event")
        if ev in OK or ev in ("login_ok", "relogin_ok"):
            if cur is not None:
                cur.update(end=float(r["at"]), fixed_by=r.get("lib"))
                cur["hours"] = round((cur["end"] - cur["start"]) / 3600.0, 1)
                outages.append(cur)
                cur = None
        elif _breakage(r):
            if cur is None:
                cur = {"start": float(r["at"]), "lib": r.get("lib"), "failures": 0,
                       "errors": Counter(), "accounts": set()}
            cur["failures"] += 1
            cur["errors"][str(r.get("error") or r.get("event"))] += 1
            cur["accounts"].add(r.get("tenant"))
    if cur is not None:
        cur.update(end=None, hours=round((now - cur["start"]) / 3600.0, 1), ongoing=True)
        outages.append(cur)
    for o in outages:
        o["from"] = dt.datetime.fromtimestamp(o["start"]).isoformat(timespec="minutes")
        o["to"] = dt.datetime.fromtimestamp(o["end"]).isoformat(timespec="minutes") if o.get("end") else None
        o["errors"] = dict(o["errors"])
        o["accounts"] = len(o["accounts"])
        o["package_updated"] = bool(o.get("fixed_by") and o.get("lib") and o["fixed_by"] != o["lib"])
    versions = []
    for r in events:
        v = r.get("lib")
        if v and (not versions or versions[-1]["version"] != v):
            versions.append({"version": v, "first_seen": _day(r["at"])})

    # 3. locks
    users = {r.get("tenant") for r in events if r.get("tenant") and r.get("event") in
             ("login_ok", "relogin_ok", "mfa_required") + tuple(f"sync_{x}" for x in ("ok", "partial", "failed", "aborted"))}
    blocked = [r for r in events if r.get("event") in ("http_429", "http_403")]
    locked_accounts = {r.get("tenant") for r in blocked}
    recovered = [int(r["locked_s"]) for r in events if r.get("event") == "account_recovered"
                 and isinstance(r.get("locked_s"), (int, float))]
    locks = {"users": len(users), "locked_accounts": len(locked_accounts),
             "share_locked": round(len(locked_accounts) / len(users), 3) if users else None,
             "http_429": sum(1 for r in blocked if r["event"] == "http_429"),
             "http_403": sum(1 for r in blocked if r["event"] == "http_403"),
             "at_login": sum(1 for r in blocked if r.get("login")),
             "lock_hours": {"n": len(recovered), "median": _pct([s / 3600.0 for s in recovered], 0.5),
                            "max": _pct([s / 3600.0 for s in recovered], 1.0)},
             "still_paused": sorted((paused_now or {}).keys()),
             "breaker_trips": [_day(r["at"]) for r in events if r.get("event") == "breaker_tripped"],
             "mfa_required": sum(1 for r in events if r.get("event") == "mfa_required"),
             "mfa_wrong": sum(1 for r in events if r.get("event") == "mfa_wrong"),
             "login_failed": dict(Counter(str(r.get("kind")) for r in events if r.get("event") == "login_failed")),
             "token_refused": sum(1 for r in events if r.get("event") == "token_refused"),
             "relogin_ok": sum(1 for r in events if r.get("event") == "relogin_ok")}

    # 4. frequency: runs per active day per account, locked vs not; per day across accounts
    per_acc: dict = defaultdict(lambda: {"runs": 0, "manual": 0, "days": set(), "requests": {}})
    for r in runs:
        a = per_acc[r.get("tenant")]
        a["runs"] += 1
        a["manual"] += 1 if r.get("trigger") in ("manual", "retry") else 0
        a["days"].add(_day(r["at"]))
        if isinstance(r.get("requests_today"), (int, float)):
            d = _day(r["at"])
            a["requests"][d] = max(a["requests"].get(d, 0), int(r["requests_today"]))

    def group(locked: bool) -> dict:
        accs = [v for k, v in per_acc.items() if (k in locked_accounts) == locked]
        rpd = [v["runs"] / len(v["days"]) for v in accs if v["days"]]
        req = [x for v in accs for x in v["requests"].values()]
        return {"accounts": len(accs), "runs_per_active_day": round(statistics.mean(rpd), 2) if rpd else None,
                "manual_share": round(sum(v["manual"] for v in accs) / max(1, sum(v["runs"] for v in accs)), 2),
                "requests_per_day": {"median": _pct(req, 0.5), "max": _pct(req, 1.0)}}
    days: dict = defaultdict(lambda: {"accounts": set(), "runs": 0, "blocked": 0})
    for r in runs:
        days[_day(r["at"])]["accounts"].add(r.get("tenant"))
        days[_day(r["at"])]["runs"] += 1
    for r in blocked:
        days[_day(r["at"])]["blocked"] += 1
    per_day = [{"day": d, "accounts": len(v["accounts"]), "runs": v["runs"], "blocked": v["blocked"]}
               for d, v in sorted(days.items())]
    freq = {"locked": group(True), "not_locked": group(False), "per_day": per_day}
    return {"from": _day(events[0]["at"]) if events else None, "to": _day(events[-1]["at"]) if events else None,
            "events": len(events), "sync": sync, "outages": outages, "versions": versions,
            "locks": locks, "frequency": freq}


def _fmt(x) -> str:
    return "–" if x is None else str(x)


def text(rep: dict) -> str:
    s, lk, fq = rep["sync"], rep["locks"], rep["frequency"]
    out = [f"Garmin 試運行紀錄 {_fmt(rep['from'])} ~ {_fmt(rep['to'])}（{rep['events']} 筆事件）", "",
           "1. 同步成功率與延遲",
           f"   同步 {s['runs']} 次：" + "、".join(f"{k} {v}" for k, v in sorted(s["by_outcome"].items()))
           + f"；成功率 {_fmt(s['success_rate'])}",
           f"   每次同步耗時（秒）：中位數 {_fmt(s['run_s']['median'])}、p90 {_fmt(s['run_s']['p90'])}、最長 {_fmt(s['run_s']['max'])}",
           f"   同一帳號兩次成功同步的間隔（小時）：中位數 {_fmt(s['hours_between_ok_syncs']['median'])}、"
           f"p90 {_fmt(s['hours_between_ok_syncs']['p90'])}",
           f"   下載 {s['downloaded']} 筆活動；{s['budget_used_runs']} 次用完當天下載量（隔天繼續）",
           "   沒開始的同步：" + ("、".join(f"{k} {v}" for k, v in sorted(s["skipped"].items())) or "無"), "",
           f"2. 斷線（不是被鎖、不是額度、不是要重新登入的失敗）：{len(rep['outages'])} 次"]
    for o in rep["outages"]:
        out.append(f"   {o['from']} ~ {_fmt(o['to'])}，{o['hours']} 小時，{o['failures']} 次失敗、{o['accounts']} 個帳號；"
                   f"套件 {_fmt(o.get('lib'))} → {_fmt(o.get('fixed_by'))}" + ("（更新套件後恢復）" if o["package_updated"] else "")
                   + ("（尚未恢復）" if o.get("ongoing") else ""))
    out.append("   garminconnect 版本：" + ("、".join(f"{v['version']}（{v['first_seen']} 起）" for v in rep["versions"]) or "–"))
    out += ["", "3. 被鎖的帳號",
            f"   用過 Garmin 的帳號 {lk['users']} 個，被 429／403 擋過 {lk['locked_accounts']} 個（{_fmt(lk['share_locked'])}）；"
            f"429 {lk['http_429']} 次、403 {lk['http_403']} 次（登入時 {lk['at_login']} 次）",
            f"   被鎖多久才恢復（小時）：{lk['lock_hours']['n']} 次，中位數 {_fmt(lk['lock_hours']['median'])}、最長 {_fmt(lk['lock_hours']['max'])}",
            "   目前暫停中：" + ("、".join(lk["still_paused"]) or "無"),
            "   熔斷：" + ("、".join(lk["breaker_trips"]) or "沒有觸發"),
            f"   MFA：要求 {lk['mfa_required']} 次、打錯 {lk['mfa_wrong']} 次；登入失敗：" +
            ("、".join(f"{k} {v}" for k, v in lk["login_failed"].items()) or "0") +
            f"；憑證失效 {lk['token_refused']} 次、自動重登成功 {lk['relogin_ok']} 次", "",
            "4. 同步頻率和被鎖的關係"]
    for name, g in (("被鎖過的帳號", fq["locked"]), ("沒被鎖的帳號", fq["not_locked"])):
        out.append(f"   {name} {g['accounts']} 個：每個活躍日平均同步 {_fmt(g['runs_per_active_day'])} 次、手動占 "
                   f"{_fmt(g['manual_share'])}、每天 Garmin 請求數中位數 {_fmt(g['requests_per_day']['median'])}"
                   f"（最多 {_fmt(g['requests_per_day']['max'])}）")
    out.append("   每天（同一個 IP）：日期 帳號數 同步次數 429/403")
    for d in fq["per_day"][-60:]:
        out.append(f"   {d['day']} {d['accounts']} {d['runs']} {d['blocked']}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Garmin 試運行報表（SP-91）")
    ap.add_argument("--since", help="YYYY-MM-DD: only events from this day")
    ap.add_argument("--log", help="the event log (default: <server root>/logs/sync_events.jsonl)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--switch", choices=("on", "off"), help="the server-wide Garmin switch (the owner)")
    a = ap.parse_args(argv)
    from backend.sync import garmin_limits as GL
    if a.switch:
        st = GL.set_enabled(a.switch == "on", by="owner (cli)")
        print(f"Garmin 全站開關：{'開' if st['enabled'] else '關'}（{st['state']}）")
        return 0
    if a.log:
        base = Path(a.log)
    else:
        from backend.sync import events
        base = events.path()
    since = dt.datetime.fromisoformat(a.since).timestamp() if a.since else None
    evs = read_events([base.with_name(base.name + ".1"), base], since)
    from backend import site_settings as SS
    rep = report(evs, paused_now=SS.get("garmin.paused") or {})
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str) if a.json else text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
