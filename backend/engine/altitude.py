"""
高度適應提醒 (SP-100): before an event whose highest point is ≥ 3,000 m, the
floating suggestion box (engine/suggestions.altitude_rows) shows what the
athlete's own recent altitude says, 14 days before the start. Information only:
the plan is not changed.

安排適應週末 (SP-258, owner 2026-10-06 / 2026-10-07; docs/research/altitude-training.md §2.2,
§2.3, §4.2 單 1): the row starts 28 days out (EARLY_DAYS) so CDC's 2 nights still fit a weekend.
15–28 days before: one row 「安排適應週末」 that lists the weekends inside the 14 days before the
start (the weekend whose Sunday is the departure day counts; the ones inside the event's 減量期
marked 「走輕鬆路線」) — and no place (owner 2026-10-07: 松雪樓 dropped, no other names, just the
reminder). It can be closed (✕) for that trip for good (suggestions.altitude_rows gives it the id
`altitude_plan:<event>:<start>`, kept by suggestions.prune until the trip starts). When the trip's
first night is above 3,000 m (owner: 玉山、嘉明湖 kind of trips) 「行前一晚住約 2,500 m」
(玉山國家公園) is only in its 說明 (?), never in the row's text. 1–14 days before: the checks
below, as before (its 說明 also names 塔塔加 2,610 m / 大禹嶺 2,565 m for that night, owner
2026-10-06). Beidleman 2018 (High Alt Med Biol 19:329, abstract): 2 days at 3,000 m before
4,300 m, AMS 83 % → 43 %.

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
    partly kept 12 days back at low altitude (Beidleman 2017, J Appl Physiol
    123:1214 [400]).
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

睡在高處的紀錄 (SP-259, owner 2026-10-06: on the 課表 calendar's day, no symptoms): the
athlete marks 「這一晚睡在 X m」 on a day (the night from that evening to the next morning), stored
as user_settings NIGHTS_KEY = [{day, m}] — e.g. a drive up to 松雪樓 and a climb the next day,
which the activities can't see. `exposure` counts a night when the activities say so OR it is
recorded at ≥ 2,750 m; nights are counted per date, so one night is never counted twice. A
recorded night at ≥ 3,000 m also makes its evening and the next morning 「到過 3,000 m 以上」
days for the Schneider / Shen lines (推估). No record = exactly the old result. The record
dialog can be filled from a plan event's GPX night (the camp of that night, `event_night`).
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
# SP-258 (owner 2026-10-06: 28, not 21): the row starts this many days out; 15–28 = 安排適應週末
EARLY_DAYS = 28
EVE_M = 2500.0                      # 玉山國家公園 [H13]: 「應先於海拔2,500公尺左右地區適應高度（約1晚）」
EVE_FIRST_NIGHT_M = 3000.0          # owner 2026-10-06: the 行前一晚 line only for a first night > 3,000 m
# places of the 1–14 day row's 行前一晚 note, elevations checked 2026-10-07 (docs/research/
# altitude-training.md §2.3, [H18]–[H20]); the 15–28 day row names none (owner 2026-10-07):
#   塔塔加 2,610 m — 玉山國家公園管理處 (ysnp.gov.tw, 塔塔加遊客中心): 「海拔2,610公尺的塔塔加」
#   大禹嶺 2,565 m — 太魯閣國家公園管理處 (taroko.gov.tw, 大禹嶺): 「海拔2,565公尺」
TATAKA_M = 2610
DAYULING_M = 2565

SRC = ("CDC Yellow Book（Hackett & Shlim，High-Altitude Travel and Altitude Illness）；"
       "Schneider…Bärtsch 2002（Med Sci Sports Exerc 34:12）；Shen 2024（J Formos Med Assoc 123:1161）")
SRC_PLAN = "CDC Yellow Book（Hackett & Shlim）；Beidleman 2018（High Alt Med Biol 19:329）"


def _src_plan() -> str:
    """The 安排適應週末 row's sources (SRC_PLAN + 玉山國家公園, whose name is translated)."""
    return SRC_PLAN + "；" + _("玉山國家公園管理處〈高山生理、高山症預防及處理〉")


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


