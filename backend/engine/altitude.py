"""
高度適應提醒 (SP-100): before an event whose highest point is ≥ 3,000 m, the
floating suggestion box (engine/suggestions.altitude_rows) shows what the
athlete's own recent altitude says, 14 days before the start. Information only:
the plan is not changed.

The event's altitude comes from its stored GPX (engine/event_gpx.py): the
highest point (the course's smoothed profile, z_max) and — for a multi-day trip
whose nights are known from the GPX (stored day splits or camp waypoints) — the
altitude of each night's stop. Without a GPX there is nothing to say.

Rules (docs/research/periodization-cross-sport.md §3.3 / §6.1 T6;
docs/research/mountaineering-physiology-scholars.md §3, SP-107 #4d):

  * CDC Yellow Book (Hackett & Shlim, full text): ≥ 2 nights above 2,750 m in
    the 14 days before the trip help (the closer the better); above 3,000 m the
    sleeping altitude should not rise more than 500 m a night; a first night
    above 3,400 m is high risk (排雲山莊 3,402 m is just over the line);
    「Training and physical fitness do not affect risk」. Acclimatisation is
    partly kept 12 days back at low altitude (Pichon 2017 [400]).
  * Schneider…Bärtsch 2002 (MSSE, abstract): pre-exposure = > 4 days above
    3,000 m in the 2 months before; with it and a slow ascent, susceptible
    climbers' AMS 58 % → 7 %.
  * Shen 2024 (J Formos Med Assoc, abstract; 1,021 people seen at 排雲山莊, 57 %
    AMS — patients, not every climber): no climb above 3,000 m in the last 3
    months and a first climb above 3,000 m go with AMS.
  The research is mostly about 4,000 m and higher; applying it at 3,000–3,950 m
  is 推估.

The athlete's altitude (推估): each activity's highest altitude (the elevation
channel, the highest held about 30 s so a GPS spike doesn't count), on every date the
activity covers. A night counts as spent above 2,750 m when the days on both sides
of it reached 2,750 m (a multi-day activity, or activities on consecutive days) —
where the tent or hut was is not in the data.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from backend.i18n import _

EVENT_MIN_M = 3000.0                # 推估: the reminder's line (research mostly ≥ 4,000 m)
REMIND_DAYS = 14                    # CDC: the 14 days before the trip
SLEEP_M = 2750.0                    # CDC: > 2,750 m
NIGHTS = 2                          # CDC: ≥ 2 nights
HIGH_M = 3000.0                     # Schneider 2002 / Shen 2024: above 3,000 m
PRE_DAYS = 60                       # Schneider 2002: the 2 months before
PRE_MIN_DAYS = 5                    # Schneider 2002: > 4 days
RECENT_DAYS = 90                    # Shen 2024: the last 3 months
FIRST_NIGHT_M = 3400.0              # CDC: a first night above 3,400 m = high risk
NIGHT_STEP_M = 500.0                # CDC: ≤ +500 m a night above 3,000 m
HISTORY_DAYS = 3 * 365              # 推估: how far back 「從來沒上過 3,000 m」 looks
SPIKE_S = 30.0                      # s: the highest altitude held ~30 s (one GPS spike doesn't count)
KEY = "altitude_max_v1"             # Dataset.cached_series: {"max_m", "secs"} per activity

SRC = ("CDC Yellow Book（Hackett & Shlim，High-Altitude Travel and Altitude Illness）；"
       "Schneider…Bärtsch 2002（Med Sci Sports Exerc 34:12）；Shen 2024（J Formos Med Assoc 123:1161）")


# ---------------------------------------------------------------------------
# the event (its stored GPX)
# ---------------------------------------------------------------------------

def event_altitude(eid: str, days: int = 1, *, db_path=None, root=None) -> Optional[dict]:
    """{"max_m", "nights": [m per night] | None, "nights_source": stored | camp | None} from the
    event's stored GPX, None without one. Nights only from day splits or camp waypoints (equal-km
    days would put the night anywhere)."""
    from backend.engine import event_gpx as EG
    row = EG.get(eid, db_path)
    if row is None or row.get("z_max") is None:
        return None
    out = {"max_m": float(row["z_max"]), "nights": None, "nights_source": None}
    n = max(1, int(days or 1))
    if n < 2:
        return out
    cuts = EG.splits_for(row, n)
    src = ("stored" if cuts and EG.clean_splits(row.get("day_splits") or [], row["km"]) == cuts
           else "camp" if cuts and EG.clean_splits(row.get("camp_km") or [], row["km"]) == cuts else None)
    if src is None:
        return out
    try:
        got = EG.track(eid, row, db_path=db_path, root=root)
        if got is None:
            return out
        import numpy as np
        from backend.engine.racepower import course as CO
        prof = CO.build_course(got[0], split="none")["profile"]
        out["nights"] = [round(float(np.interp(k, prof["km"], prof["z"]))) for k in cuts]
        out["nights_source"] = src
    except Exception:                       # noqa: BLE001 — the max is still known
        pass
    return out


# ---------------------------------------------------------------------------
# the athlete's altitude (activities)
# ---------------------------------------------------------------------------

def _act(ds, w) -> Optional[dict]:
    import numpy as np
    z = ds.channel(w.idx, "_elevation")
    if z is None:
        z = ds.channel(w.idx, "elevation")
    if z is None:
        return None
    z = np.asarray(z, float)
    z = z[np.isfinite(z)]
    if not len(z):
        return None
    t = ds.channel(w.idx, "elapsedtime")
    t = np.asarray(t, float) if t is not None else None
    ok = t is not None and np.isfinite(t).any()
    secs = float(np.nanmax(t)) if ok else float((w.metrics or {}).get("duration") or 0.0)
    step = float(np.nanmedian(np.diff(t))) if ok and len(t) > 1 else 1.0
    n = max(1, int(round(SPIKE_S / step))) if step > 0 else 1
    return {"max_m": float(np.sort(z)[-min(n, len(z))]), "secs": secs}


def day_altitudes(ds, today: dt.date, days: int = HISTORY_DAYS) -> dict:
    """{date: highest altitude (m) reached that day} over `days` up to `today`; an activity
    counts on every date it covers. Memoised on ds.memo, per activity on disk (cached_series)."""
    from backend.engine.wko5expr.dataset import date_to_day, day_to_date
    tday = int(math.floor(date_to_day(today)))
    memo = getattr(ds, "memo", None)
    mk = ("altitude_days", tday, days, len(getattr(ds, "workouts", []) or []))
    if isinstance(memo, dict) and mk in memo:
        return memo[mk]
    cached = getattr(ds, "cached_series", None)
    out: dict = {}
    for w in ds.workouts:
        if not tday - days < math.floor(w.day) <= tday:
            continue
        a = cached(KEY, w, lambda w=w: _act(ds, w)) if cached is not None else _act(ds, w)
        if not a or not a.get("max_m"):
            continue
        d0 = day_to_date(w.day)
        d1 = day_to_date(w.day + (a.get("secs") or 0.0) / 86400.0)
        d = d0
        while d <= d1:
            out[d] = max(out.get(d, -1e9), float(a["max_m"]))
            d += dt.timedelta(days=1)
    if cached is not None and hasattr(ds, "flush_series"):
        ds.flush_series()
    if isinstance(memo, dict):
        memo[mk] = out
    return out


def exposure(alts: dict, today: dt.date, start: dt.date) -> dict:
    """What the days say before a trip starting `start`:
    {"nights": nights above SLEEP_M from start − 14 days to today (CDC), "pre_days": days above
     HIGH_M in the last 60 days (Schneider), "recent": any day above HIGH_M in the last 90 days
     (Shen), "ever": any day above HIGH_M in the data}."""
    since = start - dt.timedelta(days=REMIND_DAYS)
    nights = sum(1 for d, m in alts.items() if since <= d < today and m >= SLEEP_M
                 and alts.get(d + dt.timedelta(days=1), -1e9) >= SLEEP_M)
    high = [d for d, m in alts.items() if m >= HIGH_M and d <= today]
    return {"nights": nights,
            "pre_days": sum(1 for d in high if d > today - dt.timedelta(days=PRE_DAYS)),
            "recent": any(d > today - dt.timedelta(days=RECENT_DAYS) for d in high),
            "ever": bool(high)}


# ---------------------------------------------------------------------------
# the reminder
# ---------------------------------------------------------------------------

def reminder(ev: dict, alt: dict, ex: dict, today: dt.date) -> Optional[dict]:
    """The box row for one event (engine/suggestions.altitude_rows adds the id), or None when
    the event is under 3,000 m or not 1–14 days away. `ev`: {id, name, start, days}."""
    start = ev["start"] if isinstance(ev["start"], dt.date) else dt.date.fromisoformat(str(ev["start"])[:10])
    days_to = (start - today).days
    if alt is None or alt.get("max_m") is None or alt["max_m"] < EVENT_MIN_M or not 0 < days_to <= REMIND_DAYS:
        return None
    m = alt["max_m"]
    lines, flags = [], []
    if ex["nights"] >= NIGHTS:
        status = _("近 14 天有 {n} 晚在 2,750 m 以上：已有部分適應（CDC）", n=ex["nights"])
        flags.append("acc")
    elif ex["nights"]:
        status = _("近 14 天有 1 晚在 2,750 m 以上；CDC 建議出發前 14 天內至少 2 晚，越接近出發越好")
        flags.append("one")
    else:
        status = _("CDC 建議：出發前 14 天內在 2,750 m 以上睡至少 2 晚，越接近出發越好")
    if ex["pre_days"] >= PRE_MIN_DAYS:
        lines.append(_("近 2 個月有 {n} 天到 3,000 m 以上：有事前暴露（Schneider 2002：有事前暴露又慢慢上升，"
                       "容易高山症的人發生率從 58% 降到 7%）", n=ex["pre_days"]))
        flags.append("pre")
    elif ex["recent"]:
        lines.append(_("近 2 個月到 3,000 m 以上只有 {n} 天，還不算事前暴露（Schneider 2002：> 4 天）",
                       n=ex["pre_days"]))
        flags.append("recent")
    else:
        lines.append(_("近 3 個月沒有到 3,000 m 以上：高山症風險較高（Shen 2024，玉山排雲山莊就診者的資料）"))
    if not ex["ever"]:
        lines.append(_("紀錄裡沒有到過 3,000 m 以上：第一次上 3,000 m 是高山症的相關因子（Shen 2024）"))
        flags.append("first")
    nights = alt.get("nights") or []
    if nights and nights[0] > FIRST_NIGHT_M:
        lines.append(_("第一晚睡在約 {m:,.0f} m，超過 3,400 m 屬高風險（CDC；排雲山莊 3,402 m 就在線上）", m=nights[0]))
        flags.append("n1")
    jumps = [i for i in range(1, len(nights)) if nights[i] >= HIGH_M and nights[i] - nights[i - 1] > NIGHT_STEP_M]
    if jumps:
        i = jumps[0]
        lines.append(_("第 {a} 晚到第 {b} 晚的睡眠高度升高 {d:,.0f} m：CDC 建議 3,000 m 以上每晚升高不超過 500 m",
                       a=i, b=i + 1, d=nights[i] - nights[i - 1]))
        flags.append("jump")
    fit = _("體能好壞不影響高山症風險（CDC）：練得好不代表不會高山症")
    title = _("「{name}」最高約 {m:,.0f} m：出發前的高度適應", name=ev.get("name") or "", m=m)
    reason = status + "。" + (lines[0] if lines else "") + ("。" if lines else "") + fit + "。"
    help_ = "\n".join(
        [status + "。"] + [x + "。" for x in lines] + [fit + "。",
         _("最高點與每晚的高度來自賽事的 GPX（每晚 = 分段點或營地）；你的高度來自活動的海拔紀錄，"
           "前後兩天都到 2,750 m 以上才算在高處過夜（推估）。研究多在 4,000 m 以上，套到 3,000–3,950 m 是推估。"
           "只是提醒，不會改課表。"),
         _("來源：{src}", src=SRC)])
    return {"event_id": ev.get("id"), "start": start.isoformat(), "days_to": days_to, "max_m": round(m),
            "nights": nights or None, "flags": flags, "title": title, "reason": reason, "help": help_}
