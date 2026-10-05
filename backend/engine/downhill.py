"""
下坡課 in the generated plan (SP-99): before an A race with a clear descent (越野、百岳), the 專項期
gets a structured downhill session on a countdown — 賽前第 9、6、3 週 (DOWNHILL_WEEKS), so they are
≤ 3 weeks apart and the last one is 14–21 days out. The session type is SP-62's 「下坡離心預適應」
(workout_templates.downhill_ecc: −10～−15 % downhill at RPE 3–5, time + descent, no HR / power
target); this module only decides WHEN it goes in.

Why (docs/research/periodization-cross-sport.md §3.3, §6.1 SP-99):
  * one downhill run protects against the next one's soreness and muscle damage for 3 and 6
    weeks, not 9 (controlled trial [350]); the 3-week gap replicated three times [351][352][353];
    trail runners who do downhill repeats lose less force after a downhill run [357] — 已驗證;
  * no study gives a dose-response or where the last one goes before a race: 14–21 days out is
    推估 (the protection lasts ≥ 3 weeks; the first bout does the most damage, so not too close),
    and it matches SP-96's 「no hard downhill in the 減量期」;
  * the first one is small (DOWNHILL_FIRST_WORK) and the next 48 h are easy only [352].

Applied after the week is placed, like engine/technical.py, in week_plan and in every projected
week (the same rule, so reconcile never flips a week back and forth), just before the 技術地形
session (which then keeps ≥ 2 days from it, technical.HARD_IDS): one easy run (not the
長爬坡反覆 / 陡坡健走 / heat session, not the easy days after a B2B) on a day ≥ 14 days before the
race and before its 減量期 (planning.taper_start), never on a race's post-race days (post_race_days:
the 72 h after a big downhill included), not on / the day before a hard day (the long
run, an interval, the 長爬坡反覆); the first one also keeps the 2 days after it easy. RPE 3–5 is an easy session (workout_templates.session_role),
so a recovery week keeps it. A weekday keeps the weekday cap
(the downhill part shrinks, ≥ DOWNHILL_MIN_WORK). The week's minutes stay: the other easy runs
give the difference. A road race (or 主要訓練項目 = 路跑) gets none.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import _

DOWNHILL_WEEKS = (9, 6, 3)          # 賽前第 n 週: every 3 weeks counted back from week 3 [350]
DOWNHILL_LAST_DAYS = 14             # 推估: never closer than 14 days to the race (the 減量期 has none, SP-96)
MIN_DESCENT_M_PER_KM = 20.0         # 推估: a race descending ≥ 20 m/km has a clear descent (trail ~25–35 m/km)
WORK_MIN = 25                       # the template's downhill block (workout_templates.downhill_ecc)
FIRST_WORK_MIN = 15                 # 推估: the first one ≈ 60 % of it [352]: the first bout damages most
DOWNHILL_MIN_WORK = 10              # 推估: a shorter block isn't worth it
WARM, COOL = 10, 10                 # the template's
DOWN_M_PER_MIN = 350 / 25           # the template's descent (DOWNHILL_M over 25′)
EASY_MIN = 20                       # the other easy runs give the difference, never below this
HARD_IDS = ("long", "long2", "long3", "climb")
SKIP_IDS = ("climb", "steep", "tech", "downhill")    # (tech: a user's own / an earlier pass)
SRC = ("下坡跑的保護效果（重複回合效應）維持 3–6 週、9 週消失（對照試驗；3 週間隔另有三個研究重複）；"
       "第一次之後 48 小時只排輕鬆課；最後一次在賽前 14–21 天、第一次量小為推估")


def _d(x) -> Optional[dt.date]:
    if not x:
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def weeks_out(start: dt.date, monday: dt.date) -> int:
    """賽前第 n 週 of the week starting `monday` (specific_phase.weeks_out)."""
    return -(-(start - monday).days // 7)


def descent_per_km(e) -> float:
    """The race's descent per km (overview._descent_per_km: the stored GPX, else descent = climb)."""
    from backend.engine.overview import _descent_per_km
    return _descent_per_km(e)


def _phase_start(phases: list, monday: dt.date) -> Optional[dt.date]:
    """The first day of the 專項期 holding `monday`."""
    for p in phases or ():
        kind = p["kind"] if isinstance(p, dict) else p.kind
        s, e = (_d(p["start"]), _d(p["end"])) if isinstance(p, dict) else (_d(p.start), _d(p.end))
        if kind == "specific" and s <= monday <= e:
            return s
    return None


