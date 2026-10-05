"""
多日百岳的賽事評估 (SP-114): a multi-day 百岳 trip is mountaineering, not a trail race — the race check
(engine/race_feasibility.py) judges it on the 攻頂日模擬 instead of the weekly volume.
docs/research/baiyue-mountaineering-training.md §1.2, §4.1, §4.2; the owner's decisions 2026-10-05.

Only a 百岳 of ≥ 2 days (applies). A 單攻百岳 is entered as a trail race and stays one; a multi-day
trail race keeps the trail rules (B2B included).

  攻頂日      the trip's day with the most climb (a 3-day 大小霸 ↑3500 m simulates its hardest DAY, ~1000 m,
              not the trip) — from the event's day plan / the GPX's day ends (race_refs.course_of).
  攻頂日模擬  one day climbing ≥ the summit day's climb with a pack (the activity's pack,
              racepower_hike_meta.json via athlete.activity_pack) ≥ SIM_PACK_SHARE of the trip's pack.
              UA〈Training for Mountaineering〉: from 8 weeks out, at least one a week, the summit day's
              whole climb, a pack of about the trip's weight. SIM_NEED = 3 is the owner's number; the 80 %
              pack and the last one ≥ 10 days out are 推估 (research §3.2, §4.2).

  可行性 (feasibility)  the weekly-volume check (UA Big Vert, a trail-race source) is dropped; instead:
              will there be time for SIM_NEED simulations? The longest single-day climb of the last 8
              weeks grows ≤ +10 % a week (SP-89's step; recovery weeks hold and carry no simulation) and
              every week inside [trip − 8 weeks, trip − SIM_LAST_DAYS] that reaches the summit climb is one
              more, plus the ones already done. ok ≥ SIM_NEED, tight ≥ 1, over = none: a lower-grade
              route, one more day, or later (research §3.3 — not 「降組別」).
  完備程度 (readiness)  the コース定数 long day and the biggest week are replaced by the simulations
              done since trip − 8 weeks: ok ≥ SIM_NEED, tight 1–2, short 0; climbs high enough without a
              recorded pack are named, so the user can record it.

The late (< 3 weeks), turnaround (撤退時間) and B2B checks stay as race_feasibility makes them.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import N_, _

SIM_NEED = 3                 # owner 2026-10-05: 3 summit-day simulations are enough
SIM_WEEKS = 8                # UA: from 8 weeks before the trip
SIM_LAST_DAYS = 10           # 推估 (research §3.2): the last simulation ≥ 10 days before the trip (taper 7–10)
SIM_PACK_SHARE = 0.8         # 推估 (research §4.2): a pack ≥ 80 % of the trip's counts
SIM_STEP = 1.10              # SP-89: ≤ +10 % a week
DROP_FEAS = ("weekly", "hours")          # the weekly volume (UA Big Vert) and Koop's ultra hours: trail sources
DROP_READY = ("long", "weekly", "hours")  # the コース定数 long day, the biggest week, Koop
SRC_SUMMIT = N_("攻頂日模擬：Uphill Athlete〈Training for Mountaineering〉——行程前 8 週起每週一次，一天爬完攻頂日（爬升最多的那天）"
                "的爬升，背和行程差不多重的背包；做到 3 次算夠；背包 ≥ 行程背包 80%、最後一次在行程前 10 天以前、"
                "爬升每週最多 +10% 是推估。多日百岳不看每週的距離和爬升（那是越野跑的規則）")


def applies(e) -> bool:
    """A 百岳 of ≥ 2 days (a 單攻百岳 is entered as a trail race)."""
    return getattr(e, "kind", None) == "baiyue" and int(getattr(e, "days", 1) or 1) >= 2


def summit_day(line: dict) -> Optional[dict]:
    """The race line's day with the most climb: {day, km, climb_m, hours}."""
    days = line.get("per_day") or []
    if not days:
        return None
    d = max(days, key=lambda x: x["climb_m"])
    return {"day": d["day"], "km": float(d["km"]), "climb_m": float(d["climb_m"]), "hours": float(d["hours"]),
            "cc": float(d["cc"])}


