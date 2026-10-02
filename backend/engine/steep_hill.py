"""
陡坡健走（模擬負重）before a 百岳 / multi-day trip — replaces the loaded-carry
sessions (no pack in training; docs/research/loaded-carry-training.md §1.1).

Why no pack: for heart and lungs a 9 kg pack is about 9 kg more body mass —
walking metabolic rate scales with total mass (Ludlow & Weyand 2017) — so the
aerobic part is trained by walking STEEPER without one (doc §1.1, summary 1).
The grade that costs what the pack would is Pandolf, Givoni & Goldman 1977
(racepower/hike.pandolf):

    M(W, L, V, G) = M(W, 0, V, G′)  →  G′ = the grade without the pack

at the doc's machine session: 3.5 km/h on a 12 % treadmill (doc §3.2: 12–15 %,
3.5 km/h × 15 % × 35′ ≈ 300 m). Above the 15 % most treadmills top out at, the
speed rises instead (same Pandolf equation, solved for V). The pack per stage
is the old progression (UA trekking: 5 % → 10 % of body weight → the trip pack,
≈ 2 weeks each; weeks 10–9 / 8–7 / 6–3 before the trip, 推估).

Planner hook (overview.week_plan, projection.project_weeks), like b2b.py:
plan_context() / projected_context() → apply() turns one weekday easy run of a
專項期 week into the 40–50 min session (the week's easy minutes unchanged),
never within a day of the long day / quality, never a recovery / re-entry week,
not when the week starts at TSB < −20, nothing in the last 7 days before the
trip. Intensity HR ≤ AeT (UA: an aerobic session) — target_policy gives trail
sessions HR.

What the doc says a pack alone trains (hip extensors, trunk, feet, loaded
descents) is not simulated here; the long days and strength sessions carry on
as before.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

STAGE_PCT = (0.05, 0.10)          # UA trekking: 5 %, 10 % of body weight; stage 3 = the trip pack
STAGE_WEEKS = ((9, 1), (7, 2), (0, 3))   # w ≥ 9 → 1, 7–8 → 2, else 3 (推估)
WEEKS = (3, 10)                   # 專項期 weeks before the trip (doc §2.1)
BASE_GRADE = 12.0                 # % — the doc's machine session (§3.2: 12–15 %)
BASE_KMH = 3.5                    # km/h — the doc's example (§3.2)
TREADMILL_MAX = 15.0              # % — most treadmills (doc §3.2)
MINUTES = (40, 50)                # doc §3.2: 40–50 min
FLOOR = 30                        # 推估: a weekday cap below 30 min → none
TSB_MIN = -20.0                   # week_plan's 維持量 line
NO_DAYS = 7                       # nothing in the last 7 days (doc §2.5, 推估)
DEFAULT_PCT = 0.13                # 9 kg / 68 kg (the doc's example) when there is no body weight
SRC = ("Pandolf 1977（同代謝率的坡度）；Ludlow & Weyand 2017（代謝量和總重成正比）；"
       "UA trekking：跑步機坡度可以替代、5 → 10% 體重 → 行程背包；loaded-carry-training.md §1.1、§3.2")


def _d(x) -> Optional[dt.date]:
    if x is None:
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def qualifies(ev) -> bool:
    """A 百岳 or a multi-day event."""
    if not ev:
        return False
    kind = ev.get("kind") if isinstance(ev, dict) else ev.kind
    days = int((ev.get("days") if isinstance(ev, dict) else ev.days) or 1)
    return kind == "baiyue" or days > 1


def trip_kg(ev: Optional[dict]) -> float:
    """Event.pack_kg, else capacity.PACK_DEFAULT_MULTI / _SINGLE (9 kg)."""
    from backend.engine.racepower.capacity import PACK_DEFAULT_MULTI, PACK_DEFAULT_SINGLE
    if ev and ev.get("pack_kg") is not None:
        return float(ev["pack_kg"])
    return PACK_DEFAULT_MULTI if int((ev or {}).get("days") or 1) > 1 else PACK_DEFAULT_SINGLE


def weeks_out(start: dt.date, monday: dt.date) -> int:
    return -(-(start - monday).days // 7)


def stage_of(w: int) -> int:
    return next(k for lo, k in STAGE_WEEKS if w >= lo)


def stage_pct(stage: int, weight: Optional[float], trip: float) -> float:
    """Pack ÷ body weight of the stage (the trip pack caps 5 % / 10 %)."""
    if stage < 3:
        p = STAGE_PCT[stage - 1]
        return min(p, trip / weight) if weight else p
    return trip / weight if weight else DEFAULT_PCT


def simulated(pct: float, grade: float = BASE_GRADE, kmh: float = BASE_KMH) -> dict:
    """{"grade", "kmh"}: no pack, the metabolic rate of `pct` × body weight at
    `grade` / `kmh` (Pandolf; per kg of body weight, so the weight cancels). Above
    TREADMILL_MAX the grade stays there and the speed rises."""
    from backend.engine.racepower.hike import pandolf
    v = kmh / 3.6
    target = pandolf(1.0, pct, v, grade)
    lo, hi = grade, 60.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if pandolf(1.0, 0.0, v, mid) < target:
            lo = mid
        else:
            hi = mid
    g = (lo + hi) / 2
    if g <= TREADMILL_MAX:
        return {"grade": round(g * 2) / 2, "kmh": kmh}
    lo, hi = v, 3.0 * v
    for _ in range(80):
        mid = (lo + hi) / 2
        if pandolf(1.0, 0.0, mid, TREADMILL_MAX) < target:
            lo = mid
        else:
            hi = mid
    return {"grade": TREADMILL_MAX, "kmh": round((lo + hi) / 2 * 3.6, 1)}


def week_context(*, kind: str, mode: str, monday: dt.date, event: Optional[dict], weight: Optional[float],
                 tsb: Optional[float] = None) -> dict:
    info = {"active": False, "event": event, "weight": weight, "monday": monday.isoformat(), "why": [], "src": SRC}
    if not qualifies(event):
        info["why"].append("下一場 A 賽事不是百岳或多日行程" if event else "沒有下一場 A 賽事")
        return info
    start = _d(event["start"])
    w = weeks_out(start, monday)
    info.update(weeks_out=w, days_to=(start - monday).days)
    if kind != "specific" or not WEEKS[0] <= w <= WEEKS[1]:
        info["why"].append("只在專項期（賽前第 10–3 週）排")
        return info
    if mode in ("recovery_week", "reentry"):
        info["why"].append("恢復週／停訓後恢復期：不排")
        return info
    if tsb is not None and tsb < TSB_MIN:
        info["why"].append(f"週初 TSB {tsb:+.0f} < {TSB_MIN:.0f}：這週不排")
        return info
    trip = trip_kg(event)
    st = stage_of(w)
    pct = stage_pct(st, weight, trip)
    info.update(active=True, step=st, trip_kg=trip, pct=pct, kg=round(pct * weight, 1) if weight else None,
                sim=simulated(pct))
    return info


def plan_context(ds, status, today: dt.date, monday: dt.date, mode: str, tsb: Optional[float], gate=None) -> dict:
    """week_context() from week_plan()'s data. Never raises."""
    try:
        from backend.engine import b2b as B2B
        ev = B2B.event_json(B2B.target_event(status.plan.events, today))
        weight = status.plan.weight_on(today) if getattr(status, "plan", None) else None
        if not weight:
            try:
                from backend.engine.wko5expr.dataset import date_to_day
                weight = ds.setting("weight", date_to_day(today))
            except Exception:              # noqa: BLE001
                weight = None
        return week_context(kind=status.kind or "base", mode=mode, monday=monday, event=ev, weight=weight, tsb=tsb)
    except Exception as e:                  # noqa: BLE001 — the plan must still build
        return {"active": False, "error": type(e).__name__}