def exposure(alts: dict, today: dt.date, start: dt.date, manual: Optional[dict] = None) -> dict:
    """What the days say before a trip starting `start`:
    {"nights": nights above SLEEP_M from start − 14 days to today (CDC), "pre_days": days above
     HIGH_M in the last 60 days (Schneider), "recent": any day above HIGH_M in the last 90 days
     (Shen), "ever": any day above HIGH_M in the data, "manual": how many of `nights` only the
     records give}. `manual`: {date: m} recorded nights (SP-259, load_nights); None / {} = the
     activities alone (the result before SP-259)."""
    manual = manual or {}
    since = start - dt.timedelta(days=REMIND_DAYS)
    act = {d for d, m in alts.items() if since <= d < today and m >= SLEEP_M
           and alts.get(d + dt.timedelta(days=1), -1e9) >= SLEEP_M}
    rec = {d for d, m in manual.items() if since <= d < today and m >= SLEEP_M}
    high = {d for d, m in alts.items() if m >= HIGH_M and d <= today}
    for d, m in manual.items():                 # a night up high: its evening and the next morning (推估)
        if m >= HIGH_M:
            high |= {x for x in (d, d + dt.timedelta(days=1)) if x <= today}
    return {"nights": len(act | rec),
            "pre_days": sum(1 for d in high if d > today - dt.timedelta(days=PRE_DAYS)),
            "recent": any(d > today - dt.timedelta(days=RECENT_DAYS) for d in high),
            "ever": bool(high), "manual": len(rec - act)}


# 百岳計算機的海拔適應預設 (SP-260, owner 2026-10-06; altitude-training.md §4.1): ≥ 2 nights above
# 2,750 m in the 14 days before the trip (CDC's count, the same as the reminder) → 部分, else 未適應
# (推估: 「部分」 is itself the midpoint of the two curves); 已適應 only when the athlete picks it.
# Routes under 3,000 m (EVENT_MIN_M) get no 部分 on the page.
PARTIAL_NIGHTS = NIGHTS


def accl_default(nights: int) -> str:
    return "partial" if nights >= PARTIAL_NIGHTS else "unacclimatised"


def acclimatisation_default(alts: dict, today: dt.date, date: Optional[dt.date],
                            manual: Optional[dict] = None) -> dict:
    """The calculator's default for a trip on `date` (None or past = today): {"default", "nights",
    "manual", "since", "start"} — nights counted as exposure() does (activities + records)."""
    start = max(date or today, today)
    ex = exposure(alts, today, start, manual)
    return {"default": accl_default(ex["nights"]), "nights": ex["nights"], "manual": ex["manual"],
            "since": (start - dt.timedelta(days=REMIND_DAYS)).isoformat(), "start": start.isoformat()}


# ---------------------------------------------------------------------------
# 睡在高處的紀錄 (SP-259): user_settings NIGHTS_KEY = [{"day": ISO, "m": int}]
# ---------------------------------------------------------------------------

NIGHTS_KEY = "altitude.nights"
NIGHT_MAX_M = 8849                  # Everest: no night is recorded higher
MAX_NIGHTS = 1000                   # 推估: years of nights, keeps the setting small


def validate_nights(value) -> None:
    """The stored list (raises ValueError): [{day: YYYY-MM-DD, m: 1–8849}], one per day."""
    if not isinstance(value, list) or len(value) > MAX_NIGHTS:
        raise ValueError(f"altitude.nights must be a list of at most {MAX_NIGHTS} {{day, m}}")
    seen = set()
    for e in value:
        if not isinstance(e, dict) or set(e) != {"day", "m"}:
            raise ValueError("altitude.nights entries must be {day, m}")
        try:
            day = dt.date.fromisoformat(str(e["day"]))
        except ValueError:
            raise ValueError("altitude.nights day must be YYYY-MM-DD")
        if day.isoformat() != e["day"] or e["day"] in seen:
            raise ValueError("altitude.nights: one entry per day, YYYY-MM-DD")
        m = e["m"]
        if isinstance(m, bool) or not isinstance(m, int) or not 0 < m <= NIGHT_MAX_M:
            raise ValueError(f"altitude.nights m must be a whole number of metres, 1–{NIGHT_MAX_M}")
        seen.add(e["day"])


def clean_m(m) -> int:
    """A typed altitude → whole metres (raises ValueError)."""
    try:
        v = float(m)
    except (TypeError, ValueError):
        raise ValueError(_("高度要是數字（公尺）"))
    if not math.isfinite(v) or not 0 < v <= NIGHT_MAX_M:
        raise ValueError(_("高度要在 1–{mx:,} m 之間", mx=NIGHT_MAX_M))
    return int(round(v))


