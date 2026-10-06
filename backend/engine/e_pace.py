"""
E 配速 from one race result (SP-276; docs/research/lthr-low-confidence-testing.md §2.1, §2.3,
§5 單 3, §6.1 第 2 點).

徐國峰's 90-minute test is run at E pace, looked up from a race result (RQ 跑力 or Daniels
VDOT), not from LTHR. The app had no E pace at all; this module gives one:

  * VDOT from a race (distance, time) with the Daniels–Gilbert equations (Daniels & Gilbert
    1979, *Oxygen Power*; the formulas every VDOT calculator uses):
        VO2(v)    = −4.60 + 0.182258·v + 0.000104·v²          v in m/min
        %max(t)   = 0.8 + 0.1894393·e^(−0.012778·t) + 0.2989558·e^(−0.1932605·t)   t in min
        VDOT      = VO2(race speed) ÷ %max(race time)
  * E pace = the speeds whose VO2 is 62 % and 70 % of VDOT (E_LOW / E_HIGH). The bounds are
    fitted to Daniels' published E-pace table (4th ed., as reproduced by sport-calculator.com:
    VDOT 30–70 all land on 62.0 % / 70.0 % ± 0.1 — 推估 fit, the book itself not read).
    A 10 K in 45:00 → VDOT 45.3, E 5:32–6:06 /km (the table's VDOT 45 row: 5:34–6:08).

Used only by the 90-minute test (aet_test.session, coros_workouts, workout_steps) and its
explanation — no other session or zone changes (owner 2026-10-06: 先不動區間).

Where the race comes from (owner 2026-10-06, §6.1 第 2 點):
  * the athlete enters it in 設定 (距離、時間、日期) → stored as RACE_KEY;
  * runs the app recognises as races (threshold_confidence.RACE_WORDS on the title, or a road
    race on the plan that day) are offered as candidates; one is used only after the athlete
    confirms it (it is then stored as RACE_KEY with source "activity"). Unconfirmed
    candidates are never used.
  * trail races don't count (climb distorts the time): a plan trail race / 百岳 that day, the
    runningtrail tag, or ≥ TRAIL_M_PER_KM of climb per km.
A race older than STALE_DAYS (推估) is too old: shown as such, NOT used for the 90-minute test
(owner 2026-10-06; aet_test.xu_target then falls back to a tested CP / the talk test).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from backend.i18n import _

RACE_KEY = "athlete.race_result"   # user_settings: {distance_m, time_s, date, source, title?} | None
E_LOW, E_HIGH = 0.62, 0.70         # 推估 fit to Daniels' E table (see the module doc)
STALE_DAYS = 180                   # 推估: an older race no longer says much about today's E pace — not used
TRAIL_M_PER_KM = 20.0              # 推估: base_check.XU_FLAT_M_PER_KM — more climb = not a road race
DIST_RANGE_M = (1500.0, 42500.0)   # Daniels' equations: 1500 m to the marathon
PACE_RANGE_S = (150.0, 900.0)      # 推估 sanity: 2:30–15:00 /km
CANDIDATE_DAYS = 365               # how far back races are looked for
CANDIDATE_KM = (3.0, 42.5)         # 推估: 5 K to the marathon (shorter "races" are usually not races)
SOURCES = ("manual", "activity")


def vo2_of_speed(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def pct_max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(distance_m: float, time_s: float) -> Optional[float]:
    """Daniels–Gilbert VDOT; None for an impossible input."""
    if not distance_m or not time_s or distance_m <= 0 or time_s <= 0:
        return None
    t = time_s / 60.0
    return vo2_of_speed(distance_m / t) / pct_max(t)


def speed_at(vo2: float) -> float:
    """The speed (m/min) whose oxygen cost is `vo2` (the VO2 equation solved for v)."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def e_range(vd: float) -> tuple[float, float]:
    """(fast, slow) E pace in s/km for a VDOT."""
    return 60000.0 / speed_at(E_HIGH * vd), 60000.0 / speed_at(E_LOW * vd)


def fmt_pace(s_per_km: Optional[float]) -> str:
    if s_per_km is None:
        return "—"
    s = int(round(s_per_km))
    return f"{s // 60}:{s % 60:02d}"