def sim_rows(ds, today: dt.date, days: int = SIM_WEEKS * 7) -> list[dict]:
    """The foot activities of the last `days` with their climb and the pack recorded for them
    (athlete.activity_pack; None = not recorded): {date, climb_m, km, hours, pack_kg}."""
    if ds is None:
        return []
    from backend.engine import overview as O
    from backend.engine.racepower import athlete as A
    try:
        meta = A.hike_meta()
    except Exception:                       # noqa: BLE001 — no file: no packs
        meta = {}
    out = []
    for w in O.workouts_between(ds, today - dt.timedelta(days=days), today + dt.timedelta(days=1)):
        if O.category(w) not in O.FOOT:
            continue
        try:
            pack = A.activity_pack(w, meta)["pack_kg"]
        except Exception:                   # noqa: BLE001
            pack = None
        out.append({"date": O.wdate(w), "climb_m": O._n(w.metrics.get("climbing")) or 0.0,
                    "km": O._n(w.metrics.get("distance")) or 0.0, "hours": O.moving_s(w) / 3600.0, "pack_kg": pack})
    return out


def sims_done(rows: list[dict], e, need_m: float, today: dt.date) -> dict:
    """The simulations done since trip − SIM_WEEKS: {"done": [dates], "no_pack": [dates] (climb enough,
    no pack recorded or too light), "since", "pack_min"}."""
    since = e.start - dt.timedelta(weeks=SIM_WEEKS)
    pack_min = SIM_PACK_SHARE * float(e.pack)
    done, no_pack = [], []
    for r in rows:
        if not (since <= r["date"] <= today) or r["climb_m"] < need_m:
            continue
        (done if (r.get("pack_kg") or 0.0) >= pack_min else no_pack).append(r["date"])
    return {"done": sorted(done), "no_pack": sorted(no_pack), "since": since, "pack_min": pack_min}


def projected(best_m: float, need_m: float, today: dt.date, start: dt.date) -> dict:
    """The simulations still possible: from next week the longest climb grows ≤ +10 % a week (a
    recovery week every 4th holds and has none); a week inside [start − SIM_WEEKS, start −
    SIM_LAST_DAYS] (its Saturday) whose climb reaches `need_m` is one. {"n", "first", "peak_m"}."""
    from backend.engine import race_feasibility as F
    lo = start - dt.timedelta(weeks=SIM_WEEKS)
    hi = start - dt.timedelta(days=SIM_LAST_DAYS)
    climb, n, first = float(best_m), 0, None
    for w in F.weeks_ahead(today, start):
        if w["recovery"]:
            continue
        climb *= SIM_STEP
        sat = w["monday"] + dt.timedelta(days=5)
        if lo <= sat <= hi and climb >= need_m:
            n += 1
            first = first or sat
    return {"n": n, "first": first, "peak_m": climb}


def _level(checks: list, order: tuple) -> str:
    lv = "unknown"
    for c in checks:
        x = c["level"]
        if x == "unknown":
            continue
        lv = x if lv == "unknown" or order.index(x) > order.index(lv) else lv
    return lv