def set_night(nights: list, day: str, m: Optional[int]) -> list:
    """The list with `day`'s night set to `m` (None = removed), sorted by day."""
    out = [e for e in (nights or []) if e.get("day") != day]
    if m is not None:
        out.append({"day": day, "m": int(m)})
    return sorted(out, key=lambda e: e["day"])


def nights_map(value) -> dict:
    """{date: m} of a stored list; {} when it is bad."""
    try:
        validate_nights(value or [])
    except ValueError:
        return {}
    return {dt.date.fromisoformat(e["day"]): float(e["m"]) for e in value or []}


def load_nights(user_id: int = 1) -> dict:
    """The recorded nights {date: m} (synchronous read-only sqlite, like blackouts.load)."""
    from backend.engine.wko5expr.datasource import read_setting
    return nights_map(read_setting(NIGHTS_KEY, [], user_id))


def event_night(events, day: dt.date, alt_of) -> Optional[dict]:
    """The plan event night on `day` (a ≥ 2 day trip's nights are its first day … its second-last
    day) whose altitude its GPX knows: {"m", "event_id", "name", "night"} — fills the record
    dialog (SP-259 「從百岳賽事的營地自動帶入」); None otherwise. `alt_of(event)`: event_altitude."""
    for e in events or []:
        n = int(getattr(e, "days", 1) or 1)
        try:
            first = dt.date.fromisoformat(str(getattr(e, "date", ""))[:10])
        except ValueError:
            continue
        i = (day - first).days
        if n < 2 or not 0 <= i < n - 1:
            continue
        nights = (alt_of(e) or {}).get("nights") or []
        if i < len(nights) and nights[i]:
            return {"m": int(round(nights[i])), "event_id": e.id, "name": e.name, "night": i + 1}
    return None


# ---------------------------------------------------------------------------
# the reminder
# ---------------------------------------------------------------------------

def weekends(start: dt.date, today: dt.date, taper_days: int = 0) -> list[dict]:
    """安排適應週末 (SP-258): the weekends whose two nights (Friday and Saturday) both fall inside
    the 14 days before `start` (CDC's window) and not before today — [{"sat", "sun", "taper"}];
    the weekend whose Sunday is the departure day counts (its two nights are before the trip,
    owner 2026-10-07). `taper`: the weekend reaches into the event's last `taper_days` days."""
    since = start - dt.timedelta(days=REMIND_DAYS)
    fri = since + dt.timedelta(days=(4 - since.weekday()) % 7)        # the first Friday ≥ since
    out = []
    while fri + dt.timedelta(days=2) <= start:
        if fri >= today:
            sat, sun = fri + dt.timedelta(days=1), fri + dt.timedelta(days=2)
            out.append({"sat": sat.isoformat(), "sun": sun.isoformat(),
                        "taper": bool(taper_days) and sun >= start - dt.timedelta(days=int(taper_days))})
        fri += dt.timedelta(days=7)
    return out