def week_context(*, kind: str, mode: str, monday: dt.date, events, phases=None, road: bool = False) -> dict:
    """Whether this week gets a downhill session: {"active", "race", "weeks_out", "first", "why"}."""
    info = {"active": False, "monday": monday.isoformat()}
    if road:
        return {**info, "why": _("主要訓練項目：路跑")}
    if kind != "specific" or mode == "reentry":
        return {**info, "why": _("只在專項期排（停訓後恢復期不排）")}
    ahead = sorted((e for e in events or () if getattr(e, "priority", None) == "A" and e.start > monday),
                   key=lambda e: e.start)
    if not ahead:
        return {**info, "why": _("沒有下一場 A 賽事")}
    e = ahead[0]
    if e.kind == "road":
        return {**info, "why": _("路跑賽事不排下坡課")}
    try:
        dpk = descent_per_km(e)
    except Exception:                       # noqa: BLE001 — the plan must still build
        dpk = 0.0
    info.update(race={"id": e.id, "name": e.name, "start": e.start.isoformat()}, descent_per_km=round(dpk, 1))
    if dpk < MIN_DESCENT_M_PER_KM:
        return {**info, "why": _("賽事下降不明顯（每公里 ↓{d:.0f} m < {min:.0f} m）：不排下坡課",
                                 d=dpk, min=MIN_DESCENT_M_PER_KM)}
    w = weeks_out(e.start, monday)
    info["weeks_out"] = w
    if w not in DOWNHILL_WEEKS:
        return {**info, "why": _("下坡課排在賽前第 {weeks} 週", weeks="、".join(str(x) for x in DOWNHILL_WEEKS))}
    s0 = _phase_start(phases, monday)
    w0 = weeks_out(e.start, s0 - dt.timedelta(days=s0.weekday())) if s0 is not None else max(DOWNHILL_WEEKS)
    first = w == max((x for x in DOWNHILL_WEEKS if x <= w0), default=w)
    # the race's real 減量期 (SP-96 / SP-114: 7–21 days, planning.taper_start — the planned phase, else
    # taper_days): no downhill on its days, whatever DOWNHILL_LAST_DAYS says (a 21-day taper starting
    # mid-week would otherwise get week 3's session)
    from backend.engine import planning as PL
    try:
        t0 = PL.taper_start(phases or (), e)
    except Exception:                       # noqa: BLE001 — the plan must still build
        t0 = None
    return {**info, "active": True, "first": first, "taper_start": t0.isoformat() if t0 else None,
            "post_race": post_race_days(events, phases, monday)}


def post_race_days(events, phases, monday: dt.date) -> list[str]:
    """The days of the week of `monday` under a race's post-race day rules (engine/post_race.py, SP-98 /
    SP-95): an A race's first week (≤ REC_SHORT_DAYS) and its flat days after a big downhill
    (DOWNHILL_FLAT_DAYS = 72 h), a B race's easy days and flat days — no downhill session there (the
    day rules run after this pass and would only cap / flatten it, keeping its title)."""
    from backend.engine import post_race as PR
    out = set()
    try:
        wins = PR.a_windows(phases or (), events or (), monday) + PR.b_windows(events or (), monday)
    except Exception:                       # noqa: BLE001 — the plan must still build
        return []
    for w in wins:
        last = max(x for x in (w.get("short"), w.get("easy"), w.get("flat"), w["end"]) if x is not None)
        d = max(w["end"] + dt.timedelta(days=1), monday)
        while d <= min(last, monday + dt.timedelta(days=6)):
            out.add(d.isoformat())
            d += dt.timedelta(days=1)
    return sorted(out)


def _steps(work: int) -> dict:
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    b = WT.B()
    items = [b.warm(WARM, "平路暖身"),
             b.t("work", work * 60, WT.rpe(3, 5, down=int(round(work * DOWN_M_PER_MIN / 10.0) * 10)),
                 "−10～−15% 下坡，輕鬆到中等"),
             b.cool(COOL, "平路緩和")]
    return WS.doc(items, origin="template:lib:downhill_ecc")


def _rebalance(ss: list, me: dict, delta: int) -> None:
    """The week's total stays: the other easy runs give `delta` minutes (≥ EASY_MIN)."""
    for x in sorted((x for x in ss if x is not me and x.get("kind") == "easy" and not x.get("done")
                     and x.get("id") not in SKIP_IDS and not x.get("heat")),
                    key=lambda x: -(x.get("minutes") or 0)):
        if delta <= 0:
            break
        m = int(x.get("minutes") or 0)
        new = max(EASY_MIN, m - delta)
        if new < m and m:
            x["tss"] = round(float(x.get("tss") or 0.0) * new / m, 1)
            x["minutes"] = new
            delta -= m - new