def projected_context(kind: str, mode: str, monday: dt.date, cur: Optional[dict], state=None, phases=None) -> dict:
    cur = cur or {}
    return week_context(kind=kind, mode=mode, monday=monday, event=cur.get("event"), weight=cur.get("weight"))


def next_state(info: Optional[dict] = None, state=None) -> dict:
    return {}


PUBLIC = ("active", "event", "step", "weeks_out", "days_to", "weight", "trip_kg", "pct", "kg", "sim", "why",
          "planned", "src", "error")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}


def _rate(s: dict) -> float:
    m = s.get("minutes") or 0
    return float(s.get("tss") or 0.0) / m if m else 0.0


def session_text(info: dict, minutes: int, aet: Optional[float]) -> tuple[str, str]:
    """(title, detail) of the session."""
    sim, pct = info["sim"], info["pct"]
    kg = info.get("kg")
    what = f"{kg:g} kg（體重的 {pct * 100:.0f}%）" if kg else f"體重的 {pct * 100:.0f}%"
    hr = f"心率 ≤ AeT {aet:.0f} bpm" if aet else "心率 ≤ AeT"
    main = max(10, minutes - 15)
    speed = f"{sim['kmh']:g} km/h" + ("（坡度到上限，改加速度）" if sim["grade"] >= TREADMILL_MAX and sim["kmh"] > BASE_KMH else "")
    title = f"陡坡健走 {sim['grade']:g}%（模擬負重 {kg:g} kg）" if kg else f"陡坡健走 {sim['grade']:g}%（模擬負重）"
    detail = (f"不背包：跑步機坡度 {sim['grade']:g}%、{speed}，或戶外 ≥ {sim['grade']:g}% 的坡用走的。"
              f"暖身 10 分平路 → 陡坡 {main} 分 → 緩和 5 分；{hr}，心率到上限就放慢、不降坡度。"
              f"這個坡度的代謝量 ≈ 在 {BASE_GRADE:g}% 坡、{BASE_KMH:g} km/h 背 {what}（Pandolf 1977，推估）；"
              f"背包另外練到的髖、軀幹、腳底和背著下坡這裡練不到。")
    return title, detail