def feasibility(r: dict, e, line: Optional[dict], today: dt.date, rows: list[dict]) -> None:
    """The race check `r` (race_feasibility.assess) of a multi-day 百岳, in place: the weekly checks
    out, the summit-day simulation in; the late / turnaround checks and their advice kept."""
    from backend.engine import race_feasibility as F
    late_sugg = {_("建議改成 B 或 C 賽，用現在的體能去跑就好，不要臨時猛加量"), _("用現在的體能去跑就好，不要臨時猛加量")}
    cut_sugg = _("預估還沒到山頂就得撤退：這座百岳可能還不適合現在的你。可以換短一點的路線、多排一天，或先不去")
    keep = late_sugg | {cut_sugg}
    r["checks"] = [c for c in r["checks"] if c["id"] not in DROP_FEAS]
    r["suggestions"] = [s for s in r["suggestions"] if s in keep]
    for k in ("downgrade", "koop"):
        r.pop(k, None)
    r["src"] = [_(SRC_SUMMIT)]
    sd = summit_day(line) if line else None
    if sd is None:
        r["checks"].append({"id": "summit_sim", "level": "unknown", "label": _(F.LEVEL_LABEL["unknown"]),
                            "text": _("沒有每天的距離和爬升，找不到攻頂日")})
    else:
        r["race_day"] = sd
        best = max((x["climb_m"] for x in rows), default=0.0)
        if best <= 0:
            lv, txt, kw = "unknown", _("最近 8 週沒有跑步或健行紀錄，沒辦法推算"), {}
        else:
            got = sims_done(rows, e, sd["climb_m"], today)
            pj = projected(best, sd["climb_m"], today, e.start)
            total = len(got["done"]) + pj["n"]
            lv = "ok" if total >= SIM_NEED else "tight" if total >= 1 else "over"
            txt = _("攻頂日是第 {d} 天，爬升 {need:.0f} m。你最近 8 週一天最多爬 {best:.0f} m；照每週最多 +10% 慢慢加，"
                    "行程前還能做 {n} 次攻頂日模擬（一天爬完攻頂日的爬升、背行程的背包），要 {k} 次",
                    d=sd["day"], need=sd["climb_m"], best=best, n=pj["n"], k=SIM_NEED)
            if got["done"]:
                txt += _("；已經做了 {n} 次", n=len(got["done"]))
            kw = {"projected": pj["n"], "done": len(got["done"]), "need": SIM_NEED, "need_m": round(sd["climb_m"]),
                  "best_m": round(best)}
            if lv == "over":
                r["suggestions"].insert(0, _("來不及練到攻頂日的爬升：可以換短一點、低一級的路線，多排一天把攻頂日拆短，或延後出發"))
            elif lv == "tight" and r.get("days_to", 0) >= F.WINDOW_DAYS:
                r["suggestions"].append(_("每週排一次攻頂日模擬：背行程的背包，爬升一次比一次多（每週最多 +10%）"))
        r["checks"].append({"id": "summit_sim", "level": lv, "label": _(F.LEVEL_LABEL[lv]), "text": txt, **kw})
    r["level"] = _level(r["checks"], F.LEVELS)
    r["label"] = _(F.LEVEL_LABEL[r["level"]])


def readiness(rd: dict, e, line: Optional[dict], today: dt.date, rows: list[dict]) -> None:
    """賽事完備程度 `rd` (race_feasibility.readiness) of a multi-day 百岳, in place: the long day and
    the weekly checks out, 「攻頂日模擬做了幾次」 in; B2B kept."""
    from backend.engine import race_feasibility as F
    rd["checks"] = [c for c in rd["checks"] if c["id"] not in DROP_READY]
    rd["src"] = [_(SRC_SUMMIT)]
    sd = summit_day(line) if line else None
    if sd is None:
        rd["checks"].insert(0, {"id": "summit_sim", "level": "unknown", "label": _(F.READY_LABEL["unknown"]),
                                "text": _("沒有每天的距離和爬升，找不到攻頂日")})
    else:
        got = sims_done(rows, e, sd["climb_m"], today)
        n = len(got["done"])
        lv = "ok" if n >= SIM_NEED else "tight" if n >= 1 else "short"
        txt = _("行程前 8 週內做了 {n} 次攻頂日模擬（一天爬升 ≥ {need:.0f} m、背包 ≥ {kg:.1f} kg），{k} 次算夠",
                n=n, need=sd["climb_m"], kg=got["pack_min"], k=SIM_NEED)
        if got["no_pack"]:
            txt += _("；另外 {m} 次爬升夠，但沒有記背包或太輕：在活動頁記下那次背多少就會算進來", m=len(got["no_pack"]))
        if today < got["since"]:
            txt += _("（從 {d} 開始算）", d=got["since"].isoformat())
        rd["checks"].insert(0, {"id": "summit_sim", "level": lv, "label": _(F.READY_LABEL[lv]), "text": txt,
                                "count": n, "need": SIM_NEED, "no_pack": len(got["no_pack"])})
    rd["level"] = _level(rd["checks"], F.READY_LEVELS)
    rd["label"] = _(F.READY_LABEL[rd["level"]])


def apply(r: dict, e, line: Optional[dict], today: dt.date, ds, rows: Optional[list[dict]] = None) -> None:
    """race_feasibility.races' hook: a multi-day 百岳's feasibility and readiness by the 攻頂日模擬."""
    if not applies(e) or r.get("skipped"):
        return
    rows = sim_rows(ds, today) if rows is None else rows
    feasibility(r, e, line, today, rows)
    if r.get("readiness"):
        readiness(r["readiness"], e, line, today, rows)
