"""
高山症預防提醒 (SP-546, owner 2026-10-14; before: SP-100 / SP-258 / SP-259): in the week before a
百岳 trip (event kind baiyue) or an event whose GPX tops out at ≥ 3,000 m, the floating suggestion
box (engine/suggestions.altitude_rows) shows one row with what prevents altitude sickness — unless
the athlete's own activities reached 3,000 m or higher in the last 3 months (Shen 2024: that is
what goes with less AMS). Nothing to fill in, nothing on any page: the owner — 「只是用來預防高山症，
跟本專案做的事無關」 — dropped the manual inputs (the 課表 calendar's 「睡在高處」 record, SP-259; the
race calculator's 海拔適應 choice, SP-260) and the 28-day 「安排適應週末」 row (SP-258). Information
only: the plan is not changed. It can be closed (✕) or put off (稍後提醒) as every row.

The event's altitude comes from its stored GPX (engine/event_gpx.py): the highest point (the
course's smoothed profile, z_max) and — for a multi-day trip whose nights are known from the GPX
(the day splits the user set; SP-533: camp waypoints are only candidates) — the altitude of each
night's stop. A 百岳 trip without a GPX still gets the row (every 百岳 is ≥ 3,000 m), only without
the heights.

Rules (docs/research/periodization-cross-sport.md §3.3 / §6.1 T6;
docs/research/mountaineering-physiology-scholars.md §3, SP-107 #4d):

  * Shen 2024 (J Formos Med Assoc, abstract; 1,021 people seen at 排雲山莊, 57 % AMS — patients,
    not every climber): no climb above 3,000 m in the last 3 months and a first climb above
    3,000 m go with AMS → the reminder's switch (RECENT_DAYS, HIGH_M) and its 「第一次」 line.
  * CDC Yellow Book (Hackett & Shlim, full text): above 3,000 m the sleeping altitude should not
    rise more than 500 m a night; a first night above 3,400 m is high risk; with symptoms don't go
    higher, descend when they get worse; 「Training and physical fitness do not affect risk」.
  * 玉山國家公園 [H13]: a night at about 2,500 m before a trip whose first night is above 3,000 m.
  The research is mostly about 4,000 m and higher; applying it at 3,000–3,950 m is 推估.

The athlete's altitude (推估): each activity's highest altitude (the elevation channel, the highest
held about 30 s so a GPS spike doesn't count), on every date the activity covers.

The race calculator's 百岳 altitude curve (SP-260, `acclimatisation_default`) is automatic only:
≥ 2 nights above 2,750 m in the 14 days before the trip (CDC's count, from the activities) → 部分,
else 未適應. Recorded nights (user_settings NIGHTS_KEY, SP-259) stay in the database untouched but
nothing reads them any more.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from backend.i18n import _

EVENT_MIN_M = 3000.0                # 推估: the reminder's line (research mostly ≥ 4,000 m)
REMIND_DAYS = 14                    # CDC: the 14 days before the trip (the calculator's night count)
NOTICE_DAYS = 7                     # SP-546 (owner 2026-10-14): the reminder in the week before the trip
SLEEP_M = 2750.0                    # CDC: > 2,750 m
NIGHTS = 2                          # CDC: ≥ 2 nights
HIGH_M = 3000.0                     # Schneider 2002 / Shen 2024: above 3,000 m (台灣百岳)
PRE_DAYS = 60                       # Schneider 2002: the 2 months before
RECENT_DAYS = 90                    # Shen 2024: the last 3 months
FIRST_NIGHT_M = 3400.0              # CDC: a first night above 3,400 m = high risk
NIGHT_STEP_M = 500.0                # CDC: ≤ +500 m a night above 3,000 m
HISTORY_DAYS = 3 * 365              # 推估: how far back 「從來沒上過 3,000 m」 looks
SPIKE_S = 30.0                      # s: the highest altitude held ~30 s (one GPS spike doesn't count)
KEY = "altitude_max_v1"             # Dataset.cached_series: {"max_m", "secs"} per activity
EVE_M = 2500.0                      # 玉山國家公園 [H13]: 「應先於海拔2,500公尺左右地區適應高度（約1晚）」
EVE_FIRST_NIGHT_M = 3000.0          # owner 2026-10-06: the 行前一晚 line only for a first night > 3,000 m
TRIP_KIND = "baiyue"                # planning.KINDS: a 百岳 trip gets the reminder even without a GPX

SRC = ("CDC Yellow Book（Hackett & Shlim，High-Altitude Travel and Altitude Illness）；"
       "Shen 2024（J Formos Med Assoc 123:1161）")


def _src(eve: bool) -> str:
    """The row's sources (+ 玉山國家公園, whose name is translated, when the 行前一晚 line is in it)."""
    return SRC + (_("；") + _("玉山國家公園管理處〈高山生理、高山症預防及處理〉") if eve else "")



