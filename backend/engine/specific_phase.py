"""
專項期 (the 8 weeks before the taper, 賽前第 10–3 週) built on the next A race's
コース定数 (engine/panels/race_refs.py — the same numbers as the chart's target line).

1. Long days. The target is the race's single-day target (a single-day race's own
   constant, a multi-day trip's per-day average). Each week's long day aims at a share
   of it (FRAC, 推估: rising from ~50 % to 85 % at week 6 and 90 % at week 4 — the
   80–100 % band twice in weeks 6–3; 江晏慶「抓比賽距離爬升的七成」 about 1.5 months
   out, Koop: longest run 20–80 % of the race distance, CTS's biggest block 4–6 weeks
   out — back-to-back-and-long-day.md §2.2). The existing guardrails still cap it:
   ≤ +10 % over the longest of the last 30 days (load_guard.LONG_CAP, Frandsen 2025, SP-66; was
   +15 % over 4 weeks, and the 90-min floor no longer lifts it over that), the week's volume, re-entry.
   The recovery weeks are 賽前第 5、3 週 (EASY_WEEKS, FRAC's low points; SP-97): a shorter long day
   (overview.recovery_long_minutes, also ≤ LONG_CAP), never the week 6 / 4 peaks. A trail long day
   stops at TRAIL_LONG_MAX_MIN (SP-106): past ~6 h
   coaches stop the long run and cover the rest with B2B and weekly volume (iRunFar 5–6 h for
   100 mi; Koop: no magic long run — race-feasibility.md §1). The course constant is linear (1.8 h + 0.3 km + 10 climb
   km + 0.6 descent km), so N % of the race day = N % of its time, km, climb and
   descent at the athlete's own speeds (the race calculator's predicted day): the
   session says the minutes and the route that gives that constant.
2. The race GPX (engine/event_gpx.py): the longest continuous climb, the longest /
   steepest descent and the total descent → one 長爬坡反覆 session a week (an easy
   run of the week, like steep_hill.py) with the climb's grade and up to its duration,
   running down at the race's descent grade; the long day asks for the race's per-km
   climb and descent.
3. The race simulation (Koop / Uphill Athlete: rehearse kit, fuelling and pacing on
   race-like terrain) 4–3 weeks before: a SUGGESTION in the floating box (like B2B) —
   one long day at the target (still ≤ +10 %) with the race's fuelling (racepower
   fuel.py), kit and pacing notes; a multi-day trip gets two days.

主要訓練項目 = 路跑 (engine/primary_sport.py; info["sport"] = "road"): no コース定數 — the long
day follows the race DISTANCE (ROAD_FRAC of the race km, ≤ ROAD_LONG_MAX_KM, Pfitzinger's longest
runs; ≤ ROAD_LONG_MAX_MIN) at long-run pace (race pace × ROAD_EASY_SLOW, 推估); no 長爬坡反覆 /
descent session; the race simulation is a long run in race kit, fuelling and pacing on the flat.

Planner hooks (overview.week_plan, projection.project_weeks): plan_context() /
projected_context() → long_minutes() → decorate() / apply_climb(); sim_suggestion().
Climb durations use the race day's hours per effort-km (km + climb / 100, ITRA) —
推估.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from backend.engine.load_guard import LONG_CAP
from backend.i18n import N_, _

WEEKS = (3, 10)                     # 專項期 weeks before the race (planning: 8-week specific + 14-day taper)
# 推估. The low points (賽前第 5、3 週) are the 專項期's recovery weeks (EASY_WEEKS, SP-97): there the
# long day is overview.recovery_long_minutes (65 % of the usual, ≤ this share); the share stays for
# a week 5 / 3 that isn't one (the week right after another recovery-like week)
FRAC = {10: 0.50, 9: 0.55, 8: 0.60, 7: 0.70, 6: 0.85, 5: 0.70, 4: 0.90, 3: 0.70}
# SP-97: the 專項期's recovery weeks are counted back from the race, on FRAC's low points, so a
# recovery week never takes the biggest long days (weeks 6 and 4); the history-triggered 3:1 is the
# base phase's only (periodization-cross-sport.md §4.3, §6.1 SP-97)
EASY_WEEKS = (5, 3)
SIM_WEEKS = (4, 3)                  # the race simulation (推估 inside Koop's 「not the last 2–3 weeks」)
STEP = LONG_CAP                     # ≤ +10 % over the longest of the last 30 days (Frandsen 2025, SP-66)
FLOOR_MIN = 90.0                    # the 專項期 long day's old floor (never over STEP, SP-66)
TSB_MIN = -20.0                     # week_plan's 維持量 line
CLIMB_TOL_M = 30.0                  # 推估: a dip < 30 m doesn't end a continuous climb
CLIMB_MIN_GAIN = 150.0              # 推估: a race whose longest climb is < 150 m gets no climb session
STEEP_MIN_DROP = 100.0              # 推估: the steepest descent counts from 100 m of drop
UP_MAX_MIN = 60.0                   # 推估: ≤ 60 min uphill in one session
REP_MAX_MIN = 20.0                  # 推估: a repeat ≤ 20 min (a longer climb = several 20′ repeats)
DOWN_SHARE = 0.6                    # 推估: running down takes ~60 % of the way up
FLOOR_CLIMB = 45                    # 推估: under 45 min no climb session fits
FUEL_MIN_H = 4.0                    # Koop: a fuelling long run is ≥ 4 h (b2b.FUEL_MIN_H)
# 路跑 (主要訓練項目): the long run's share of the race distance per 賽前第 n 週 (推估, the same rise /
# step-back shape as FRAC), Pfitzinger's longest runs (20–22 mi ≈ 32–35 km), a 3-h ceiling (推估)
# and long-run pace = race pace × 1.15 (推估: easy long runs are 10–20 % slower than race pace)
ROAD_FRAC = {10: 0.55, 9: 0.60, 8: 0.65, 7: 0.70, 6: 0.75, 5: 0.70, 4: 0.80, 3: 0.65}
ROAD_LONG_MAX_KM = 35.0
ROAD_LONG_MAX_MIN = 180.0
TRAIL_LONG_MAX_MIN = 360.0         # SP-106 推估: iRunFar's 5–6 h for 100 mi, the upper end; past it B2B (race-feasibility.md §1)
ROAD_EASY_SLOW = 1.15
# SP-114 攻頂日模擬 (a multi-day 百岳 only; baiyue-mountaineering-training.md §1.2, §4, owner 2026-10-05): from
# 8 weeks before the trip the long day climbs the summit day's whole climb in one day with the trip's pack
# (UA〈Training for Mountaineering〉: 「8 weeks out … at least one workout per week where you ascend [the
# summit day's climb] in one day, with a backpack of approximately the same weight」). The summit day = the
# day with the most climb. Its minutes: the climb at 山本's 430 m/h up (10 % pack) and ~650 m/h down
# (research §1.4: 1,500 m ≈ 3.5 h up, 5.5–6 h in all — 推估); still ≤ STEP (load_guard.LONG_CAP, +10 % over
# the longest of the last 30 days, SP-66) — long_minutes caps it like any long day.
# Weeks 8–3 out, so the last one is ≥ 10 days before the trip (baiyue_multiday.SIM_LAST_DAYS). The
# weekday steep-hill walk (steep_hill.py) stays without a pack (UA: aerobic sessions needn't carry the
# trip's weight).
SUMMIT_SIM_WEEKS = 8
SUMMIT_UP_MPH = 430.0
SUMMIT_DOWN_MPH = 650.0
# SP-114 ME (肌耐力, muscular endurance) — a multi-day 百岳's 專項期 quality session instead of the uphill
# VO2max set (research §2.5, §4.3; UA〈Vertical Beast Mode〉, Evoke): a heavy pack up the steepest slope, the
# legs (not the breathing) the limit; once a week, 3 easy days after; the climb from 50 % of the summit
# day's to more than it (UA: 「final workouts … more vertical … than the biggest day」), one thing at a time
# — here the climb, the pack fixed at the low end of UA's 15–40 % of body weight; no ME in the last 2
# weeks (Evoke). Only when ADS (LTHR ÷ AeT − 1, both measured) ≤ 10 % (UA's condition); else the week
# keeps general strength (2 sessions). Speeds 500 m/h up with the load and 1000 m/h down light (the water
# poured out at the top) and the ME_MAX_MIN ceiling (UA podcast: ~1 h at the start, 90 min–2.5 h on a
# weekend) are 推估: a longer climb is cut to what fits.
ME_ADS_MAX = 0.10
ME_CLIMB = {10: 0.50, 9: 0.55, 8: 0.60, 7: 0.70, 6: 0.80, 5: 0.90, 4: 1.00, 3: 1.10}   # 推估 steps
ME_PACK_SHARE = 0.15
ME_PACK_DEFAULT_KG = 10.0           # 推估: no body weight → 15 % of ~65 kg
ME_UP_MPH = 500.0
ME_DOWN_MPH = 1000.0
ME_WARM_MIN = 10
ME_MAX_MIN = 150
SRC_ME = ("ME（肌耐力）：Uphill Athlete〈Vertical Beast Mode〉——背 15–40% 體重爬陡坡，腿先酸、呼吸不喘；每週一次、做完 3 天輕鬆，"
          "共 6–10 次；從攻頂日爬升的 50% 開始，最後超過它；一次只加一樣；前提 AeT 在 AnT 的 10% 以內。Evoke：最後兩週不做 ME。"
          "每週比例、背 15% 體重、上坡 350 m/h 為推估")
SRC_SUMMIT = ("攻頂日模擬：Uphill Athlete〈Training for Mountaineering〉——行程前 8 週起每週一次，一天爬完攻頂日的爬升，"
              "背和行程差不多重的背包；時間用山本正嘉背 10% 體重每小時 430 m 上、下山約 650 m/h 估（推估）")

SRC = ("單日目標＝コース定數（山本正嘉）；進度：江晏慶「抓比賽距離爬升的七成」（賽前約 1.5 個月）、"
       "Koop 最長一次 20–80% 賽事距離、CTS 賽前 4–6 週最大量；每週比例為推估")
SRC_CLIMB = ("Uphill Athlete／Koop：專項期練比賽的坡（坡度、長度）；下坡用跑的累積下坡耐受（重複負荷效應，"
             "Bontemps 2025）；次數、長度上限為推估")
SRC_ROAD = ("長跑距離進度：Pfitzinger & Douglas《Advanced Marathoning》（最長約 32–35 km）；"
            "每週比例、3 小時上限、長跑配速 ≈ 比賽配速 × 1.15 為推估")
SRC_SIM = ("Koop《Training Essentials for Ultrarunning》：賽前演練裝備、補給、配速；Uphill Athlete：在像比賽的地形演練；"
           "賽前 4–3 週、目標定數為推估")


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _r5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def weeks_out(start: dt.date, monday: dt.date) -> int:
    """賽前第 n 週 of the week starting `monday` (steep_hill.weeks_out)."""
    return -(-(start - monday).days // 7)


def easy_week(start: Optional[dt.date], monday: dt.date) -> Optional[int]:
    """賽前第 n 週 when the week of `monday` is one of the 專項期's recovery weeks (EASY_WEEKS), else
    None (SP-97). `start` = the next A race's first day."""
    if start is None:
        return None
    w = weeks_out(start, monday)
    return w if w in EASY_WEEKS else None