def fmt_time(s: float) -> str:
    s = int(round(s))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def parse(v) -> Optional[dict]:
    """The stored setting → {distance_m, time_s, date, source, title}; None when unset or
    malformed (a bad distance, an impossible pace)."""
    if not isinstance(v, dict):
        return None
    try:
        d, t = float(v.get("distance_m")), float(v.get("time_s"))
        date = str(v.get("date") or "")
        dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        return None
    if not (DIST_RANGE_M[0] <= d <= DIST_RANGE_M[1]) or t <= 0:
        return None
    if not (PACE_RANGE_S[0] <= t / (d / 1000.0) <= PACE_RANGE_S[1]):
        return None
    src = v.get("source") if v.get("source") in SOURCES else "manual"
    out = {"distance_m": d, "time_s": t, "date": date, "source": src}
    if v.get("title"):
        out["title"] = str(v["title"])[:120]
    return out


def stored(user_id: int = 1) -> Optional[dict]:
    """The confirmed race (a synchronous read like load_guard.manual_start); None = not set."""
    from backend.engine.wko5expr.datasource import read_setting
    return parse(read_setting(RACE_KEY, None, user_id))


def of_race(race: Optional[dict], today: dt.date) -> Optional[dict]:
    """The E pace of a confirmed race: {vdot, e_fast, e_slow (s/km), age_days, stale, …race}."""
    r = parse(race)
    if r is None:
        return None
    vd = vdot(r["distance_m"], r["time_s"])
    if vd is None or vd <= 0:
        return None
    fast, slow = e_range(vd)
    age = (today - dt.date.fromisoformat(r["date"])).days
    return {**r, "vdot": round(vd, 1), "e_fast": round(fast), "e_slow": round(slow),
            "age_days": age, "stale": age > STALE_DAYS}


def current(today: dt.date, user_id: int = 1) -> Optional[dict]:
    """The athlete's E pace in effect (the confirmed race's), or None."""
    try:
        return of_race(stored(user_id), today)
    except Exception:              # noqa: BLE001 — no settings DB (tests, CLI): no E pace
        return None


def label(e: Optional[dict]) -> str:
    """「E 配速 5:32–6:06 /km（10 K 45:00，VDOT 45.3）」; past STALE_DAYS it says the race is too old
    and isn't used."""
    if not e:
        return ""
    km = e["distance_m"] / 1000.0
    out = _("E 配速 {fast}–{slow} /km（{km:g} K {time}，VDOT {vdot:.1f}）", fast=fmt_pace(e["e_fast"]),
            slow=fmt_pace(e["e_slow"]), km=round(km, 2), time=fmt_time(e["time_s"]), vdot=e["vdot"])
    if e.get("stale"):
        out += _("；比賽是 {d} 天前的，太舊了（超過 {n} 天，推估）：90 分鐘測試不用它，有新的比賽成績再更新",
                 d=e["age_days"], n=STALE_DAYS)
    return out


# ---- races found in the activities (offered, used only after the athlete confirms) -----------

def _is_trail(w, plan_kind: Optional[str], dist_km: float, climb_m: Optional[float]) -> bool:
    if plan_kind in ("race", "baiyue"):            # planning.Event: race = a trail race; road = road
        return True
    if "runningtrail" in (getattr(w, "tags", None) or ()):
        return True
    return climb_m is not None and dist_km > 0 and climb_m / dist_km >= TRAIL_M_PER_KM


def candidates(ds, today: dt.date, days: int = CANDIDATE_DAYS) -> list[dict]:
    """Runs of the last `days` recognised as road races (newest first): {idx, date, title,
    distance_m, time_s, vdot, e_fast, e_slow, stale}. Trail races are left out."""
    from backend.engine import threshold_confidence as TC
    from backend.engine import workout_review as WR
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    plan = getattr(ds, "plan", None)
    kinds = {}
    for e in getattr(plan, "events", None) or []:
        kinds.setdefault(e.date, e.kind)
    out = []
    for w in getattr(ds, "workouts", None) or []:
        if w.sport != "run" or not (tday - days < math.floor(w.day) <= tday):
            continue
        iso = WR._wdate(w).isoformat()
        title = WR._title(w) or ""
        kind = kinds.get(iso)
        if kind != "road" and not TC.RACE_WORDS.search(title):
            continue
        m = getattr(w, "metrics", None) or {}
        dist_km = _num(m.get("distance"))
        dur = _num(m.get("duration"))
        if not dist_km or not dur or not (CANDIDATE_KM[0] <= dist_km <= CANDIDATE_KM[1]):
            continue
        if _is_trail(w, kind, dist_km, _num(m.get("climbing"))):
            continue
        e = of_race({"distance_m": dist_km * 1000.0, "time_s": dur, "date": iso, "source": "activity",
                     "title": title}, today)
        if e is None:
            continue
        out.append({**e, "idx": w.idx})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def _num(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x
