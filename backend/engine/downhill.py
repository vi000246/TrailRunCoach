"""
下坡課 in the generated plan. SP-99 put a structured downhill session on a countdown before an A race
with a clear descent (越野、百岳). SP-87 (owner 2026-10-05, decision 3;
docs/research/trail-terrain-and-climb-sessions.md §2.2, §4 #3) makes it a steady small dose:

  * 專項期 — every week, the downhill part rising FIRST_WORK_MIN → WORK_MIN (15 → 20 → 25′, SPEC_STEP a
    week from the phase's first week): SP-62's 「下坡離心預適應」 (workout_templates.downhill_ecc:
    −10～−15 % at RPE 3–5, time + descent, no HR / power target);
  * 基礎期 — every 2 weeks (an even ISO week), a gentle dose inside an easy run: −5～−10 % at RPE 3–5
    (the template's range: an RPE ≤ 4 part would give its minutes to 每堂課前加熱身, SP-364),
    BASE_FIRST_WORK the first time in the phase, then BASE_WORK (10 / 15′; the report's 10–15′);
  * none in the last 2 weeks before the race (賽前第 1–2 週; no day < DOWNHILL_LAST_DAYS), nor in the
    race's real 減量期 or its post-race days.

Why: the protection of one downhill run lasts 3–6 weeks and is gone by 9 (controlled trial [350];
Bontemps 2020: one 30′ run, gone after 9 weeks without another), 10 runs cut the soreness and 5 did not
(Bontemps 2025), and the less used to downhill running the more damage (Bontemps 2020 review) — so it
starts before the 專項期 in small doses and keeps going; Uphill Athlete does downhill repeats once a week
in the 專項期. Koop would do less (he is the outlier). Frequencies and doses are 推估 — no study gives a
dose-response or where the last one goes (14 days out matches SP-96's 「no hard downhill in the 減量期」);
the first one is small and the next 48 h are easy only [352].

Applied after the week is placed, like engine/technical.py, in week_plan and in every projected
week (the same rule, so reconcile never flips a week back and forth), before the 長爬坡有氧 and the
技術地形 session (which then keeps ≥ 2 days from it, technical.HARD_IDS): one easy run (not the
長爬坡反覆 / 陡坡健走 / heat session, not the easy days after a B2B) on a day ≥ 14 days before the
race and before its 減量期 (planning.taper_start), never on a race's post-race days (post_race_days:
the 72 h after a big downhill included), not on / the day before a hard day (the long
run, an interval, the 長爬坡反覆); the first one also keeps the 2 days after it easy. RPE 3–5 is an easy
session (workout_templates.session_role), so a recovery week keeps it. A weekday keeps the weekday cap
(the downhill part shrinks, ≥ DOWNHILL_MIN_WORK). The week's minutes stay: the other easy runs
give the difference. A road race (or 主要訓練項目 = 路跑) gets none; the 基礎期 with no A race ahead
(a trail athlete between races) keeps its every-2-weeks dose, a 基礎期 before an A race without a clear
descent gets none.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import _

LAST_WEEKS = 2                      # SP-87: none in 賽前第 1–2 週 (owner 2026-10-05)
DOWNHILL_LAST_DAYS = 14             # 推估: never closer than 14 days to the race (the 減量期 has none, SP-96)
MIN_DESCENT_M_PER_KM = 20.0         # 推估: a race descending ≥ 20 m/km has a clear descent (trail ~25–35 m/km)
WORK_MIN = 25                       # the template's downhill block (workout_templates.downhill_ecc)
FIRST_WORK_MIN = 15                 # 推估: the first one ≈ 60 % of it [352]: the first bout damages most
SPEC_STEP = 5                       # 推估: the 專項期's downhill part +5′ a week up to WORK_MIN (15 → 20 → 25)
SPEC_WEEKS = 10                     # 賽前第 10 週 = the 專項期's first week (specific_phase.WEEKS) — without phases
BASE_FIRST_WORK = 10                # 推估: the 基礎期's gentle dose, the report's 10–15′ (the first one 10′)
BASE_WORK = 15
DOWNHILL_MIN_WORK = 10              # 推估: a shorter block isn't worth it
WARM, COOL = 10, 10                 # the template's
DOWN_M_PER_MIN = 350 / 25           # the template's descent (DOWNHILL_M over 25′), −10～−15 %
BASE_DOWN_M_PER_MIN = 10.0          # 推估: −5～−10 % at an easy jog
EASY_MIN = 20                       # the other easy runs give the difference, never below this
HARD_IDS = ("long", "long2", "long3", "climb")
SKIP_IDS = ("climb", "steep", "tech", "downhill",    # (tech: a user's own / an earlier pass)
            "climb_base")                       # SP-87: the 基礎期's 長爬坡有氧 (engine/base_climb.py)
SRC = ("下坡跑的保護效果（重複回合效應）維持 3–6 週、9 週消失（對照試驗；Bontemps 2020）；10 次下坡跑才降低痠痛、"
       "5 次不夠（Bontemps 2025）；Uphill Athlete 專項期每週一次下坡；第一次之後 48 小時只排輕鬆課；"
       "專項期每週、基礎期每 2 週、賽前 2 週內不排、每次的分鐘數為推估")


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


def base_week(monday: dt.date) -> bool:
    """基礎期: the weeks that get the gentle downhill — every other week, by the ISO week number."""
    return monday.isocalendar()[1] % 2 == 0


def _first_monday(d: dt.date) -> dt.date:
    """The first Monday on / after `d` (a projected week's phase is the one holding its Monday)."""
    return d + dt.timedelta(days=(7 - d.weekday()) % 7)


def _phase(phases: list, monday: dt.date, kind: str) -> tuple[Optional[dt.date], Optional[str]]:
    """(the first day of the `kind` phase holding `monday`, the kind of the phase right before it)."""
    rows = []
    for p in phases or ():
        k = p["kind"] if isinstance(p, dict) else p.kind
        s, e = (_d(p["start"]), _d(p["end"])) if isinstance(p, dict) else (_d(p.start), _d(p.end))
        rows.append((k, s, e))
    for k, s, e in rows:
        if k == kind and s <= monday <= e:
            prev = next((k2 for k2, _s2, e2 in rows if e2 == s - dt.timedelta(days=1)), None)
            return s, prev
    return None, None


def week_context(*, kind: str, mode: str, monday: dt.date, events, phases=None, road: bool = False,
                 injury: Optional[dict] = None, behind: Optional[str] = None) -> dict:
    """Whether this week gets a downhill session: {"active", "phase", "race", "weeks_out", "first",
    "work", "why"}. `injury`: injuries.condition_week's rule (SP-270) — 膝前痛／髂脛束進行中 = no downhill
    session. `behind`: the day of this week's stored one when it is already behind (plan_store.hook_days,
    SP-87) — no second one."""
    info = {"active": False, "monday": monday.isoformat(), "phase": kind}
    if behind:
        b = _d(behind)
        return {**info, "why": _("本週的下坡課已經排在 {day}，不另外排", day=f"{b.month}/{b.day}")}
    if road:
        return {**info, "why": _("主要訓練項目：路跑")}
    if kind not in ("base", "specific") or mode == "reentry":
        return {**info, "why": _("只在基礎期、專項期排（停訓後恢復期不排）")}
    from backend.engine import injuries as INJ
    if INJ.avoids(injury, "downhill"):
        return {**info, "why": "；".join(injury["notes"])}
    if kind == "base" and not base_week(monday):
        return {**info, "why": _("基礎期每 2 週一次：這週不排下坡")}
    ahead = sorted((e for e in events or () if getattr(e, "priority", None) == "A" and e.start > monday),
                   key=lambda e: e.start)
    e = ahead[0] if ahead else None
    if e is None and kind == "specific":
        return {**info, "why": _("沒有下一場 A 賽事")}
    w = None
    if e is not None:
        if e.kind == "road":
            return {**info, "why": _("路跑賽事不排下坡課")}
        try:
            dpk = descent_per_km(e)
        except Exception:                   # noqa: BLE001 — the plan must still build
            dpk = 0.0
        info.update(race={"id": e.id, "name": e.name, "start": e.start.isoformat()}, descent_per_km=round(dpk, 1))
        if dpk < MIN_DESCENT_M_PER_KM:
            return {**info, "why": _("賽事下降不明顯（每公里 ↓{d:.0f} m < {min:.0f} m）：不排下坡課",
                                     d=dpk, min=MIN_DESCENT_M_PER_KM)}
        w = weeks_out(e.start, monday)
        info["weeks_out"] = w
        if w <= LAST_WEEKS:
            return {**info, "why": _("賽前 {n} 週內不排下坡課", n=LAST_WEEKS)}
    s0, prev = _phase(phases, monday, kind)
    m0 = _first_monday(s0) if s0 is not None else None
    if kind == "base":
        # the first even week of the 基礎期 = the first one (a new block after a race / a break)
        first = m0 is not None and monday == (m0 if base_week(m0) else m0 + dt.timedelta(weeks=1))
        work = BASE_FIRST_WORK if first else BASE_WORK
    else:
        k = (monday - m0).days // 7 if m0 is not None and monday >= m0 else max(0, SPEC_WEEKS - (w or SPEC_WEEKS))
        # the 專項期's first week is the first one unless a 基礎期 (with its small doses) came right before
        first = k == 0 and prev != "base"
        work = FIRST_WORK_MIN if first else min(WORK_MIN, FIRST_WORK_MIN + SPEC_STEP * k)
    # the race's real 減量期 (SP-96 / SP-114: 7–21 days, planning.taper_start — the planned phase, else
    # taper_days): no downhill on its days, whatever DOWNHILL_LAST_DAYS says (a 21-day taper starting
    # mid-week would otherwise get week 3's session)
    t0 = None
    if e is not None:
        from backend.engine import planning as PL
        try:
            t0 = PL.taper_start(phases or (), e)
        except Exception:                   # noqa: BLE001 — the plan must still build
            t0 = None
    return {**info, "active": True, "first": first, "work": work, "taper_start": t0.isoformat() if t0 else None,
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


def _down(work: int, base: bool) -> int:
    """The descent (m) of `work` downhill minutes, to 10 m."""
    return int(round(work * (BASE_DOWN_M_PER_MIN if base else DOWN_M_PER_MIN) / 10.0) * 10)


def _steps(work: int, base: bool = False) -> dict:
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    b = WT.B()
    main = b.t("work", work * 60, WT.rpe(3, 5, down=_down(work, True)), "−5～−10% 緩下坡，放鬆小步") if base else \
        b.t("work", work * 60, WT.rpe(3, 5, down=_down(work, False)), "−10～−15% 下坡，輕鬆到中等")
    items = [b.warm(WARM, "平路暖身"), main, b.cool(COOL, "平路緩和")]
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
    race = _d((info.get("race") or {}).get("start"))     # None: a 基礎期 with no A race ahead
    base = info.get("phase") == "base"
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
    want = (BASE_FIRST_WORK if base else FIRST_WORK_MIN) if first else \
        int(info.get("work") or (BASE_WORK if base else WORK_MIN))

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
        if (race is not None and (race - d).days < DOWNHILL_LAST_DAYS) or (taper is not None and d >= taper) \
                or d.isoformat() in post_race \
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
                          "text": _("這週該排下坡課，但沒有適合的輕鬆日（長跑、強度課的當天和前一天不放"
                                    "{first}；賽前 {days} 天內不放）", days=DOWNHILL_LAST_DAYS,
                                    first=_("，第一次之後 2 天也要輕鬆") if first else "")})
        return ss
    cands.sort(key=lambda c: (c[0], c[1]))
    _m, d, s, work = cands[0]
    need = WARM + work + COOL
    m = max(int(s.get("minutes") or 0), need)
    delta = m - int(s.get("minutes") or 0)
    down = _down(work, base)
    flat = m - need
    rate = float((rates or {}).get("trail") or (rates or {}).get("road") or 60.0)
    if base:
        title = (_("緩下坡（第一次，下坡 {work}′）", work=work) if first else _("緩下坡（下坡 {work}′）", work=work))
        detail = _("找 −5～−10% 的緩下坡（步道、產業道路或公園坡道），下坡 {work} 分、約 ↓{down} m，RPE 3–5、"
                   "放鬆小步，上坡用走的回去；暖身、緩和各 10 分平路", work=work, down=down)
    else:
        title = (_("下坡離心（第一次，下坡 {work}′）", work=work) if first else _("下坡離心（下坡 {work}′）", work=work))
        detail = _("找 −10～−15% 的下坡（步道或產業道路），下坡 {work} 分、約 ↓{down} m，RPE 3–5、放鬆不衝，"
                   "上坡用走的回去；暖身、緩和各 10 分平路", work=work, down=down)
    detail += (_("；其餘 {m} 分平路輕鬆跑", m=flat) if flat >= 5 else "") \
        + (_("；第一次量小，之後 2 天只排輕鬆課") if first else "") \
        + _("；心率、功率在下坡不準，看 RPE")
    s.update(id="downhill", kind="easy", terrain="trail", minutes=int(m), title=title, detail=detail, target="",
             source=SRC, steps=_steps(work, base), climb_m=None, tss=round(rate * m / 60.0, 1))
    if delta > 0:
        _rebalance(ss, s, delta)
    info["planned"].append({"day": s["day"], "minutes": int(m), "work": work, "descent_m": down, "first": first})
    if notes is not None:
        day, fst = f"{d.month}/{d.day}", (_("；這是第一次，量小、之後 2 天輕鬆") if first else "")
        text = _("基礎期每 2 週一次小劑量的緩下坡（{day}，下坡 {work} 分）：下坡跑的保護效果維持 3–6 週、9 週沒做就消失，"
                 "所以專項期之前就小量開始（頻率、分鐘數為推估）{first}", day=day, work=work, first=fst) if base else \
            _("賽前第 {w} 週的下坡課（{day}，下坡 {work} 分）：專項期每週一次，逐週加到 25 分；賽前 2 週內、"
              "減量期不排（頻率、分鐘數為推估）{first}", w=info.get("weeks_out"), day=day, work=work, first=fst)
        notes.append({"level": "info", "src": "downhill", "text": text})
    return ss


PUBLIC = ("active", "phase", "race", "weeks_out", "first", "work", "why", "planned", "descent_per_km")


def public(info: Optional[dict]) -> Optional[dict]:
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}