def frac(w: int, sport: str = "trail") -> float:
    f = ROAD_FRAC if sport == "road" else FRAC
    return f.get(w, f[WEEKS[1]] if w > WEEKS[1] else f[WEEKS[0]])


def is_road(info: Optional[dict]) -> bool:
    return (info or {}).get("sport") == "road"


def road_pace(race: dict) -> Optional[float]:
    """Long-run minutes per km: the race day's pace × ROAD_EASY_SLOW (推估)."""
    day = race.get("day") or {}
    if not day.get("km") or not day.get("hours"):
        return None
    return day["hours"] * 60.0 / day["km"] * ROAD_EASY_SLOW


def road_km(race: dict, minutes: float) -> float:
    p = road_pace(race)
    return minutes / p if p else 0.0


# ---------------------------------------------------------------------------
# the race: target, its day at the athlete's speeds, the GPX features
# ---------------------------------------------------------------------------

def legs(km: list, z: list, tol: float = CLIMB_TOL_M) -> list[dict]:
    """Continuous climbs / descents of a profile: a zig-zag filter — a leg ends when the
    elevation turns back by ≥ `tol` m (smaller dips stay inside it)."""
    n = len(z)
    if n < 2:
        return []
    out, d = [], 0
    lo = hi = piv = ext = 0

    def leg(a: int, b: int, sign: int) -> dict:
        dist = max(1e-6, float(km[b]) - float(km[a]))
        dz = abs(float(z[b]) - float(z[a]))
        return {"dir": sign, "start_km": float(km[a]), "end_km": float(km[b]), "km": dist, "dz": dz,
                "grade": dz / (dist * 1000.0) * 100.0}
    for i in range(1, n):
        if d == 0:
            if z[i] < z[lo]:
                lo = i
            if z[i] > z[hi]:
                hi = i
            if z[i] - z[lo] >= tol:
                d, piv, ext = 1, lo, i
            elif z[hi] - z[i] >= tol:
                d, piv, ext = -1, hi, i
        elif d == 1:
            if z[i] >= z[ext]:
                ext = i
            elif z[ext] - z[i] >= tol:
                out.append(leg(piv, ext, 1))
                d, piv, ext = -1, ext, i
        else:
            if z[i] <= z[ext]:
                ext = i
            elif z[i] - z[ext] >= tol:
                out.append(leg(piv, ext, -1))
                d, piv, ext = 1, ext, i
    if d != 0 and ext != piv:
        out.append(leg(piv, ext, d))
    return out