def apply(ss: list, info: Optional[dict], *, prefs=None, rates: Optional[dict] = None, notes: Optional[list] = None,
          hard_done=(), b2b: Optional[dict] = None, **_kw) -> list:
    """Turn one placed easy run into the week's downhill session, in place; sets info["planned"].
    `hard_done`: hard days already done this week (dates); `b2b`: this week's B2B info (its easy days)."""
    if not info or not info.get("active"):
        return ss
    info["planned"] = []
    if any(s.get("id") == "downhill" for s in ss):
        return ss
    race = _d(info["race"]["start"])
    taper = _d(info.get("taper_start"))
    post_race = set(info.get("post_race") or ())
    hard = [_d(s["day"]) for s in ss if s.get("day") and (s.get("kind") in ("quality", "test", "race")
                                                           or s.get("id") in HARD_IDS)]
    hard += [_d(d) for d in hard_done or ()]
    post = (b2b or {}).get("post") or {}
    until = _d(post.get("until")) if post else None
    act = prefs is not None and getattr(prefs, "active", False)
    cap = getattr(prefs, "cap_weekday", None) if act else None
    long_cap = getattr(prefs, "long_cap", None) if act else None
    first = bool(info.get("first"))
    want = FIRST_WORK_MIN if first else WORK_MIN

    def free_of_hard(d: dt.date) -> bool:
        # not on / the day before a hard day (sore legs the next day); the first one keeps the 2 days
        # after it easy (48 h, [352])
        span = (0, 1, 2) if first else (0, 1)
        return all((h - d).days not in span for h in hard)

    cands = []
    for s in ss:
        if s.get("kind") != "easy" or s.get("done") or not s.get("day") or s.get("id") in SKIP_IDS or s.get("heat"):
            continue
        d = _d(s["day"])
        if (race - d).days < DOWNHILL_LAST_DAYS or (taper is not None and d >= taper) or d.isoformat() in post_race \
                or (until is not None and d <= until) or not free_of_hard(d):
            continue
        c = long_cap if d.weekday() >= 5 else cap
        work = want if c is None else min(want, int(c) - WARM - COOL)
        if work < DOWNHILL_MIN_WORK:
            continue
        cands.append((-(int(s.get("minutes") or 0)), d, s, work))
    if not cands:
        if notes is not None:
            notes.append({"level": "info", "src": "downhill",
                          "text": _("賽前第 {w} 週該排下坡課，但這週沒有適合的輕鬆日（長跑、強度課的當天和前一天不放"
                                    "{first}；賽前 {days} 天內不放）", w=info.get("weeks_out"), days=DOWNHILL_LAST_DAYS,
                                    first=_("，第一次之後 2 天也要輕鬆") if first else "")})
        return ss
    cands.sort(key=lambda c: (c[0], c[1]))
    _m, d, s, work = cands[0]
    need = WARM + work + COOL
    m = max(int(s.get("minutes") or 0), need)
    delta = m - int(s.get("minutes") or 0)
    down = int(round(work * DOWN_M_PER_MIN / 10.0) * 10)
    flat = m - need
    rate = float((rates or {}).get("trail") or (rates or {}).get("road") or 60.0)
    title = (_("下坡離心（第一次，下坡 {work}′）", work=work) if first else _("下坡離心（下坡 {work}′）", work=work))
    detail = _("找 −10～−15% 的下坡（步道或產業道路），下坡 {work} 分、約 ↓{down} m，RPE 3–5、放鬆不衝，"
               "上坡用走的回去；暖身、緩和各 10 分平路", work=work, down=down) \
        + (_("；其餘 {m} 分平路輕鬆跑", m=flat) if flat >= 5 else "") \
        + (_("；第一次量小，之後 2 天只排輕鬆課") if first else "") \
        + _("；心率、功率在下坡不準，看 RPE")
    s.update(id="downhill", kind="easy", terrain="trail", minutes=int(m), title=title, detail=detail, target="",
             source=SRC, steps=_steps(work), climb_m=None, tss=round(rate * m / 60.0, 1))
    if delta > 0:
        _rebalance(ss, s, delta)
    info["planned"].append({"day": s["day"], "minutes": int(m), "work": work, "descent_m": down, "first": first})
    if notes is not None:
        notes.append({"level": "info", "src": "downhill",
                      "text": _("賽前第 {w} 週排一次下坡課（{day}）：下坡跑一次的保護效果維持 3–6 週，"
                                "所以專項期每 3 週一次、最後一次在賽前 14–21 天（推估）{first}",
                                w=info.get("weeks_out"), day=f"{d.month}/{d.day}",
                                first=_("；這是第一次，量小、之後 2 天輕鬆") if first else "")})
    return ss


PUBLIC = ("active", "race", "weeks_out", "first", "why", "planned", "descent_per_km")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}