def apply(ss: list[dict], info: Optional[dict], *, aet: Optional[float] = None, prefs=None, th=None,
          b2b: Optional[dict] = None, notes: Optional[list] = None, rates: Optional[dict] = None, **_) -> list[dict]:
    """Turn one weekday easy run into the session (in place); sets info["planned"]."""
    if not info or not info.get("active"):
        return ss
    info["planned"] = []
    start = _d((info.get("event") or {}).get("start"))
    for s in ss:
        if s.get("id") == "steep":
            return ss
    hard = [_d(s["day"]) for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "race")
                                                         or s.get("id") in ("long", "long2", "long3", "climb"))]
    post = (b2b or {}).get("post") or {}
    until = _d(post.get("until")) if post else None
    cap = getattr(prefs, "cap_weekday", None) if prefs is not None and getattr(prefs, "active", False) else None
    cands = []
    for s in ss:
        if s.get("kind") != "easy" or s.get("done") or not s.get("day") or s.get("id") == "climb":
            continue
        d = _d(s["day"])
        if start is not None and (start - d).days <= NO_DAYS:
            continue
        if any(abs((d - h).days) < 2 for h in hard):
            continue                                  # 48 h from the long day / quality
        if until is not None and d <= until:
            continue                                  # the easy days after a B2B
        m = MINUTES[1] if d.weekday() >= 5 or cap is None else min(MINUTES[1], int(cap))
        if m < FLOOR:
            continue
        cands.append((d.weekday() >= 5, d, s, max(min(m, MINUTES[1]), min(MINUTES[0], m))))
    if not cands:
        if notes is not None:
            notes.append({"level": "info", "src": "steep", "text": "這週沒有可以放陡坡健走的輕鬆日（長天、強度課的前後一天都不放）"})
        return ss
    cands.sort(key=lambda c: (c[0], c[1]))
    _, d, s, m = cands[0]
    delta = m - int(s.get("minutes") or 0)
    if delta:
        # the week's total is unchanged: the other easy runs give / take the difference
        for x in sorted((x for x in ss if x is not s and x.get("kind") == "easy" and not x.get("done")
                         and x.get("id") != "climb"),
                        key=lambda x: -(x.get("minutes") or 0)):
            r = _rate(x)
            new = max(20, int(x["minutes"]) - delta)
            delta -= int(x["minutes"]) - new
            x["minutes"], x["tss"] = new, round(r * new, 1)
            if delta <= 0:
                break
    rate = (rates or {}).get("trail") or (rates or {}).get("road") or 50.0
    title, detail = session_text(info, m, aet)
    s.update(id="steep", kind="easy", terrain="trail", minutes=int(m), title=title, detail=detail, source=SRC,
             tss=round(rate * m / 60.0, 1), target=f"心率 ≤ AeT {aet:.0f} bpm" if aet else "心率 ≤ AeT")
    info["planned"].append({"day": s["day"], "minutes": m, "grade": info["sim"]["grade"], "kmh": info["sim"]["kmh"]})
    return ss