def features_of(profile: dict, totals: dict, h_per_ekm: Optional[float]) -> dict:
    """{climb, descent, steep_descent, loss_m} from a course profile; durations (min)
    at the race day's hours per effort-km."""
    ls = legs(profile.get("km") or [], profile.get("z") or [])
    ups = [x for x in ls if x["dir"] > 0]
    downs = [x for x in ls if x["dir"] < 0]

    def timed(x: Optional[dict], up: bool) -> Optional[dict]:
        if x is None:
            return None
        r = {k: round(v, 2) if isinstance(v, float) else v for k, v in x.items()}
        if h_per_ekm:
            ekm = x["km"] + (x["dz"] / 100.0 if up else 0.0)
            r["minutes"] = round(ekm * h_per_ekm * 60.0)
        return r
    climb = max(ups, key=lambda x: x["dz"], default=None)
    desc = max(downs, key=lambda x: x["dz"], default=None)
    steep = max((x for x in downs if x["dz"] >= STEEP_MIN_DROP), key=lambda x: x["grade"], default=None)
    return {"climb": timed(climb, True), "descent": timed(desc, False), "steep_descent": timed(steep, False),
            "loss_m": round(float(totals.get("loss_m") or 0.0)), "gain_m": round(float(totals.get("gain_m") or 0.0))}


def gpx_features(event_id: str, h_per_ekm: Optional[float]) -> Optional[dict]:
    """features_of() the event's stored GPX (engine/event_gpx.py), None = no GPX."""
    from backend.engine import event_gpx as EG
    from backend.engine.racepower import course as CO
    try:
        got = EG.track(event_id)
        if got is None:
            return None
        tr, row = got
        key = ("specific", event_id, row["sha1"])
        if key not in EG._memo:
            c = CO.build_course(tr, split="none")
            EG._memo[key] = (c["profile"] or {}, c["totals"])
        prof, tot = EG._memo[key]
        return {**features_of(prof, tot, h_per_ekm), "gpx": row.get("filename")}
    except Exception:                       # noqa: BLE001 — a broken file = no features
        return None


def race_day(plan, today: dt.date, predict: Optional[Callable] = None, gpx: Optional[Callable] = None) -> Optional[dict]:
    """The next A race as the 專項期 needs it: race_refs' line (calculator time when
    `predict` is given, else the plan's 預估移動時間), the single-day target, one
    average race day (hours, km, climb, descent) and the GPX features."""
    from backend.engine.panels import race_refs as RR
    try:
        evs = [e for e in RR.upcoming(plan, today) if e.priority == "A" and e.start > today]
    except Exception:                       # noqa: BLE001
        return None
    if not evs:
        return None
    e = evs[0]
    c = RR.course_of(e, gpx or RR.stored_course)
    hs = None
    if predict is not None:
        try:
            hs = predict(e, c)
        except Exception:                   # noqa: BLE001
            hs = None
    ln = RR.race_line(e, hs, "賽事計算器預測的完賽時間", c)
    if ln is None or not ln.get("hours"):
        return None
    n = max(1, int(ln["days"]))
    day = {"hours": ln["hours"] / n, "km": ln["km"] / n, "climb_m": ln["climb_m"] / n, "descent_m": ln["descent_m"] / n}
    ekm = day["km"] + day["climb_m"] / 100.0
    hpe = day["hours"] / ekm if ekm > 0 else None
    from backend.engine.race_feasibility import split_note
    summit = None
    if e.kind == "baiyue" and n > 1 and ln.get("per_day"):
        # SP-114: the summit day = the trip's day with the most climb (攻頂日模擬)
        sd = max(ln["per_day"], key=lambda d: d["climb_m"])
        summit = {"day": sd["day"], "km": sd["km"], "climb_m": sd["climb_m"], "descent_m": sd["descent_m"],
                  "hours": sd["hours"], "pack_kg": e.pack}
    return {"id": e.id, "name": e.name, "start": e.start.isoformat(), "days": n, "kind": e.kind, "summit": summit,
            "pack_kg": e.pack_kg, "goal": round(RR.goal_of(ln), 1), "cc": ln["cc"], "time_source": ln["time_source"],
            "hours": ln["hours"], "km": ln["km"], "climb_m": ln["climb_m"], "descent_m": ln["descent_m"],
            "descent_assumed": ln.get("descent_assumed"), "day": day, "h_per_ekm": hpe,
            # SP-114: the per-day numbers (a GPX's descent stays real even when the user's day plan sets the days)
            "per_day": ln.get("per_day"), "split_hint": split_note(ln),
            "features": gpx_features(e.id, hpe) if not ln.get("descent_assumed") or ln.get("gpx") else None}


# ---------------------------------------------------------------------------
# the week
# ---------------------------------------------------------------------------

def week_context(*, kind: str, mode: str, monday: dt.date, race: Optional[dict], tsb: Optional[float] = None,
                 longest28: Optional[float] = None, sport: str = "trail") -> dict:
    road = sport == "road"
    info = {"active": False, "race": race, "why": [], "src": SRC_ROAD if road else SRC, "monday": monday.isoformat(),
            "longest28": longest28, "sport": "road" if road else "trail"}
    if not race:
        info["why"].append("沒有下一場有距離的 A 賽事")
        return info
    w = weeks_out(_d(race["start"]), monday)
    info["weeks_out"] = w
    if kind != "specific" or w < WEEKS[0]:
        info["why"].append("只在專項期（賽前第 10–3 週）")
        return info
    info.update(active=True, frac=frac(w, info["sport"]), sim_week=w in SIM_WEEKS)
    if mode == "recovery_week":
        info["recovery"] = True             # SP-97: the long day is the recovery week's shorter one
    if race.get("summit") and not road and w <= SUMMIT_SIM_WEEKS and mode not in ("recovery_week", "reentry"):
        info["summit_sim"] = True                   # SP-114: this week's long day is the 攻頂日模擬
    if race.get("summit") and not road and mode not in ("recovery_week", "reentry"):
        info["me_week"] = True                      # SP-114: ME instead of the uphill VO2max set (apply_me)
    if road:
        info["climb_why"] = "主要訓練項目是路跑：不排長爬坡、下坡"
        return info
    f = race.get("features") or {}
    cl = f.get("climb") or {}
    if mode in ("recovery_week", "reentry"):
        info["climb_why"] = "恢復週／停訓後恢復期：不排長爬坡"
    elif tsb is not None and tsb < TSB_MIN:
        info["climb_why"] = f"週初 TSB {tsb:+.0f} < {TSB_MIN:.0f}：這週不排長爬坡"
    elif (cl.get("dz") or 0) < CLIMB_MIN_GAIN:
        info["climb_why"] = "賽事沒有 GPX，或最長的爬坡不到 150 m"
    else:
        info["climb"] = True
    return info