# ---------------------------------------------------------------------------
# the event (its stored GPX)
# ---------------------------------------------------------------------------

def event_altitude(eid: str, days: int = 1, *, db_path=None, root=None) -> Optional[dict]:
    """{"max_m", "nights": [m per night] | None, "nights_source": stored | None} from the
    event's stored GPX, None without one. Nights only from the user's day splits (equal-km days
    would put the night anywhere; camp waypoints are candidates, SP-533)."""
    from backend.engine import event_gpx as EG
    row = EG.get(eid, db_path)
    if row is None or row.get("z_max") is None:
        return None
    out = {"max_m": float(row["z_max"]), "nights": None, "nights_source": None}
    n = max(1, int(days or 1))
    if n < 2:
        return out
    cuts, src = EG.split_source(row, n)
    if src != "stored":                     # SP-533: camp waypoints are candidates, not nights
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
    {"nights": nights above SLEEP_M from start − 14 days to today (CDC; a night counts when the
     days on both sides of it reached SLEEP_M), "pre_days": days above HIGH_M in the last 60 days
     (Schneider), "recent": any day above HIGH_M in the last 90 days (Shen), "ever": any day
     above HIGH_M in the data}."""
    since = start - dt.timedelta(days=REMIND_DAYS)
    nights = sum(1 for d, m in alts.items() if since <= d < today and m >= SLEEP_M
                 and alts.get(d + dt.timedelta(days=1), -1e9) >= SLEEP_M)
    high = {d for d, m in alts.items() if m >= HIGH_M and d <= today}
    return {"nights": nights,
            "pre_days": sum(1 for d in high if d > today - dt.timedelta(days=PRE_DAYS)),
            "recent": any(d > today - dt.timedelta(days=RECENT_DAYS) for d in high),
            "ever": bool(high)}


# 百岳計算機的海拔適應預設 (SP-260, owner 2026-10-06; altitude-training.md §4.1): ≥ 2 nights above
# 2,750 m in the 14 days before the trip (CDC's count) → 部分, else 未適應 (推估: 「部分」 is itself
# the midpoint of the two curves). SP-546: the only value (no choice on the page any more).
# Routes under 3,000 m (EVENT_MIN_M) get no 部分 on the page.
PARTIAL_NIGHTS = NIGHTS


def accl_default(nights: int) -> str:
    return "partial" if nights >= PARTIAL_NIGHTS else "unacclimatised"


def acclimatisation_default(alts: dict, today: dt.date, date: Optional[dt.date]) -> dict:
    """The calculator's value for a trip on `date` (None or past = today): {"default", "nights",
    "since", "start"} — nights counted as exposure() does (the activities)."""
    start = max(date or today, today)
    ex = exposure(alts, today, start)
    return {"default": accl_default(ex["nights"]), "nights": ex["nights"],
            "since": (start - dt.timedelta(days=REMIND_DAYS)).isoformat(), "start": start.isoformat()}


# ---------------------------------------------------------------------------
# 睡在高處的紀錄 (SP-259, dropped by SP-546): user_settings NIGHTS_KEY = [{"day": ISO, "m": int}]
# stays in the database (no destructive migration) and is still validated on write, read by nothing
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



# ---------------------------------------------------------------------------
# the reminder
# ---------------------------------------------------------------------------

def _sent(x: str) -> str:
    """One sentence with its full stop (「。」 in Chinese, "." in English)."""
    return _("{x}。", x=x)


def is_high_trip(kind: Optional[str], alt: Optional[dict]) -> bool:
    """A 百岳 trip (every 百岳 is ≥ 3,000 m, GPX or not) or an event whose GPX reaches EVENT_MIN_M."""
    top = (alt or {}).get("max_m")
    return kind == TRIP_KIND or (top is not None and top >= EVENT_MIN_M)


def first_night_high(alt: Optional[dict]) -> Optional[float]:
    """The trip's first night (m) when it is above EVE_FIRST_NIGHT_M (owner 2026-10-06: the 行前一晚
    line only then); None otherwise or when the GPX doesn't say where the nights are."""
    nights = (alt or {}).get("nights") or []
    return float(nights[0]) if nights and nights[0] > EVE_FIRST_NIGHT_M else None