def _md(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def _weekend_label(w: dict) -> str:
    a, b = dt.date.fromisoformat(w["sat"]), dt.date.fromisoformat(w["sun"])
    txt = f"{a.month}/{a.day}–{b.day}" if a.month == b.month else f"{_md(w['sat'])}–{_md(w['sun'])}"
    return txt + (_("（減量期：走輕鬆路線）") if w["taper"] else "")


def first_night_high(alt: Optional[dict]) -> Optional[float]:
    """The trip's first night (m) when it is above EVE_FIRST_NIGHT_M (owner 2026-10-06: the 行前一晚
    line only then); None otherwise or when the GPX doesn't say where the nights are."""
    nights = (alt or {}).get("nights") or []
    return float(nights[0]) if nights and nights[0] > EVE_FIRST_NIGHT_M else None


def _plan_row(ev: dict, alt: dict, start: dt.date, today: dt.date) -> dict:
    """15–28 days before (SP-258): 安排適應週末, the weekends of the 14-day window listed. No place
    named (owner 2026-10-07); 行前一晚 only in the 說明."""
    m = alt["max_m"]
    ws = weekends(start, today, ev.get("taper_days") or 0)
    flags = ["plan"]
    title = _("「{name}」最高約 {m:,.0f} m：安排適應週末", name=ev.get("name") or "", m=m)
    if ws:
        what = _("出發前 14 天內找一個週末，在 2,750 m 以上睡 2 晚。可選：{days}",
                 days="、".join(_weekend_label(w) for w in ws))
    else:
        what = _("出發前 14 天內在 2,750 m 以上連睡 2 晚")
    reason = what + "。"
    help_ = [_("CDC 建議出發前 14 天內在 2,750 m 以上睡至少 2 晚，越接近出發越好。Beidleman 2018："
               "先在 3,000 m 住 2 天再上 4,300 m，高山症從 83% 降到 43%。"),
             what + "。"]
    if any(w["taper"] for w in ws):
        help_.append(_("標「減量期」的週末一樣可以去，白天走輕鬆的路線、不要走長距離（推估）。"))
    n1 = first_night_high(alt)
    if n1 is not None:
        flags.append("eve")
        help_.append(_("第一晚睡在約 {m:,.0f} m：行前一晚先住約 2,500 m（玉山國家公園建議上 3,000 m 以上的高山前，"
                       "先在 2,500 m 左右住約 1 晚；這一晚低於 2,750 m，不算進適應週末的 2 晚）。", m=n1))
    help_ += [_("最高點與每晚的高度來自賽事的 GPX。研究多在 4,000 m 以上，套到 3,000–3,950 m 是推估。"
                "出發前 14 天內會改成檢查你最近的高度紀錄。只是提醒，不會改課表。"),
              _("按 ✕ 關掉後，這趟行程不再顯示這個提醒；出發前 14 天內的高度檢查照常出現。"),
              _("來源：{src}", src=_src_plan())]
    return {"event_id": ev.get("id"), "start": start.isoformat(), "days_to": (start - today).days,
            "max_m": round(m), "nights": alt.get("nights") or None, "flags": flags, "title": title,
            "reason": reason, "help": "\n".join(help_), "weekends": ws, "src": _src_plan()}


def reminder(ev: dict, alt: dict, ex: Optional[dict], today: dt.date) -> Optional[dict]:
    """The box row for one event (engine/suggestions.altitude_rows adds the id), or None when
    the event is under 3,000 m or not 1–28 days away. `ev`: {id, name, start, days, taper_days}.
    15–28 days: 安排適應週末 (`ex` not read, may be None); 1–14 days: the checks on `ex`."""
    start = ev["start"] if isinstance(ev["start"], dt.date) else dt.date.fromisoformat(str(ev["start"])[:10])
    days_to = (start - today).days
    if alt is None or alt.get("max_m") is None or alt["max_m"] < EVENT_MIN_M or not 0 < days_to <= EARLY_DAYS:
        return None
    if days_to > REMIND_DAYS:
        return _plan_row(ev, alt, start, today)
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
    # SP-258: 行前一晚 only in the 說明 here — the row's title, text and id stay as before
    n1 = first_night_high(alt)
    eve = [_("第一晚在 3,000 m 以上：行前一晚先住約 2,500 m（玉山國家公園建議；例如塔塔加 {a:,} m、大禹嶺 {b:,} m）。",
             a=TATAKA_M, b=DAYULING_M)] if n1 is not None else []
    # SP-259: where the nights came from, and how to add one the activities can't see
    rec = ([_("其中 {n} 晚來自你在課表日曆記的「睡在高處」。", n=ex["manual"])] if ex.get("manual") else []) + \
        [_("開車上山過夜、隔天才爬，活動看不到這一晚：在課表日曆那一天按右鍵（手機長按）記「睡在高處」。")]
    help_ = "\n".join(
        [status + "。"] + rec + [x + "。" for x in lines] + eve + [fit + "。",
         _("最高點與每晚的高度來自賽事的 GPX（每晚 = 分段點或營地）；你的高度來自活動的海拔紀錄，"
           "前後兩天都到 2,750 m 以上才算在高處過夜（推估）。研究多在 4,000 m 以上，套到 3,000–3,950 m 是推估。"
           "只是提醒，不會改課表。"),
         _("來源：{src}", src=SRC)])
    return {"event_id": ev.get("id"), "start": start.isoformat(), "days_to": days_to, "max_m": round(m),
            "nights": nights or None, "flags": flags, "title": title, "reason": reason, "help": help_}