def plan_context(status, today: dt.date, monday: dt.date, mode: str, tsb: Optional[float],
                 longest28: Optional[float], predict: Optional[Callable] = None, sport: str = "trail") -> dict:
    """week_context() from week_plan()'s data. Never raises."""
    try:
        race = race_day(status.plan, today, predict)
        return week_context(kind=status.kind or "base", mode=mode, monday=monday, race=race, tsb=tsb,
                            longest28=longest28, sport=sport)
    except Exception as e:                  # noqa: BLE001 — the plan must still build
        return {"active": False, "error": type(e).__name__}


def projected_context(kind: str, mode: str, monday: dt.date, cur: Optional[dict]) -> dict:
    cur = cur or {}
    return week_context(kind=kind, mode=mode, monday=monday, race=cur.get("race"), sport=cur.get("sport") or "trail")


PUBLIC = ("active", "race", "weeks_out", "frac", "sim_week", "climb", "climb_why", "why", "src", "longest28",
          "long", "planned", "error", "sport", "recovery", "summit_sim", "me_week", "me")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}


def long_minutes(info: dict, longest: float) -> Optional[float]:
    """This week's long day: frac × the race day's time, ≤ +10 % over `longest` (the
    longest of the last 30 days); not below min(90 min, the aim) unless the +10 % says so (SP-66:
    the floor used to lift the day over the cap)."""
    if not info or not info.get("active"):
        return None
    want = trail_aim(info)[0]
    if info.get("summit_sim"):
        want = summit_minutes(info["race"]["summit"])       # SP-114: the whole summit-day climb, no 6-h cap
    if is_road(info):
        # 路跑: frac × the race distance (≤ 35 km) at long-run pace, ≤ 3 h
        p = road_pace(info["race"])
        if not p:
            return None
        want = min(min(info["frac"] * info["race"]["day"]["km"], ROAD_LONG_MAX_KM) * p, ROAD_LONG_MAX_MIN)
    cap = max(float(longest or 0.0), 60.0) * STEP
    return min(max(min(FLOOR_MIN, want), min(want, cap)), cap)


def trail_aim(info: dict) -> tuple[float, bool]:
    """(this week's trail long-day aim in minutes, whether TRAIL_LONG_MAX_MIN cut it): frac × the
    race day's time, ≤ the cap (SP-106)."""
    want = info["frac"] * info["race"]["day"]["hours"] * 60.0
    return min(want, TRAIL_LONG_MAX_MIN), want > TRAIL_LONG_MAX_MIN


def route(race: dict, minutes: float) -> dict:
    """The share of the race day `minutes` is, and the route that gives that constant."""
    day = race["day"]
    f = minutes / (day["hours"] * 60.0) if day["hours"] else 0.0
    km = f * day["km"]
    return {"f": f, "cc": f * race["goal"], "km": km, "climb_m": f * day["climb_m"], "descent_m": f * day["descent_m"],
            "climb_per_km": day["climb_m"] / day["km"] if day["km"] else 0.0,
            "descent_per_km": day["descent_m"] / day["km"] if day["km"] else 0.0}


def route_text(race: dict, minutes: float, capped: bool = False, at_max: bool = False) -> str:
    r = route(race, minutes)
    s = (f"這次目標定數約 {r['cc']:.0f}（單日目標的 {r['f'] * 100:.0f}%）：約 {r['km']:.0f} km、"
         f"↑{r['climb_m']:.0f} ↓{r['descent_m']:.0f} m 的路線")
    if r["climb_per_km"] >= 10:
        s += f"（每公里 ↑{r['climb_per_km']:.0f} ↓{r['descent_per_km']:.0f} m，像{race['name']}）"
    if capped:
        s += "；受「每次最多 +10%」限制"
    elif at_max:
        s += _("；長天上限 {h:g} 小時：比賽更長的部分交給 B2B 和週量", h=TRAIL_LONG_MAX_MIN / 60)
    return s


_OLD_TERRAIN = ("挑每公里爬升", "有山路就走山路", "平路或緩坡")


def decorate(ss: list[dict], info: Optional[dict]) -> None:
    """The long day's 「這次目標定數約 N（單日目標的 X%）」 + route (in place), after the
    課表偏好 shaping / B2B texts (they may cap or rename it)."""
    if not info or not info.get("active"):
        return
    s = next((x for x in ss if x.get("id") == "long" and not x.get("done")), None)
    if s is None or not s.get("minutes"):
        return
    race = info["race"]
    m = float(s["minutes"])
    if is_road(info):
        # 路跑: 「這次約 N km（賽事距離的 X%）」 — no コース定數, no climb
        km = road_km(race, m)
        want = long_minutes(info, 1e9) or m
        pct = km / race["day"]["km"] * 100 if race["day"].get("km") else 0
        s["detail"] = "；".join([f"這次約 {km:.0f} km（賽事距離的 {pct:.0f}%）"
                                + ("；受「每次最多 +10%」限制" if want > m + 5 and not info.get("recovery") else "")]
                               + [p for p in (s.get("detail") or "").split("；") if p])
        s["distance_km"] = round(km, 1)
        s["source"] = ((s.get("source") or "") + "；" + SRC_ROAD).lstrip("；")
        info["long"] = {"minutes": int(m), "pct": round(pct), "km": round(km, 1)}
        return
    if info.get("summit_sim"):
        summit_session(s, info, m)
        return
    want, cut = trail_aim(info)
    r = route(race, m)
    parts = [p for p in (s.get("detail") or "").split("；") if p and not p.startswith(_OLD_TERRAIN)]
    rec = bool(info.get("recovery"))        # SP-97: shorter on purpose, not the +10 % cap (SP-66)
    s["detail"] = "；".join([route_text(race, m, want > m + 5 and not rec, cut and m >= want - 5 and not rec)] + parts)
    s["distance_km"] = round(r["km"], 1)
    s["climb_m"] = round(r["climb_m"])
    s["source"] = ((s.get("source") or "") + "；" + SRC).lstrip("；")
    info["long"] = {"minutes": int(m), "cc": round(r["cc"], 1), "pct": round(r["f"] * 100), "km": round(r["km"], 1),
                    "climb_m": round(r["climb_m"]), "descent_m": round(r["descent_m"])}


def summit_minutes(summit: dict) -> float:
    """The minutes of a 攻頂日模擬 climbing the whole summit-day climb (up and down, 推估)."""
    cl = float(summit.get("climb_m") or 0.0)
    return cl / SUMMIT_UP_MPH * 60.0 + cl / SUMMIT_DOWN_MPH * 60.0


