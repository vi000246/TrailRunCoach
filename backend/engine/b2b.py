"""
連續兩天長天（Back-to-Back，B2B） — docs/research/back-to-back-and-long-day.md.

Hooks (small, so the planner files stay readable):

  * overview.week_plan  — week_context() at the volume step (the TSB exception),
    finalize() + followers() in the session template, decorate() after the
    課表偏好 shaping, done_follow() when marking what is done, place() after
    the placement.
  * projection.project_weeks — the same per projected week (no TSB there:
    re-checked when the week comes).
  * adapt._fatigue — fatigue_exempt(): TSB < −30 alone after a planned B2B
    does not cut the week.

When (doc §2.4):
  * only in the 專項期 (`specific`) before the next A event that is multi-day
    (days > 1) or a single day ≥ 6 h (planning.Event.is_long) — UA「longer
    races」, CTS, Koop;
  * the last B2B ends ≥ 3 weeks before the event (搜尋摘要「3 weeks」未驗證;
    Koop: nothing hard in the last 2–3 weeks);
  * the 3-day version once, 4–6 weeks before a multi-day event of ≥ 3 days
    (CTS Jones-Wilkins「four to six weeks in advance」; doc §2.5 ties it to the
    3-day 百岳 — a 2-day trip keeps the 2-day B2B); if no recovery-week
    trigger lands in that window, the window's last week takes it (推估);
  * at most one per 3:1 cycle, on the first build week after the recovery week
    (Johnston「slightly below-average」week before, Koop「after rest」); the
    number of weekends (≈ 2 in an 8-week block) is 推估;
  * prerequisites: base built (longest of the 28 days before ≥ day 1 × 0.87,
    the app's +15 % step reversed), not a re-entry / recovery week, TSB ≥ −20,
    CTL ramp < 8 and the gate's guardrails pass (combination 推估).

Structure (doc §2.5):
  * day 1 = the week's long day (longer, more climbing); day 2 ≈ 2/3 of day 1
    (CTS 30:20 ≈ 0.67; 0.6–0.7 推估); both ≤ AeT HR, HR-only target
    (vo2max-gate-and-trail-metric.md: trail pace / power unreliable);
  * the week's total does not grow (Koop「Do not increase the total」): day 2
    comes out of the easy runs; the pair ≤ 70 % of the week (推估);
  * 百岳: pack 5 → 10 → 15 % of body weight by weeks before the trip (UA
    trekking, ≈ 2 weeks a step; weeks 10–9 / 8–7 / 6–3 as in
    loaded-carry-training.md, 推估), ≤ the trip pack (9 kg,
    capacity.PACK_DEFAULT_MULTI); day 1 practises race fuelling 30–60 g/h
    (Burke 2011).

The exception (doc §2.5, user-approved): a planned B2B pushes TSB below −30 /
−20. week_plan would make the next week a recovery week (3:1 → 2:2); instead
the B2B week and the week after keep their volume, the 4 days after the B2B are
easy only (no quality / test; UA「three or four light days」, 4 推估), and the
3:1 cycle continues. Not when something beyond the expected drop shows up:
a CTL ramp ≥ 8 (status.RAMP["short"]) or — in adapt — two red-compliance
sessions in a row.

Evaluation without RPE (doc §2.6): day 2 vs day 1 on the climbs (grade ≥ 10 %,
≥ 3 consecutive 100 m windows, moving > 1.5 km/h — hikehr.HIKE_FILTER):
ΔHR@VAM (hikehr.fatigue: day-1 HR ~ VAM line, day-2 median residual) and
ΔVAM@AeT (windows at AeT − 10 … AeT: each day's VAM ~ HR line read at
AeT − 5, day 2 ÷ day 1 — the line is 推估; one-speed days fall back to the
band medians), read as a
2 × 2: lower HR + slower = muscular fatigue; similar / higher HR + slower =
cardiovascular drift or under-fuelling; similar = good durability. Thresholds
±3 bpm (= workout_review.AET_MARGIN) and ±5 % (UA's 5 %) are 推估.
"""
from __future__ import annotations

import datetime as dt
from statistics import median
from typing import Callable, Iterable, Optional

FOLLOWERS = ("long2", "long3")

# ---- when ------------------------------------------------------------------
LAST_BEFORE_DAYS = 21          # last B2B day ≥ 3 weeks before the event (未驗證 summary; Koop 2–3 wk)
THREE_DAY_WINDOW = (28, 42)    # CTS: the 3-day block 4–6 weeks before
THREE_DAY_MIN_TRIP = 3         # doc §2.5: the 3-day block before a ≥ 3-day trip (2-day trip: 2-day B2B)
SPACING_DAYS = 14              # 推估: never two B2B weekends < 2 weeks apart
RECOVERY_SHARE = 0.85          # 推估: last week ≤ 85 % of the 3 before = the 3:1 recovery week (0.65)
LONGEST_FRAC = 0.87            # base built: longest 28 d ≥ day 1 × 0.87 (1 / 1.15, the app's +15 % step)
TSB_MIN = -20.0                # week_plan's 維持量 line
RAMP_MAX = 8.0                 # status.RAMP["short"] (Friel 5–8)
# ---- structure ----------------------------------------------------------------
DAY2_RATIO = 0.67              # CTS 30:20 (Jones-Wilkins); 0.6–0.7 推估
SINGLE_DAY2 = (90, 150)        # single-day ≥ 6 h event: day 2 1.5–2.5 h easy (推估, doc §2.5)
PAIR_SHARE = 0.70              # 推估: the B2B days ≤ 70 % of the week's minutes
MIN_DAY2 = 60                  # 推估: a "long" day 2 is ≥ 60 min, else no B2B this week
FUEL_MIN_H = 4.0               # Koop: a fuelling long run is ≥ 4 h
PACK_STEPS = (0.05, 0.10, 0.15)  # UA trekking: 5 → 10 → 15 % of body weight
POST_EASY_DAYS = 4             # UA / Johnston「three or four light days」; 4 推估
# ---- detection of a done B2B --------------------------------------------------
DETECT_MIN = 90.0              # 推估: a day counts as a long day from 90 moving minutes
# ---- evaluation ---------------------------------------------------------------
HR_SAME_BPM = 3.0              # 推估 (= workout_review.AET_MARGIN)
VAM_SAME = 0.05                # 推估 (UA 5 %)
AET_BAND_BPM = 10.0            # ΔVAM@AeT: windows at AeT − 10 … AeT (vo2max-gate-and-trail-metric.md §2.4 (a) ③)
TREND_STEP = 0.02              # 推估: the day-2 VAM ratio moved ≥ 2 points = 變好 / 變差