def reminder(ev: dict, alt: Optional[dict], ex: dict, today: dt.date) -> Optional[dict]:
    """The box row for one trip (engine/suggestions.altitude_rows adds the id), or None when it is
    not a high trip (is_high_trip), not 1–7 days away, or the athlete reached ≥ 3,000 m in the last
    3 months (`ex["recent"]`, exposure). `ev`: {id, name, start, kind}; `alt`: event_altitude (None =
    no GPX)."""
    start = ev["start"] if isinstance(ev["start"], dt.date) else dt.date.fromisoformat(str(ev["start"])[:10])
    days_to = (start - today).days
    if not is_high_trip(ev.get("kind"), alt) or not 0 < days_to <= NOTICE_DAYS or ex.get("recent"):
        return None
    alt = alt or {}
    top = alt.get("max_m")
    name = ev.get("name") or ""
    title = (_("「{name}」最高約 {m:,.0f} m：預防高山症", name=name, m=top) if top is not None
             else _("「{name}」：預防高山症", name=name))
    status = _("近 3 個月沒有到 3,000 m 以上：高山症風險較高（Shen 2024）")
    act = _("上升放慢，出現頭痛、噁心等症狀就不再往上，變嚴重就下撤（CDC）")
    lines, flags = [], []
    if not ex.get("ever"):
        lines.append(_("紀錄裡沒有到過 3,000 m 以上：第一次上 3,000 m 是高山症的相關因子（Shen 2024）"))
        flags.append("first")
    nights = alt.get("nights") or []
    eve = first_night_high(alt) is not None
    if eve:
        lines.append(_("第一晚在 3,000 m 以上：行前一晚住 2,500 m 左右（玉山國家公園建議）"))
    if nights and nights[0] > FIRST_NIGHT_M:
        lines.append(_("第一晚睡在約 {m:,.0f} m，超過 3,400 m 屬高風險（CDC）", m=nights[0]))
        flags.append("n1")
    jumps = [i for i in range(1, len(nights)) if nights[i] >= HIGH_M and nights[i] - nights[i - 1] > NIGHT_STEP_M]
    if jumps:
        i = jumps[0]
        lines.append(_("第 {a} 晚到第 {b} 晚的睡眠高度升高 {d:,.0f} m：CDC 建議 3,000 m 以上每晚升高不超過 500 m",
                       a=i, b=i + 1, d=nights[i] - nights[i - 1]))
        flags.append("jump")
    fit = _("體能好壞不影響高山症風險（CDC）：練得好不代表不會高山症")
    help_ = "\n".join([_sent(x) for x in [status, act] + lines + [fit]] + [
        _("出發前 7 天內提醒，按「稍後提醒」到出發前一天再提醒；近 3 個月有活動到 3,000 m 以上就不提醒（看活動的海拔紀錄，推估）。"
          "研究多在 4,000 m 以上，套到 3,000 m 左右是推估。只是提醒，不會改課表。"),
        _("來源：{src}", src=_src(eve))])
    return {"event_id": ev.get("id"), "start": start.isoformat(), "days_to": days_to,
            "max_m": round(top) if top is not None else None, "nights": nights or None, "flags": flags,
            "title": title, "reason": _reason(), "help": help_, "src": _src(eve)}


def _reason() -> str:
    """The box's text (3 lines at most): the status and act lines of reminder() as one msgid, so English can
    leave out the Shen 2024 half (SP-546 R1: it pushed "(CDC)." past the third line); that line and its
    source stay first in the ? help, in both languages."""
    return _("近 3 個月沒有到 3,000 m 以上：高山症風險較高（Shen 2024）。上升放慢，出現頭痛、噁心等症狀就不再往上，變嚴重就下撤（CDC）。")