def summit_session(s: dict, info: dict, minutes: float) -> None:
    """The long day as the 攻頂日模擬 (SP-114, in place): the summit day's climb (a share of it when
    the +10 % single-run cap (STEP, SP-66) or the week's volume shortened the day), the trip's pack,
    the plain how-to."""
    race = info["race"]
    sm = race["summit"]
    full = summit_minutes(sm)
    f = min(1.0, minutes / full) if full else 1.0
    climb = f * float(sm["climb_m"])
    parts = [p for p in (s.get("detail") or "").split("；") if p and not p.startswith(_OLD_TERRAIN)]
    head = _("一天爬升約 {cl:.0f} m（攻頂日＝第 {d} 天 {need:.0f} m 的 {p:.0f}%），背 {kg:g} kg 的背包（行程背包），"
             "爬上去再下來", cl=climb, d=sm["day"], need=sm["climb_m"], p=f * 100, kg=round(float(sm["pack_kg"]), 1))
    if f < 0.95:
        head += _("；受「每次最多 +10%」和週量限制，這次先爬到 {p:.0f}%，之後每週加一點", p=f * 100)
    tail = _("做完在活動頁記下這次背多少，賽事評估才會算這次（爬升到攻頂日的 100% 而且有背包，3 次算夠）")
    s.update(title=_("攻頂日模擬｜{name}", name=race["name"]), detail="；".join([head] + parts + [tail]),
             climb_m=round(climb), terrain="trail",
             source=((s.get("source") or "") + "；" + SRC_SUMMIT).lstrip("；"))
    info["long"] = {"minutes": int(minutes), "summit_sim": True, "climb_m": round(climb), "pct": round(f * 100),
                    "pack_kg": sm["pack_kg"], "need_m": round(sm["climb_m"])}


def walk_targets(ss: list[dict], walk: Optional[dict], aet: Optional[float] = None,
                 aet_measured: bool = False) -> None:
    """SP-115 × SP-114, in place: the 攻頂日模擬 and the ME session are walking sessions
    (target_policy.is_walk) — their target is the uphill cap (hr_profile.walk_cap_hr: 75 % HRmax or
    RPE ≤ 13, never below the easy-run cap), and the long day's 「全程心率壓在輕鬆跑上限以下」 becomes the
    uphill cap with 「下坡看腿的感覺」."""
    from backend.engine import hr_profile as HP
    from backend.engine.target_policy import is_walk
    for s in ss:
        if s.get("done") or s.get("id") not in ("long", "me") or not is_walk(s):
            continue
        hr = HP.walk_cap_hr(walk, aet, aet_measured)
        s["target"] = hr
        up = _("上坡{hr}；{down}", hr=hr, down=_(HP.WALK_DOWN))
        det = s.get("detail") or ""
        if up in det:
            continue                                        # already done (a projected week re-run)
        parts = [p for p in det.split("；") if p]
        if any(p.startswith("全程心率壓在") for p in parts):
            parts = [up if p.startswith("全程心率壓在") else p for p in parts]
        else:
            parts.append(up)
        s["detail"] = "；".join(parts)


def _hill_set(s: dict) -> bool:
    """The 專項期's uphill VO2max set: the Zone 5 rung's uphill version (SP-75), or the old fixed 5×4′
    (overview.TRAIL_SPECIFIC_Z5) of a stored week."""
    t = str(s.get("title") or "")
    return s.get("kind") == "quality" and t.startswith("VO2max 間歇") and "上坡" in t


def me_session(info: dict, weight_kg: Optional[float], rate: float = 60.0) -> dict:
    """This week's ME session (SP-114): ME_CLIMB of the summit day's climb, a pack of ME_PACK_SHARE of
    the body weight (ME_PACK_DEFAULT_KG without one)."""
    sm = info["race"]["summit"]
    f = ME_CLIMB.get(int(info.get("weeks_out") or WEEKS[0]), ME_CLIMB[WEEKS[1]])
    per_m = 60.0 / ME_UP_MPH + 60.0 / ME_DOWN_MPH                 # minutes per metre up and back down
    want = f * float(sm["climb_m"])
    climb = int(round(min(want, (ME_MAX_MIN - ME_WARM_MIN - 5) / per_m) / 10.0) * 10)
    kg = round(ME_PACK_SHARE * weight_kg) if weight_kg else ME_PACK_DEFAULT_KG
    m = _r5(ME_WARM_MIN + climb * per_m + 5)
    detail = (_("背 {kg:g} kg（體重的 {p:.0f}%）", kg=kg, p=ME_PACK_SHARE * 100) if weight_kg
              else _("背 {kg:g} kg（沒有體重紀錄，先用這個重量）", kg=kg))
    detail += _("爬最陡的坡、樓梯或跑步機最大坡度，共爬升約 {cl:.0f} m（攻頂日 {need:.0f} m 的 {f:.0f}%）；"
                "要腿先酸、呼吸不喘，心率不必拉高；背水上山、到頂把水倒掉輕裝下山，保護膝蓋；暖身 10 分。做完 3 天只排輕鬆",
                cl=climb, need=sm["climb_m"], f=climb / float(sm["climb_m"]) * 100 if sm["climb_m"] else 0)
    if climb < want - 10:
        detail += _("（這週的目標是 {w:.0f} m，一次最多排 {m} 分鐘，先爬到這裡）", w=want, m=ME_MAX_MIN)
    return {"id": "me", "kind": "quality", "title": _("ME 負重爬坡（爬升 {cl:.0f} m、背 {kg:g} kg）", cl=climb, kg=kg),
            "minutes": int(m), "target": "", "detail": detail, "source": SRC_ME, "terrain": "trail",
            "tss": round(rate * m / 60.0, 1), "climb_m": climb, "day": None, "done": False, "done_by": None}


def _move_minutes(ss: list[dict], delta: int, skip) -> None:
    """Take `delta` minutes from the week's easy runs (largest first, ≥ 20 each), or give −delta to
    the largest one — the week's total stays (as apply_climb)."""
    easy = sorted((x for x in ss if x is not skip and x.get("kind") == "easy" and not x.get("done")
                   and x.get("minutes")), key=lambda x: -(x.get("minutes") or 0))
    if delta < 0 and easy:
        x = easy[0]
        r = (x.get("tss") or 0.0) / x["minutes"]
        x["minutes"] = int(x["minutes"]) - delta
        x["tss"] = round(r * x["minutes"], 1)
        return
    for x in easy:
        if delta <= 0:
            break
        r = (x.get("tss") or 0.0) / x["minutes"]
        new = max(20, int(x["minutes"]) - delta)
        delta -= int(x["minutes"]) - new
        x["minutes"], x["tss"] = new, round(r * new, 1)


def _me_day(ss: list[dict], info: dict) -> Optional[str]:
    """A day for an added ME session in an already placed week (the projection): the earliest day ≥ 3
    days before the long day (UA: 3 easy days after ME, so before the simulation) with no other hard
    session; None in an unplaced week (week_plan places it later) or when no day fits."""
    if not any(s.get("day") for s in ss):
        return None
    mon = _d(info.get("monday"))
    hard = {s["day"] for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "long")
                                                       or s.get("id") in ("steep", "climb", "tech"))}
    long_day = next((_d(s["day"]) for s in ss if s.get("id") == "long" and s.get("day")), None)
    if mon is None:
        return None
    days = [mon + dt.timedelta(days=i) for i in range(7)]
    ok = [d for d in days if d.isoformat() not in hard
          and not (long_day is not None and d <= long_day and (long_day - d).days < 3)]
    # a day away from the other hard sessions first (≥ 2 days, 台灣教練's 48 h), else any free one
    apart = [d for d in ok if all(abs((d - _d(h)).days) >= 2 for h in hard)]
    pick = (apart or ok or [None])[0]
    return pick.isoformat() if pick else None