SRC_WHEN = ("UA（專項期、longer races）；CTS Jones-Wilkins（3 天版本賽前 4–6 週）；Koop（最後 2–3 週不硬塞）；"
            "賽前 ≥ 3 週為搜尋摘要（未驗證）；每個 3:1 週期一次、次數為推估")
SRC_DAYS = ("Koop／CTS〈Block Training〉：第 1 天較硬、總量不加；Jones-Wilkins（CTS）30＋20＋20；"
            "UA：兩天都 ≤ AeT；Burke 2011：補給 30–60 g/h；第 2 天比例 0.67、背負進度對應次數為推估")
SRC_POST = "Johnston（UA）「three or four light days」；4 天、不改恢復週為推估"
SRC_EVAL = ("hikehr.fatigue（同 VAM 的心率差，無外部來源 F17）；ΔVAM@AeT（vo2max-gate-and-trail-metric.md）；"
            "2×2 判讀依 UA、Le Meur 2013、Coyle 2001；±3 bpm、±5 % 為推估")


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _r5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def wd(day) -> str:
    return "週" + "一二三四五六日"[_d(day).weekday()]


def md(day) -> str:
    d = _d(day)
    return f"{d.month}/{d.day}"


# ---------------------------------------------------------------------------
# the target event
# ---------------------------------------------------------------------------

def target_event(events: Iterable, today: dt.date):
    """The next A event after `today` (the one the 專項期 builds towards)."""
    ahead = sorted((e for e in events or () if getattr(e, "priority", "A") == "A" and e.start > today),
                   key=lambda e: e.start)
    return ahead[0] if ahead else None


def qualifies(ev) -> bool:
    """Multi-day, or a single day ≥ 6 h (planning.Event.is_long)."""
    if ev is None:
        return False
    days = int(ev.get("days") if isinstance(ev, dict) else (ev.days or 1)) or 1
    is_long = ev.get("is_long") if isinstance(ev, dict) else ev.is_long
    return days > 1 or bool(is_long)


def event_json(ev) -> Optional[dict]:
    if ev is None:
        return None
    return {"id": ev.id, "name": ev.name, "start": ev.start.isoformat(), "days": int(ev.days or 1),
            "kind": ev.kind, "is_long": bool(ev.is_long), "est_hours": ev.est_hours,
            "pack_kg": getattr(ev, "pack_kg", None), "qualifies": qualifies(ev)}


# ---------------------------------------------------------------------------
# B2B weekends already done (from the activities)
# ---------------------------------------------------------------------------

def detect(rows: Iterable[tuple]) -> list[dict]:
    """Done B2B blocks from (date, moving minutes, activity idx) rows of the
    endurance activities: ≥ 2 consecutive days of ≥ DETECT_MIN minutes each
    (推估). Per day the minutes add up; the day's longest activity is kept."""
    per: dict = {}
    for day, minutes, idx in rows:
        d = _d(day)
        tot, best = per.get(d, (0.0, None))
        bm = best[1] if best else -1
        per[d] = (tot + float(minutes or 0), (idx, minutes) if (minutes or 0) > bm else best)
    long_days = sorted(d for d, (m, _) in per.items() if m >= DETECT_MIN)
    out, run = [], []
    for d in long_days + [None]:
        if run and (d is None or (d - run[-1]).days != 1):
            if len(run) >= 2:
                out.append({"start": run[0].isoformat(), "end": run[-1].isoformat(), "days": len(run),
                            "minutes": [round(per[x][0]) for x in run], "idx": [per[x][1][0] for x in run]})
            run = []
        if d is not None:
            run.append(d)
    return out


def history(done: list[dict], since: Optional[dt.date], before: dt.date) -> dict:
    """The block's B2B record before `before`: last end, 3-day done, count."""
    blk = [b for b in done if (since is None or _d(b["start"]) >= since) and _d(b["end"]) < before]
    return {"last": blk[-1]["end"] if blk else None, "three_done": any(b["days"] >= 3 for b in blk),
            "count": len(blk), "done": blk}


# ---------------------------------------------------------------------------
# is it due this week?
# ---------------------------------------------------------------------------

def last_was_recovery(hours: list[float]) -> bool:
    """`hours` = complete weeks, oldest first. The last one is clearly lighter
    than the 3 before it (the 3:1 recovery week is 0.65 of them; 0.85 推估)."""
    if len(hours) < 4:
        return False
    prev = sum(hours[-4:-1]) / 3.0
    return prev > 0.5 and hours[-1] <= RECOVERY_SHARE * prev