def apply_me(ss: list[dict], info: Optional[dict], gap: Optional[float], weight_kg: Optional[float] = None,
             allow: bool = True, rate: float = 60.0, notes: Optional[list] = None) -> list[dict]:
    """A multi-day 百岳's 專項期 (SP-114), in place: with ADS (`gap` = LTHR ÷ AeT − 1, both measured) ≤
    ME_ADS_MAX the uphill VO2max set becomes the week's ME session (added when there was none);
    otherwise the set goes, its minutes to the easy runs, and the week keeps general strength (2
    sessions) with a note. `allow` = the week may have a hard session (the gate's guardrails, the
    days after a B2B)."""
    race = (info or {}).get("race") or {}
    if not info or not info.get("active") or not info.get("me_week") or not race.get("summit") or is_road(info):
        return ss
    if any(s.get("id") == "me" for s in ss):
        return ss
    hill = next((s for s in ss if _hill_set(s) and not s.get("done")), None)
    if gap is not None and gap <= ME_ADS_MAX:
        if not allow:
            return ss
        me = me_session(info, weight_kg, rate)
        if hill is not None:
            me["day"] = hill.get("day")             # a placed week (the projection) keeps the day
            ss[ss.index(hill)] = me
            _move_minutes(ss, me["minutes"] - int(hill.get("minutes") or 0), me)
        else:
            me["day"] = _me_day(ss, info)
            ss.append(me)
            _move_minutes(ss, me["minutes"], me)
        info["me"] = {"minutes": me["minutes"], "climb_m": me["climb_m"]}
        return ss
    if hill is not None:
        ss.remove(hill)
        _move_minutes(ss, -int(hill.get("minutes") or 0), None)
    st = [s for s in ss if s.get("kind") == "strength"]
    if st and len(st) < 2:
        ss.insert(ss.index(st[-1]) + 1, {**st[-1], "id": f"strength{len(st) + 1}", "day": None, "done": False,
                                          "done_by": None})
    why = (_("你的 ADS（LTHR ÷ AeT − 1）是 {g:.0f}%", g=gap * 100) if gap is not None
           else _("還沒有實測的 AeT 和 LTHR，算不出 ADS"))
    if notes is not None:
        notes.append({"level": "info", "src": "specific",
                      "text": _("多日百岳的專項期把爬坡間歇換成 ME（背重爬陡坡），前提是 ADS ≤ 10%（Uphill Athlete）；{why}，"
                                "這週維持一般肌力（每週 2 次）", why=why)})
    info["me"] = {"skipped": True, "gap": gap}
    return ss


# ---------------------------------------------------------------------------
# 長爬坡反覆 (one easy run of the week)
# ---------------------------------------------------------------------------