def week_context(*, kind: str, mode: str, monday: dt.date, event: Optional[dict], last_recovery: bool,
                 state: dict, tsb: Optional[float] = None, ramp: Optional[float] = None,
                 guard_ok: bool = True, post_from: Optional[dict] = None) -> dict:
    """Everything about B2B for one week except the base-built check (needs
    the long-run minutes: finalize()). `event`: event_json(); `state`:
    history(); `tsb`: TSB at the start of the week (Monday's, so the answer
    doesn't change mid-week after day 1); `post_from`: the B2B that ended just
    before this week (detect() row or a projected one)."""
    sunday = monday + dt.timedelta(days=6)
    info = {"event": event, "candidate": False, "due": False, "days": 2, "index": int(state.get("count") or 0) + 1,
            "why": [], "blocked": [], "last": state.get("last"), "three_done": bool(state.get("three_done")),
            "count": int(state.get("count") or 0), "post": None, "src": SRC_WHEN}
    if post_from and kind == "specific" and qualifies(event):
        end = _d(post_from["end"])
        if monday - dt.timedelta(days=3) <= end < monday:
            until = end + dt.timedelta(days=POST_EASY_DAYS)
            info["post"] = {"start": post_from["start"], "end": post_from["end"], "until": until.isoformat(),
                            "src": SRC_POST}
    if kind != "specific":
        info["blocked"].append("只在專項期排")
        return info
    if not qualifies(event):
        info["blocked"].append("下一場 A 賽事不是多日、也不到 6 小時" if event else "沒有下一場 A 賽事")
        return info
    if mode in ("recovery_week", "reentry"):
        info["blocked"].append("恢復週／停訓後恢復期不排")
        return info
    days_before = (_d(event["start"]) - sunday).days
    if days_before < LAST_BEFORE_DAYS:
        info["blocked"].append(f"離賽事不到 3 週（{days_before} 天）：最後一次 B2B 要在賽前 ≥ 3 週")
        return info
    multi = int(event.get("days") or 1) > 1
    # the 3-day block is for a ≥ 3-day trip (doc §2.5 「百岳 3 天」; CTS 30 + 20 + 20); a 2-day trip keeps 2 days
    three_left = int(event.get("days") or 1) >= THREE_DAY_MIN_TRIP and not info["three_done"]
    in_three = three_left and THREE_DAY_WINDOW[0] <= days_before <= THREE_DAY_WINDOW[1]
    if three_left and THREE_DAY_WINDOW[1] < days_before < THREE_DAY_WINDOW[1] + SPACING_DAYS + 7:
        # a 2-day now would sit < 2 weeks before the 3-day window: keep the slot for the 3-day (推估)
        info["blocked"].append("這次留給 3 天版本（賽前 4–6 週），兩次 B2B 至少隔 2 週")
        return info
    last = _d(state.get("last"))
    if last is not None and (monday - last).days < SPACING_DAYS:
        info["blocked"].append(f"上一次 B2B（{md(last)}）不到 2 週")
        return info
    if in_three:
        # the 3-day block once, 4–6 weeks out: the first week of the window that can take it (推估)
        info["why"].append("3 天版本要在賽前 4–6 週做一次")
    elif last_recovery:
        info["why"].append("上週是恢復週：3:1 的第一個加量週")
    else:
        info["blocked"].append("每個 3:1 週期最多一次，排在恢復週後的第一個加量週")
        return info
    if tsb is not None and tsb < TSB_MIN:
        info["blocked"].append(f"週初 TSB {tsb:+.0f} < {TSB_MIN:.0f}：先恢復")
        return info
    if ramp is not None and ramp >= RAMP_MAX:
        info["blocked"].append(f"CTL 每週 +{ramp:.1f}（≥ {RAMP_MAX:.0f}）")
        return info
    if not guard_ok:
        info["blocked"].append("間歇門檻的護欄沒過（低強度比例或飄移）")
        return info
    info["candidate"] = True
    info["days"] = 3 if in_three else 2
    info["weeks_out"] = -(-(days_before + 6) // 7)          # 賽前第 n 週 (the week's Monday)
    info["why"].append(f"賽前 {days_before} 天（{event.get('name') or 'A 賽事'}，"
                       f"{'多日' if multi else '單日 ≥ 6 小時'}）")
    return info


def finalize(info: dict, long_min: float, longest_before: float, total_min: float) -> dict:
    """The base-built check and the day minutes. Sets info["due"] / ["minutes"]."""
    if not info.get("candidate"):
        return info
    if longest_before < LONGEST_FRAC * long_min:
        info["blocked"].append(f"基礎還不夠：近 28 天最長 {longest_before:.0f} 分 < 第 1 天 {long_min:.0f} 分 × {LONGEST_FRAC}")
        return info
    mins = minutes(long_min, total_min, info["days"], info.get("event") or {})
    if mins is None:
        info["blocked"].append("本週的量排不下兩天長天（第 2 天會少於 60 分）")
        return info
    info["due"] = True
    info["minutes"] = mins
    return info


def minutes(long_min: float, total_min: float, days: int, event: dict) -> Optional[list[int]]:
    """[day 1, day 2(, day 3)] minutes; None when day 2 would be < MIN_DAY2."""
    n = days - 1
    d1 = float(long_min)
    single = int(event.get("days") or 1) <= 1
    d2 = DAY2_RATIO * d1
    if single:
        d2 = min(max(d2, SINGLE_DAY2[0]), SINGLE_DAY2[1], d1)
    cap = PAIR_SHARE * total_min
    if d1 + n * d2 > cap > 0:
        f = cap / (d1 + n * d2)
        d1, d2 = d1 * f, d2 * f
    if d2 < MIN_DAY2:
        return None
    return [_r5(d1)] + [_r5(d2)] * n


def pack_kg(weeks_out: int, weight: Optional[float], event: Optional[dict]) -> Optional[dict]:
    """百岳 pack on a B2B weekend `weeks_out` weeks before the trip: UA
    trekking 5 → 10 → 15 % of body weight, each step ≈ 2 weeks, placed like
    loaded-carry-training.md's table (weeks 10–9: 5 %, 8–7: 10 %, 6–3: the
    trip pack), never above the trip pack (Event.pack_kg when the plan has
    it, else capacity.PACK_DEFAULT_MULTI 9 kg)."""
    if not event or event.get("kind") != "baiyue":
        return None
    from backend.engine.racepower.capacity import PACK_DEFAULT_MULTI
    trip = float(event.get("pack_kg") or PACK_DEFAULT_MULTI)
    pct = PACK_STEPS[0] if weeks_out >= 9 else PACK_STEPS[1] if weeks_out >= 7 else PACK_STEPS[2]
    kg = min(trip, weight * pct) if weight else None
    return {"pct": pct, "kg": None if kg is None else round(kg, 1), "max": trip, "weeks_out": weeks_out}


# ---------------------------------------------------------------------------
# week_plan / projection glue
# ---------------------------------------------------------------------------

def plan_context(ds, status, today: dt.date, monday: dt.date, hist_hours: list[float], ctl_s, atl_s,
                 d_prev_sun: int, by: dict, mode: str) -> dict:
    """week_context() from week_plan()'s data: the next A event, the B2B
    weekends done in this 專項期, last week's hours (3:1 position), TSB at the
    start of the week, the status ramp and the gate's guardrails. Never
    raises: a failure is just "no B2B"."""
    try:
        from backend.engine import overview as O
        ev = event_json(target_event(status.plan.events, today))
        phase = getattr(status, "phase", None)
        since = _d(phase.start) if phase is not None and phase.kind == "specific" else monday - dt.timedelta(weeks=12)
        rows = [(O.wdate(w), O.moving_s(w) / 60.0, w.idx)
                for w in O.workouts_between(ds, min(since, monday - dt.timedelta(days=28)), monday)
                if O.category(w) in O.ENDURANCE]
        done = [b for b in detect(rows) if _d(b["start"]) >= since]
        state = history(done, since, monday)
        lo = monday - dt.timedelta(days=28)
        longest = max((m for d, m, _ in rows if d >= lo), default=0.0)
        tsb = O._n(ctl_s.at(d_prev_sun) - atl_s.at(d_prev_sun))
        ramp = ((getattr(by.get("fitness"), "extra", None) or {}).get("ramp_week")) if by else None
        guard = ((getattr(by.get("gate"), "extra", None) or {}).get("guard") or {}) if by else {}
        info = week_context(kind=status.kind or "base", mode=mode, monday=monday, event=ev,
                            last_recovery=last_was_recovery(hist_hours), state=state, tsb=tsb, ramp=ramp,
                            guard_ok=not guard.get("block"), post_from=done[-1] if done else None)
        info.update(longest_before=longest, ramp=ramp, tsb_week_start=tsb,
                    weight=status.plan.weight_on(today) if getattr(status, "plan", None) else None)
        return info
    except Exception as e:                  # noqa: BLE001 — the plan must still build
        return {"due": False, "candidate": False, "post": None, "error": type(e).__name__}


def placed(info: dict, kept: list[dict]) -> None:
    """After place(): no day 2 left = no B2B this week; a 3-day block that
    fell back to 2 days counts as 2 (the 3-day stays to do)."""
    n = sum(1 for s in kept if s.get("id") in FOLLOWERS)
    if info.get("due") and not n:
        info["due"] = False
        info.setdefault("blocked", []).append("本週沒有連續的日子可以排")
    elif info.get("due"):
        info["days"] = 1 + n
        days = sorted((s for s in kept if s.get("id") in ("long",) + FOLLOWERS), key=lambda s: s["id"])
        info["minutes"] = [s["minutes"] for s in days]
        days[0]["detail"] = (days[0].get("detail") or "").replace("（共 3 天）", f"（共 {1 + n} 天）")


PUBLIC = ("event", "candidate", "due", "days", "index", "why", "blocked", "last", "three_done", "count",
          "post", "minutes", "src", "error", "weight", "weeks_out")


def public(info: Optional[dict]) -> Optional[dict]:
    """The JSON part of the week's B2B state (week_plan()["b2b"])."""
    if not info:
        return None
    return {k: info.get(k) for k in PUBLIC if k in info}


def next_state(info: Optional[dict], monday: dt.date, ss: Optional[list] = None) -> dict:
    """The block state carried into the next week (projection). `ss`: the
    week's sessions — the B2B's real days (else Sat–Sun / Fri–Sun)."""
    info = info or {}
    st = {"last": info.get("last"), "three_done": bool(info.get("three_done")), "count": int(info.get("count") or 0)}
    if info.get("due"):
        n = int(info.get("days") or 2)
        days = sorted(s["day"] for s in ss or [] if s.get("id") in ("long",) + FOLLOWERS and s.get("day"))
        end = _d(days[-1]) if days else monday + dt.timedelta(days=6)
        start = _d(days[0]) if days else end - dt.timedelta(days=n - 1)
        st = {"last": end.isoformat(), "three_done": st["three_done"] or n >= 3, "count": st["count"] + 1,
              "post_from": {"start": start.isoformat(), "end": end.isoformat()}}
    return st


def projected(kind: str, mode: str, monday: dt.date, event: Optional[dict], prev_mode: Optional[str],
              state: dict, long_min: float, longest: float, total_min: float) -> dict:
    """week_context() + finalize() for a projected week (TSB / ramp re-checked
    when the week comes). `state`: next_state() of the week before."""
    info = week_context(kind=kind, mode=mode, monday=monday, event=event, last_recovery=prev_mode == "recovery_week",
                        state=state, post_from=state.get("post_from"))
    return finalize(info, long_min, longest, total_min)


# ---------------------------------------------------------------------------
# the TSB exception
# ---------------------------------------------------------------------------

def tsb_exempt(info: Optional[dict], tsb: Optional[float], ramp: Optional[float] = None) -> Optional[str]:
    """Why a low TSB this week is the planned B2B's expected drop (or None).
    A CTL ramp ≥ 8 is beyond the expected drop: no exception."""
    if not info or tsb is None or tsb >= TSB_MIN:
        return None
    if ramp is not None and ramp >= RAMP_MAX:
        return None
    if info.get("due"):
        return f"TSB {tsb:+.0f}：本週是計畫中的 B2B，TSB 下降是預期的，不改成恢復週、量不砍（推估）"
    if info.get("post"):
        p = info["post"]
        return (f"TSB {tsb:+.0f}：上週末 B2B（{md(p['start'])}–{md(p['end'])}）造成的預期下降，"
                f"只把之後 {POST_EASY_DAYS} 天排輕鬆、不改成恢復週，3:1 照常（推估）")
    return None


def fatigue_exempt(info: Optional[dict], today: str) -> Optional[str]:
    """adapt rule E: the TSB < −30 trigger alone is skipped during a planned
    B2B week and the easy days after one (the red streak and the ramp still act)."""
    if not info:
        return None
    if info.get("due"):
        return "本週是計畫中的 B2B"
    p = info.get("post")
    if p and today <= p["until"]:
        return f"上週末 B2B（{md(p['start'])}–{md(p['end'])}）後的輕鬆日"
    return None


def post_note(info: dict) -> Optional[dict]:
    p = info.get("post")
    if not p:
        return None
    return {"level": "info", "src": "b2b",
            "text": f"上週末 B2B（{md(p['start'])}–{md(p['end'])}）：到 {md(p['until'])} 只排輕鬆跑和肌力、不排間歇和測試；"
                    f"TSB 下降是預期的，不改成恢復週，3:1 照常（{SRC_POST}）"}


# ---------------------------------------------------------------------------
# the sessions
# ---------------------------------------------------------------------------

def followers(long_s: dict, info: dict) -> list[dict]:
    """long2 (and long3) for the template, minutes from finalize(). The long
    session's own minutes are set to day 1's. Text: decorate()."""
    mins = info["minutes"]
    rate = _rate(long_s)
    long_s["minutes"] = mins[0]
    long_s["tss"] = round(rate * mins[0], 1)
    return [{"id": f"long{i}", "kind": "long", "title": f"B2B 第 {i} 天", "minutes": m, "target": "",
             "detail": "", "source": SRC_DAYS, "tss": round(rate * m, 1), "day": None, "done": False,
             "done_by": None, "terrain": long_s.get("terrain")}
            for i, m in enumerate(mins[1:], 2)]


def _rate(s: dict) -> float:
    m = s.get("minutes") or 0
    return float(s.get("tss") or 0.0) / m if m else 0.0


def decorate(ss: list[dict], info: dict, aet: Optional[float], long_cap: Optional[int] = None,
             weight: Optional[float] = None) -> None:
    """Titles, details (Chinese, with sources) and HR-only targets of the B2B
    days, after the 課表偏好 shaping (which may rename / re-kind the long run or
    cap it): day 2 / 3 follow day 1's kind and terrain, ≤ the long-day cap and
    ≤ 2/3 of day 1 (or the single-day clamp)."""
    long_s = next((s for s in ss if s.get("id") == "long"), None)
    fol = [s for s in ss if s.get("id") in FOLLOWERS]
    if long_s is None or not fol:
        return
    n = 1 + len(fol)
    ev = info.get("event") or {}
    aet_t = f" {aet:.0f} bpm" if aet else ""
    hr_t = f"心率 ≤ AeT{aet_t}" if aet else "心率 ≤ AeT"
    rate = _rate(long_s)
    single = int(ev.get("days") or 1) <= 1
    for s in fol:
        lim = DAY2_RATIO * long_s["minutes"]
        if single:
            lim = min(max(lim, SINGLE_DAY2[0]), SINGLE_DAY2[1], long_s["minutes"])
        m = min(s["minutes"], _r5(lim))
        if long_cap is not None:
            m = min(m, long_cap)
        s["minutes"] = int(m)
        s["kind"] = long_s["kind"]
        s["terrain"] = long_s.get("terrain")
        s["tss"] = round(rate * s["minutes"], 1)
        s["target"] = hr_t
    pk = pack_kg(int(info.get("weeks_out") or 6), weight, ev)
    pack = ""
    if pk:
        what = f"{pk['kg']:g} kg（體重的 {pk['pct'] * 100:.0f}%）" if pk["kg"] is not None else f"體重的 {pk['pct'] * 100:.0f}%"
        pack = (f"百岳背包：賽前第 {pk['weeks_out']} 週，第 1 天背 {what}；"
                f"UA 健行訓練 5 → 10 → 15%、每階段約 2 週，不超過行程背包 {pk['max']:g} kg"
                f"（對應週數依 loaded-carry-training.md，推估）。")
    fuel = "練比賽補給：每小時 30–60 g 醣，超過 2.5 小時可到 90 g/h；當晚要吃回來，第 2 天才是在練「接續的一天」（Burke 2011）。"
    if long_s["minutes"] < FUEL_MIN_H * 60:
        fuel += f"Koop：練補給的長跑至少 {FUEL_MIN_H:.0f} 小時，本週先練到 {long_s['minutes']} 分。"
    base_title = long_s["title"].split("｜", 1)[-1]
    # the generator's / 課表偏好's terrain hint (e.g. 「挑每公里爬升 ≥ 56 m 的路線」) stays a suggestion
    old = long_s.get("detail") or ""
    terrain = "" if old.startswith("B2B") else old.split("；", 1)[0].strip()
    climb = f"建議挑爬升比較多的路線（{terrain}；建議，不強制）" if terrain else "建議挑爬升比較多的路線（建議，不強制）"
    long_s["title"] = f"B2B 第 1 天｜{base_title}"
    long_s["target"] = hr_t
    long_s["detail"] = (f"B2B 第 1 天（共 {n} 天）：這週最長的一天，{climb}；全程{hr_t}，爬坡用走的守住上限，"
                        f"不放間歇。{fuel}{pack}").rstrip()
    long_s["source"] = SRC_DAYS
    for s in fol:
        i = int(s["id"][-1])
        share = "約第 1 天的 2/3（CTS 30:20）" if not single else "1.5–2.5 小時輕鬆（推估）"
        s["title"] = f"B2B 第 {i} 天｜{base_title}"
        s["detail"] = (f"B2B 第 {i} 天：{share}，{s['minutes']} 分；{hr_t}，不加速；建議下坡多一點（不強制），練多日下坡"
                       f"（Bontemps 2025 重複負荷效應）。" + ("背包和第 1 天一樣或更輕。" if pk else "")
                       + "跟第 1 天比同樣爬坡速度的心率、同樣心率的爬坡速度，總覽的 B2B 卡會判讀。")
        s["source"] = SRC_DAYS


def done_follow(ss: list, s, day: dt.date) -> bool:
    """Mark-done helper: a long2 / long3 activity must be the day after the
    previous B2B day when that one is done (objects or dicts)."""
    get = (lambda x, k: x.get(k)) if isinstance(s, dict) else getattr
    sid = get(s, "id")
    prev_id = "long" if sid == "long2" else "long2"
    prev = next((x for x in ss if get(x, "id") == prev_id), None)
    if prev is None or not get(prev, "done") or not get(prev, "day"):
        return True
    return (day - _d(get(prev, "day"))).days == 1


def place(ss: list[dict], monday: dt.date, first: Optional[dt.date], blocked=frozenset(),
          allowed: Optional[Callable] = None, notes: Optional[list] = None,
          weekday_cap: Optional[int] = None) -> list[dict]:
    """Put the B2B days on consecutive days around the placed long run:
    (L, L+1[, L+2]) or (L−1, L[, L+1]) — inside the week, from `first`, not
    blocked, allowed by 課表偏好, no done or hard session on them, and a
    weekday only when its B2B minutes fit 課表偏好's weekday cap (weekends:
    the long-day cap, applied by decorate()). Easy runs on those days swap to
    the days the B2B left; a quality session closer than 2 days moves or is
    dropped (48 h rule). A 3-day block that doesn't fit falls back to 2 days
    with a note (「請一天假」— never squeezed into a capped weekday, doc §3.2);
    no 2 days → day 2 dropped (the long run stays: an ordinary long day).
    Returns the kept sessions (a new list; the dicts are edited in place)."""
    long_s = next((s for s in ss if s.get("id") == "long" and s.get("day")), None)
    fol = sorted([s for s in ss if s.get("id") in FOLLOWERS], key=lambda s: s["id"])
    if not fol:
        return ss
    gone: list[dict] = []

    def note(text: str) -> None:
        if notes is not None:
            notes.append({"level": "info", "src": "b2b", "text": text})

    if long_s is None:
        note("B2B 第 1 天本週排不進去：第 2 天也取消（推估）")
        return [s for s in ss if s not in fol or s.get("done")]
    if not any(not s.get("done") for s in fol):
        return ss
    week = [monday + dt.timedelta(days=i) for i in range(7)]
    L = _d(long_s["day"])
    block_ids = {id(long_s)} | {id(s) for s in fol}
    others = [s for s in ss if id(s) not in block_ids]

    def day_ok(d: dt.date, s: dict) -> bool:
        if d not in week or d.isoformat() in blocked or (allowed is not None and not allowed(d)):
            return False
        if first is not None and d < first:
            return False
        if weekday_cap is not None and d.weekday() < 5 and s["minutes"] > weekday_cap:
            return False
        for x in others:
            if x.get("day") == d.isoformat() and (x.get("done") or x.get("kind") in ("quality", "test", "race")):
                return False
        return True

    def find(fol_n: list[dict]) -> Optional[list[dt.date]]:
        n = 1 + len(fol_n)
        seq = [long_s] + fol_n
        cands = [[L + dt.timedelta(days=i - k) for i in range(n)] for k in range(n)]
        for c in cands:
            if long_s.get("done") and c[0] != L:
                continue
            if all((s.get("done") and s.get("day") == d.isoformat()) or (not s.get("done") and day_ok(d, s))
                   for s, d in zip(seq, c)):
                return c
        return None

    block = find(fol)
    if block is None and len(fol) == 2 and not fol[1].get("done"):
        block = find(fol[:1])
        if block is not None:
            gone.append(fol[1])
            cap_t = f"（平日上限 {weekday_cap} 分）" if weekday_cap is not None else ""
            note(f"3 天版本要連續 3 天，這週排不下{cap_t}：先排 2 天；要做 3 天就請一天假（週五或週一），"
                 f"在課表偏好打開那天或放寬上限（CTS 4–6 週前的 3 天區塊）")
            fol = fol[:1]
    if block is None:
        n = 1 + len(fol)
        note(f"本週沒有連續 {n} 天可以練：B2B 第 2{'、3' if n == 3 else ''} 天取消，這週照一般長天（推估）")
        return [s for s in ss if s not in fol or s.get("done")]
    pending = [s for s in fol if not s.get("done")]
    old = {long_s["day"]} | {s["day"] for s in pending + gone if s.get("day")}
    if not long_s.get("done"):
        long_s["day"] = block[0].isoformat()
    for s, d in zip(fol, block[1:]):
        if not s.get("done"):
            s["day"] = d.isoformat()
    taken = {d.isoformat() for d in block}
    vacated = sorted(x for x in old if x not in taken)
    # easy runs / strength on the B2B days go to the days the B2B left (else a free allowed day)
    main_days = {x["day"] for x in others if x.get("day") and x.get("kind") not in ("strength", "heat_passive", "notice")}
    free = [d.isoformat() for d in week if d.isoformat() not in taken and d.isoformat() not in main_days
            and d.isoformat() not in blocked and (allowed is None or allowed(d)) and (first is None or d >= first)]
    spare = vacated + [d for d in free if d not in vacated]
    kept = [s for s in ss if not any(s is g for g in gone)]
    for x in others:
        if x.get("done") or x.get("day") not in taken:
            continue
        if x.get("kind") == "strength":
            continue                    # strength after the run is fine (UA: on easy days or after a run)
        if x.get("kind") in ("easy", "heat_passive", "notice"):
            nxt = next((d for d in spare if d not in {y.get("day") for y in kept if y is not x
                                                       and y.get("kind") not in ("strength", "heat_passive", "notice")}),
                       None)
            if nxt is None:
                kept.remove(x)
                if notes is not None:
                    notes.append({"level": "info", "src": "b2b", "text": f"B2B 佔了週末：{wd(x['day'])}的輕鬆跑排不下，不用補"})
            else:
                x["day"] = nxt
    # 48 h between hard days and the B2B days
    for q in [x for x in kept if x.get("kind") in ("quality", "test") and not x.get("done") and x.get("day")]:
        if all(abs((_d(q["day"]) - d).days) >= 2 for d in block):
            continue
        busy = {y.get("day") for y in kept if y is not q and y.get("kind") not in ("strength", "heat_passive", "notice")}
        hard = [_d(y["day"]) for y in kept if y is not q and y.get("kind") in ("quality", "test") and y.get("day")]
        ok = [d for d in week if d.isoformat() not in busy and d.isoformat() not in blocked
              and (allowed is None or allowed(d)) and (first is None or d >= first)
              and all(abs((d - b).days) >= 2 for b in block + hard)]
        if ok:
            q["day"] = ok[0].isoformat()
        else:
            kept.remove(q)
            if notes is not None:
                notes.append({"level": "info", "src": "b2b",
                              "text": f"{q['title']}離 B2B 不到 48 小時、本週沒有別的空日：這週先不排"})
    return kept


# ---------------------------------------------------------------------------
# evaluation: day 2 vs day 1 (no RPE)
# ---------------------------------------------------------------------------

def climb_windows(rows: Iterable[dict]) -> list[dict]:
    """Climbing 100 m windows of one day ({"k", "g", "v", "hr", "z"?}):
    grade ≥ 10 %, moving > 1.5 km/h, HR present, in runs of ≥ 3 consecutive
    windows (hikehr.HIKE_FILTER without its HR ≥ AeT floor — the B2B days are
    ≤ AeT by design); VAM ≤ the VK world-record rate."""
    from backend.engine.racepower import hikehr as HH
    f = HH.HIKE_FILTER
    ok = sorted([r for r in rows if r.get("hr") and r.get("v") is not None and r["v"] * 3.6 > f["min_kmh"]
                 and r.get("g") is not None and r["g"] >= f["min_grade"]], key=lambda r: r.get("k", 0))
    out, run = [], []
    for r in ok + [None]:
        if run and (r is None or r.get("k") != run[-1].get("k", -10) + 1):
            if len(run) >= f["min_run"]:
                out.extend(run)
            run = []
        if r is not None:
            run.append(r)
    wins = [dict(r, vam=r["v"] * r["g"] * 3600.0) for r in out]
    return [w for w in wins if w["vam"] <= HH.VAM_MAX]


def day_stats(rows: list[dict], aet: Optional[float]) -> dict:
    """§1.3 ①: share of the moving time with HR > AeT + 3 (time = 100 m / v),
    the climbing (sum of positive window elevation steps) and the minutes."""
    from backend.engine import workout_review as WR
    rows = [r for r in rows if r.get("v") and r["v"] > 0]
    t = [100.0 / r["v"] for r in rows]
    tot = sum(t)
    hr_t = sum(ti for ti, r in zip(t, rows) if r.get("hr"))
    over = sum(ti for ti, r in zip(t, rows) if aet and r.get("hr") and r["hr"] > aet + WR.AET_MARGIN)
    zs = [r["z"] for r in sorted(rows, key=lambda r: r.get("k", 0)) if r.get("z") is not None]
    climb = sum(max(0.0, b - a) for a, b in zip(zs, zs[1:]))
    return {"moving_min": round(tot / 60.0), "climb_m": round(climb), "over_aet_share": (over / hr_t) if hr_t and aet else None,
            "over_ok": None if not (hr_t and aet) else over / hr_t <= WR.OVER_AET_SHARE}


CELLS = {
    "durable": ("耐久性好", "good", "第 2 天同樣的爬坡速度心率差不多、同樣心率的爬坡速度也沒掉"),
    "muscular": ("肌肉疲勞", "watch", "心率被壓住、腿出不了力（UA；Le Meur 2013、Kerhervé 2015 都看到心率下降）→ 下一次第 2 天縮短"),
    "cardio": ("心血管漂移或補給不足", "watch", "同樣的速度要更高的心率（Coyle 2001）→ 檢查第 1 天和當晚的補給、熱、睡眠"),
    "uncommon": ("不常見：第 2 天心率較低、速度沒掉", "info", "可能是第 1 天熱或脫水 → 先看第 1 天的 Hadley"),
    "insufficient": ("爬坡段不夠，不判讀", "na", "第 1 天和第 2 天都要有 ≥ 5 段 100 m 的爬坡（坡度 ≥ 10%）"),
}


def _vam_at(wins: list[dict], hr: float) -> Optional[float]:
    """VAM at `hr` from a least-squares VAM ~ HR line; None when HR doesn't
    vary (one speed) or the line runs the wrong way."""
    import numpy as np
    x = np.array([w["hr"] for w in wins], float)
    y = np.array([w["vam"] for w in wins], float)
    if len(x) < 2 or np.ptp(x) < 2.0:
        return None
    b, a = np.polyfit(x, y, 1)
    v = a + b * hr
    return float(v) if b > 0 and v > 0 else None


def classify(hr_shift: Optional[float], vam_ratio: Optional[float]) -> str:
    if hr_shift is None or vam_ratio is None:
        return "insufficient"
    low_hr = hr_shift <= -HR_SAME_BPM
    slow = vam_ratio < 1.0 - VAM_SAME
    if slow:
        return "muscular" if low_hr else "cardio"
    return "uncommon" if low_hr else "durable"


def evaluate(day1: list[dict], day2: list[dict], aet: Optional[float]) -> dict:
    """The 2 × 2 reading of day 2 against day 1 from their 100 m windows."""
    from backend.engine.racepower import hikehr as HH
    w1, w2 = climb_windows(day1), climb_windows(day2)
    fat = HH.fatigue([{**w, "trip": 0, "day": 1} for w in w1] + [{**w, "trip": 0, "day": 2} for w in w2])
    hr_shift = fat["days"][0]["hr_shift_bpm"] if fat["days"] else None
    vam_ratio, method = None, None
    b1 = b2 = []
    if aet:
        b1 = [w for w in w1 if aet - AET_BAND_BPM <= w["hr"] <= aet]
        b2 = [w for w in w2 if aet - AET_BAND_BPM <= w["hr"] <= aet]
        if len(b1) >= HH.FAT_MIN_N and len(b2) >= HH.FAT_MIN_N:
            # VAM at the band's middle HR from each day's own HR ~ VAM line over the band
            # windows (a band median alone follows how the windows spread inside the band:
            # a +6 bpm day 2 puts its faster climbs in the band) — interpolation 推估
            ref = aet - AET_BAND_BPM / 2.0
            v1, v2 = _vam_at(b1, ref), _vam_at(b2, ref)
            if v1 and v2:
                vam_ratio, method = v2 / v1, "line"
            else:
                m1, m2 = median(w["vam"] for w in b1), median(w["vam"] for w in b2)
                if m1 > 0:
                    vam_ratio, method = float(m2 / m1), "median"
    cell = classify(hr_shift, vam_ratio)
    label, level, text = CELLS[cell]
    return {"cell": cell, "label": label, "level": level, "text": text,
            "hr_shift_bpm": None if hr_shift is None else round(hr_shift, 1),
            "vam_ratio": None if vam_ratio is None else round(vam_ratio, 3), "vam_method": method,
            "n": {"day1": len(w1), "day2": len(w2), "aet_band_day1": len(b1), "aet_band_day2": len(b2)},
            "day1": day_stats(day1, aet), "day2": day_stats(day2, aet),
            "thresholds": {"hr_bpm": HR_SAME_BPM, "vam": VAM_SAME, "label": "推估"}, "src": SRC_EVAL}


def trend(evals: list[dict]) -> dict:
    """Across the block's B2B weekends: is day 2's VAM drop getting smaller,
    and is 耐久性好 getting more common (Maunder 2021: onset and magnitude)."""
    pts = [e for e in evals if e.get("vam_ratio") is not None]
    judged = [e for e in evals if e.get("cell") != "insufficient"]
    out = {"n": len(evals), "judged": len(judged),
           "durable_share": (sum(e["cell"] == "durable" for e in judged) / len(judged)) if judged else None,
           "first": pts[0]["vam_ratio"] if pts else None, "last": pts[-1]["vam_ratio"] if pts else None,
           "direction": None, "text": "", "label": "推估"}
    if len(pts) < 2:
        out["text"] = "還要至少兩次有判讀的 B2B 才看得出趨勢"
        return out
    ch = pts[-1]["vam_ratio"] - pts[0]["vam_ratio"]
    out["change"] = round(ch, 3)
    out["direction"] = "better" if ch >= TREND_STEP else "worse" if ch <= -TREND_STEP else "flat"
    out["text"] = {"better": "第 2 天的爬坡速度掉得比較少了：耐久性在進步",
                   "worse": "第 2 天掉得比上次多：看睡眠、補給，下一次第 2 天縮短",
                   "flat": "第 2 天的衰退差不多"}[out["direction"]] + f"（{pts[0]['vam_ratio']:.0%} → {pts[-1]['vam_ratio']:.0%}）"
    return out


def _windows(ds, w) -> list[dict]:
    """100 m windows of one activity (racepower.athlete hike rows: works for
    runs too), every calendar day of it together."""
    from backend.engine.racepower import athlete as A
    if hasattr(ds, "cached_series"):
        rows = ds.cached_series(A.HIKE_KEY, w, lambda: A._hike_windows(ds, w))
    else:
        rows = A._hike_windows(ds, w)
    return [{"g": g, "v": v, "z": z, "hr": hr, "k": k} for g, v, z, hr, k, _day, _t, _lag in rows or []]


def evaluate_pair(ds, w1, w2, aet: Optional[float]) -> dict:
    return evaluate(_windows(ds, w1), _windows(ds, w2), aet)


def card(ds, today: dt.date, events, phase=None, cur: Optional[dict] = None, weeks: Optional[list] = None,
         aet_of: Optional[Callable] = None) -> dict:
    """The overview's B2B card: the event, this week / the planned weekends,
    every done B2B in the block with its 2 × 2 reading, and the trend.
    `phase`: the current planning.Phase; `cur` / `weeks`: week_plan() and the
    projection (their "b2b" entries); `aet_of(date)`: AeT on a day."""
    from backend.engine import overview as O
    ev = target_event(events, today)
    since = _d(phase.start) if phase is not None and phase.kind == "specific" else today - dt.timedelta(days=120)
    rows = [(O.wdate(w), O.moving_s(w) / 60.0, w.idx)
            for w in O.workouts_between(ds, since, today + dt.timedelta(days=1)) if O.category(w) in O.ENDURANCE]
    by_idx = {w.idx: w for w in ds.workouts}
    done = []
    for b in detect(rows):
        w1, w2 = by_idx.get(b["idx"][0]), by_idx.get(b["idx"][1])
        aet = aet_of(_d(b["start"])) if aet_of else None
        try:
            r = evaluate_pair(ds, w1, w2, aet) if w1 is not None and w2 is not None else None
        except Exception as e:             # noqa: BLE001 — one broken file never breaks the card
            r = {"cell": "insufficient", **dict(zip(("label", "level", "text"), CELLS["insufficient"])),
                 "error": type(e).__name__}
        done.append({**b, "aet": aet, "eval": r})
    planned = []
    cb = (cur or {}).get("b2b") or {}
    if cb.get("due"):
        planned.append({"week": (cur or {}).get("week", {}).get("start"), "days": cb.get("days"),
                        "minutes": cb.get("minutes"), "this_week": True,
                        "sessions": [{"id": s["id"], "day": s.get("day"), "minutes": s["minutes"], "done": s.get("done")}
                                     for s in (cur or {}).get("sessions") or [] if s.get("id") in ("long",) + FOLLOWERS]})
    for w in weeks or []:
        b = w.get("b2b") or {}
        if b.get("due"):
            planned.append({"week": w["start"], "days": b.get("days"), "minutes": b.get("minutes"), "this_week": False,
                            "sessions": [{"id": s["id"], "day": s.get("day"), "minutes": s["minutes"]}
                                         for s in w.get("sessions") or [] if s.get("id") in ("long",) + FOLLOWERS]})
    evs = [d["eval"] for d in done if d.get("eval")]
    return {"today": today.isoformat(), "event": event_json(ev), "active": qualifies(event_json(ev)) or bool(done),
            "this_week": {k: cb.get(k) for k in ("due", "days", "index", "why", "blocked", "post", "minutes")} if cb else None,
            "planned": planned, "done": done, "trend": trend(evs),
            "rules": {"when": SRC_WHEN, "days": SRC_DAYS, "post": SRC_POST, "eval": SRC_EVAL},
            "cells": {k: {"label": v[0], "level": v[1], "text": v[2]} for k, v in CELLS.items()}}