def climb_shape(feat: dict) -> Optional[dict]:
    """Repeats of the race's longest climb: ≤ 20′ each, ≤ 60′ uphill (推估)."""
    cl = feat.get("climb") or {}
    if (cl.get("dz") or 0) < CLIMB_MIN_GAIN:
        return None
    cm = float(cl.get("minutes") or 20.0)
    rep = max(5, _r5(min(cm, REP_MAX_MIN)))
    # at least ~40′ uphill (a short climb = more repeats), at most 60′ (推估)
    n = max(2, min(int(UP_MAX_MIN // rep), int(round(max(cm, 40.0) / rep))))
    return {"rep": rep, "n": n, "grade": cl["grade"], "climb_dz": cl["dz"], "climb_min": cl.get("minutes")}


# SP-227: how to climb it in the race, by runwalk.gait (grade × the predicted climbing speed, SP-226)
CLIMB_GAIT_DO = {"walk": N_("用快走"), "either": N_("走跑皆可：跑或快走，哪個輕鬆用哪個"), "run": N_("用跑的")}


def race_gait(feat: Optional[dict]) -> Optional[dict]:
    """The race's longest climb as the race will go (SP-227): its climbing rate (m/h) at the
    race day's hours per effort-km — the same estimate as 「你的速度約 N 分」 — and
    runwalk.gait on its grade × that speed (docs/research/run-walk-threshold.md §5.4). None
    without the climb's minutes (no race time) or below 3 %. The default curve (推估)."""
    from backend.engine.racepower import runwalk as RW
    cl = (feat or {}).get("climb") or {}
    mins, km, dz, g = cl.get("minutes"), cl.get("km"), cl.get("dz"), cl.get("grade")
    if not mins or mins <= 0 or not km or not dz or g is None:
        return None
    v = float(km) * 1000.0 / (float(mins) * 60.0)
    gait = RW.gait(float(g) / 100.0, v)
    if gait is None:
        return None
    return {"gait": gait, "vam": float(dz) / float(mins) * 60.0, "grade": float(g), "speed_ms": v}


def climb_minutes(sh: dict, n: Optional[int] = None) -> int:
    n = sh["n"] if n is None else n
    return _r5(15 + n * sh["rep"] * (1 + DOWN_SHARE) + 10)


def climb_text(race: dict, sh: dict, n: int, aet: Optional[float],
               aet_measured: bool = False) -> tuple[str, str, str]:
    """`aet` = the easy-run cap (hr_profile.easy_cap_label)."""
    from backend.engine.hr_profile import easy_cap_label
    f = race.get("features") or {}
    g = sh["grade"]
    dn = f.get("steep_descent") or f.get("descent") or {}
    hr = _("心率約{cap}", cap=easy_cap_label(None, aet, aet_measured))
    when = _("、你的速度約 {m:.0f} 分", m=sh["climb_min"]) if sh.get("climb_min") else ""
    title = _("長爬坡反覆 {n}×{rep} 分（{g:.0f}% 坡）", n=n, rep=sh["rep"], g=g)
    down = (_("下坡用跑的：找接近 {g:.0f}% 的坡（賽道最陡的長下坡 ↓{dz:.0f} m；整場下降 {loss:.0f} m）",
              g=dn["grade"], dz=dn["dz"], loss=f.get("loss_m", 0))
            if dn else _("下坡用跑的，練下坡"))
    kw = dict(name=race["name"], dz=sh["climb_dz"], g=g, when=when, lo=max(0, g - 2), hi=g + 2, rep=sh["rep"], n=n,
              hr=hr, down=down)
    rg = race_gait(f)
    if rg is None:
        # no race time or a climb under 3 %: the text as before SP-227
        detail = _("{name}最長的爬坡 ↑{dz:.0f} m、平均 {g:.0f}%{when}：找 {lo:.0f}–{hi:.0f}% 的坡，"
                   "上坡 {rep} 分 × {n} 趟，用比賽的走／跑方式、{hr}；{down}。暖身 15 分、緩和 10 分", **kw)
    else:
        # SP-227: the race's gait on this climb (runwalk.gait), e.g. 「比賽時這段每小時約 620 m、18%：用快走」
        how = _("比賽時這段每小時約 {vam:.0f} m、{g:.0f}%：{act}", vam=round(rg["vam"], -1), g=g,
                act=_(CLIMB_GAIT_DO[rg["gait"]]))
        detail = _("{name}最長的爬坡 ↑{dz:.0f} m、平均 {g:.0f}%{when}：找 {lo:.0f}–{hi:.0f}% 的坡，"
                   "上坡 {rep} 分 × {n} 趟、{hr}；{how}；{down}。暖身 15 分、緩和 10 分", how=how, **kw)
    return title, detail, hr


def apply_climb(ss: list[dict], info: Optional[dict], *, aet: Optional[float] = None, prefs=None,
                b2b: Optional[dict] = None, notes: Optional[list] = None, rates: Optional[dict] = None,
                aet_measured: bool = False) -> list[dict]:
    """Turn one easy run into 長爬坡反覆 (in place; the week's easy minutes unchanged):
    never within a day of the long day / quality, the easy days after a B2B, or the last
    7 days before the race; a weekday only when it fits the weekday cap."""
    if not info or not info.get("active") or not info.get("climb") or is_road(info):
        return ss
    race = info["race"]
    sh = climb_shape(race.get("features") or {})
    if sh is None or any(s.get("id") == "climb" for s in ss):
        return ss
    start = _d(race["start"])
    hard = [_d(s["day"]) for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "race")
                                                         or s.get("id") in ("long", "long2", "steep"))]
    post = (b2b or {}).get("post") or {}
    until = _d(post.get("until")) if post else None
    cap = getattr(prefs, "cap_weekday", None) if prefs is not None and getattr(prefs, "active", False) else None
    cands = []
    for s in ss:
        if s.get("kind") != "easy" or s.get("done") or not s.get("day") or s.get("id") in ("steep",) or s.get("heat"):
            continue
        d = _d(s["day"])
        if (start - d).days <= 7 or any(abs((d - h).days) < 2 for h in hard) or (until is not None and d <= until):
            continue
        n = sh["n"]
        lim = None if d.weekday() >= 5 else cap
        while lim is not None and n > 2 and climb_minutes(sh, n) > lim:
            n -= 1
        m = climb_minutes(sh, n)
        if (lim is not None and m > lim) or m < FLOOR_CLIMB:
            continue
        cands.append((d.weekday() >= 5, d, s, n, m))
    if not cands:
        if notes is not None:
            notes.append({"level": "info", "src": "climb", "text": "這週沒有可以放長爬坡反覆的輕鬆日（長天、強度課的前後一天不放）"})
        return ss
    cands.sort(key=lambda c: (c[0], c[1]))
    _, d, s, n, m = cands[0]
    delta = m - int(s.get("minutes") or 0)
    for x in sorted((x for x in ss if x is not s and x.get("kind") == "easy" and not x.get("done")
                     and x.get("id") != "steep"), key=lambda x: -(x.get("minutes") or 0)):
        if delta <= 0:
            break
        r = (x.get("tss") or 0.0) / x["minutes"] if x.get("minutes") else 0.0
        new = max(20, int(x["minutes"]) - delta)
        delta -= int(x["minutes"]) - new
        x["minutes"], x["tss"] = new, round(r * new, 1)
    rate = (rates or {}).get("trail") or (rates or {}).get("road") or 55.0
    title, detail, hr = climb_text(race, sh, n, aet, aet_measured)
    s.update(id="climb", kind="easy", terrain="trail", minutes=int(m), title=title, detail=detail, source=SRC_CLIMB,
             tss=round(rate * 1.1 * m / 60.0, 1), target=hr,
             climb_m=round(n * sh["rep"] / sh["climb_min"] * sh["climb_dz"]) if sh.get("climb_min") else None)
    rg = race_gait(race.get("features"))
    info["planned"] = [{"day": s["day"], "minutes": m, "n": n, "rep": sh["rep"], "grade": round(sh["grade"], 1),
                        "gait": rg["gait"] if rg else None, "vam": round(rg["vam"]) if rg else None}]
    return ss


# ---------------------------------------------------------------------------
# the race simulation (a suggestion)
# ---------------------------------------------------------------------------

def sim_weeks(start: dt.date) -> list[dt.date]:
    """The Mondays of 賽前第 4 and 3 週."""
    rm = start - dt.timedelta(days=start.weekday())
    return sorted(m for m in (rm - dt.timedelta(weeks=k) for k in range(8)) if weeks_out(start, m) in SIM_WEEKS)


def fuel_text(race: dict) -> str:
    """The race's fuelling (racepower fuel.py §3.2 / §4.2 rows) at the race's class."""
    from backend.engine.racepower import fuel as F
    n = max(1, int(race.get("days") or 1))
    cls = F.event_class(race.get("kind") or "race", race["hours"] / n, race["km"] / n)
    c = F.carb_target(cls)
    s = f"每小時 {c['lo']:.0f}–{c['hi']:.0f} g 醣"
    if c.get("every_min"):
        s += f"（每 {c['every_min'][0]}–{c['every_min'][1]} 分吃一次）"
    w = F.water_band(cls, None, False)
    if w:
        s += f"、水 {w[0]:.0f}–{w[1]:.0f} ml/h"
    na = F.sodium_band(cls, None, False)
    if na and na[1] > 0:
        s += f"、鈉 {na[0]:.0f}–{na[1]:.0f} mg/h"
    return s


def sim_sessions(race: dict, minutes: list[int], aet: Optional[float], rate: float,
                 sport: str = "trail", aet_measured: bool = False) -> list[dict]:
    """The simulation day(s) as the user's sessions. `aet` = the easy-run cap (hr_profile)."""
    from backend.engine.hr_profile import easy_cap_hr
    hr = easy_cap_hr(aet, aet_measured)
    if sport == "road":
        # 路跑: a long run in race kit, fuelling and pacing, on the flat
        out = []
        for m in minutes:
            km = road_km(race, m)
            out.append({"kind": "long", "title": f"賽事模擬｜{race['name']}", "minutes": int(m), "target": hr,
                        "detail": (f"約 {km:.0f} km 平路。穿比賽的鞋、衣褲，早餐、補給照比賽：{fuel_text(race)}；"
                                   f"前段輕鬆，中後段照比賽計畫的配速跑一段，{hr}（比賽配速那段除外）"),
                        "source": SRC_SIM, "terrain": "road", "tss": round(rate * m / 60.0, 1),
                        "distance_km": round(km, 1)})
        return out
    multi = int(race.get("days") or 1) > 1
    kit = ("背行程的背包和裝備（演練打包、重量照行程）" if multi or race.get("kind") == "baiyue"
           else "穿比賽的鞋、衣褲、背心，帶強制裝備（頭燈、雨衣…）")
    out = []
    for i, m in enumerate(minutes, 1):
        head = route_text(race, m)
        r = route(race, m)
        tail = ("" if not multi else ("第 1 天，" if i == 1 else "第 2 天：接續的一天，同樣的背包，"))
        detail = (f"{head}。{tail}{kit}；補給照比賽：{fuel_text(race)}，早餐也照比賽吃；"
                  f"配速照比賽計畫，前段刻意保守，爬坡照比賽的走／跑方式，{hr}")
        if i == 1 and m < FUEL_MIN_H * 60:
            detail += f"（Koop：練補給至少 {FUEL_MIN_H:.0f} 小時，這次先練到 {m} 分）"
        out.append({"kind": "long", "title": f"賽事模擬｜{race['name']}" + (f" 第 {i} 天" if multi else ""),
                    "minutes": int(m), "target": hr, "detail": detail, "source": SRC_SIM, "terrain": "trail",
                    "tss": round(rate * m / 60.0, 1), "distance_km": round(r["km"], 1), "climb_m": round(r["climb_m"])})
    return out


def sim_day_options(sg: dict, first: dt.date, blocked=frozenset(), allowed: Optional[Callable] = None,
                    weekday_cap: Optional[int] = None, busy_for: Optional[Callable] = None) -> list[dict]:
    """The days of the simulation's two weeks it can take (a day pair for a trip:
    b2b.pair_options): from `first`, not blocked, allowed by 課表偏好, a weekday only
    when the minutes fit its cap, not a day of the user's own hard / long sessions
    (`busy_for(week ISO)` → set of ISO days). Weekends first."""
    from backend.engine import b2b as B2B
    out = []
    for w in sg.get("weeks") or []:
        m = _d(w)
        busy = busy_for(w) if busy_for else set()
        if sg.get("multi"):
            out += B2B.pair_options(m, first, sg["minutes"], blocked, allowed, weekday_cap, busy)
            continue
        for i in range(7):
            d = m + dt.timedelta(days=i)
            iso = d.isoformat()
            if d < first or iso in blocked or iso in busy or (allowed is not None and not allowed(d)):
                continue
            if d.weekday() < 5 and weekday_cap is not None and sg["minutes"][0] > weekday_cap:
                continue
            out.append({"day": iso, "label": f"{d.month}/{d.day}（{B2B.wd(d)}）",
                        "note": "週末" if d.weekday() >= 5 else "平日"})
    if not sg.get("multi"):
        out.sort(key=lambda o: (o["note"] != "週末", o["day"]))
    return out


def sim_suggestion(info: Optional[dict], monday: dt.date, longest: float, aet: Optional[float] = None,
                   rate: float = 55.0, aet_measured: bool = False) -> Optional[dict]:
    """The race simulation, suggested from the week before its window (this week or the
    next) until the window ends: {id, type race_sim, weeks, minutes, sessions, pick…}.
    `longest`: the longest of the last 30 days / this week's long day (the +10 % cap)."""
    race = (info or {}).get("race")
    if not race or not info.get("active"):
        return None
    start = _d(race["start"])
    weeks = sim_weeks(start)
    if not weeks or monday > weeks[-1] or weeks[0] > monday + dt.timedelta(days=7):
        return None
    day_min = race["day"]["hours"] * 60.0
    cap = max(float(longest or 0.0), 60.0) * STEP
    d1 = _r5(min(day_min, cap))
    ws = "、".join(f"{w.month}/{w.day}" for w in weeks)
    if is_road(info):
        # 路跑: never the whole race — a long run at the 專項期 ceiling (Pfitzinger's longest)
        d1 = _r5(min(day_min, cap, long_minutes({**info, "frac": ROAD_FRAC[4]}, 1e9) or day_min))
        km = road_km(race, d1)
        return {"id": f"race_sim:{race['id']}", "type": "race_sim", "event_id": race["id"], "event": race["name"],
                "weeks": [w.isoformat() for w in weeks], "minutes": [d1], "multi": False,
                "title": f"建議做一次賽事模擬：{race['name']}，{d1} 分（約 {km:.0f} km）",
                "reason": f"賽前第 4–3 週（{ws} 那兩週）：穿比賽的鞋和衣服、照比賽吃、照比賽計畫配速跑一次長跑",
                "help": ("Koop、Pfitzinger：賽前把比賽日的裝備、早餐、補給和配速演練一次，問題在比賽前就發現；"
                         f"路跑不跑全程，長度是專項期最長的那一次，一樣守「每次最多 +10%」。補給：{fuel_text(race)}。"
                         "選一天按「排入」才會進課表；不排也不影響其他課。時間點與長度為推估。"),
                "src": SRC_SIM, "sessions": sim_sessions(race, [d1], aet, rate, "road", aet_measured)}
    long_day = day_min > TRAIL_LONG_MAX_MIN
    d1 = min(d1, _r5(TRAIL_LONG_MAX_MIN))       # SP-106: the simulation stops at the long-day cap too
    multi = int(race.get("days") or 1) > 1
    from backend.engine.b2b import DAY2_RATIO, MIN_DAY2
    mins = [d1] + ([max(MIN_DAY2, _r5(DAY2_RATIO * d1))] if multi else [])
    r = route(race, d1)
    return {"id": f"race_sim:{race['id']}", "type": "race_sim", "event_id": race["id"], "event": race["name"],
            "weeks": [w.isoformat() for w in weeks], "minutes": mins, "multi": multi,
            "title": (f"建議做一次賽事模擬：{race['name']}，{'連續兩天，' if multi else ''}{d1} 分"
                      f"（定數約 {r['cc']:.0f}＝單日目標的 {r['f'] * 100:.0f}%）"),
            "reason": f"賽前第 4–3 週（{ws} 那兩週）：用比賽的裝備、補給、配速，在像比賽的地形跑一次",
            "help": ("Koop、Uphill Athlete：賽前在像比賽的地形演練裝備、補給和配速，問題在比賽前就發現。"
                     f"這天換掉那週的長天；長度一樣守「每次最多 +10%」。補給：{fuel_text(race)}。"
                     + ("多日行程做連續兩天，第 2 天約第 1 天的 2/3（CTS 30:20）。" if multi else "")
                     + (_("比賽單日超過 {h:g} 小時：模擬只到長天上限，更長的部分交給 B2B。",
                          h=TRAIL_LONG_MAX_MIN / 60) if long_day else "")
                     + "選一天按「排入」才會進課表；不排也不影響其他課。時間點與目標比例為推估。"),
            "src": SRC_SIM, "sessions": sim_sessions(race, mins, aet, rate, aet_measured=aet_measured)}
